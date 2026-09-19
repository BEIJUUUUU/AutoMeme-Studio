"""
线程生命周期测试。

回归保护：stop() 必须真正终止监听线程并释放资源。
历史问题 —— 采样线程休眠最长 30 秒，stop() 只是置标志位不 join，
导致停止后线程仍存活、重启后新旧线程并存抢占音频/屏幕设备。
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.screen_watcher import ScreenWatcher


def _count_extra_threads():
    """当前存活的非主线程数量"""
    return max(0, threading.active_count() - 1)


class TestScreenWatcherThreads:
    """屏幕监视器的线程回收"""

    def test_stop_terminates_threads(self):
        """stop() 后两个采样线程都应退出"""
        base = _count_extra_threads()
        sw = ScreenWatcher(lambda x: None, lambda b: None)
        sw.start()
        time.sleep(0.2)
        assert _count_extra_threads() > base, "start() 应创建采样线程"

        sw.stop()
        time.sleep(0.2)
        assert _count_extra_threads() <= base, "stop() 后不应有残留线程"

    def test_stop_is_fast_even_with_long_interval(self):
        """即使抽帧间隔很长，stop() 也应立即返回（不等待整个间隔）"""
        sw = ScreenWatcher(lambda x: None, lambda b: None)
        sw.is_vlm_auto_enabled = True
        sw.vlm_sample_interval = 30.0   # 最坏情况：30 秒间隔
        sw.start()
        time.sleep(0.2)

        t0 = time.time()
        sw.stop()
        elapsed = time.time() - t0
        assert elapsed < 3.0, f"stop() 不应阻塞，实际耗时 {elapsed:.1f}s"

    def test_restart_does_not_accumulate_threads(self):
        """反复启停不应累积线程（否则会重复占用设备）"""
        base = _count_extra_threads()
        sw = ScreenWatcher(lambda x: None, lambda b: None)

        for _ in range(3):
            sw.start()
            time.sleep(0.15)
            sw.stop()
            time.sleep(0.15)

        assert _count_extra_threads() <= base, "反复启停后线程数应回到基线"

    def test_double_start_is_idempotent(self):
        """重复 start() 不应创建多余线程"""
        base = _count_extra_threads()
        sw = ScreenWatcher(lambda x: None, lambda b: None)
        sw.start()
        time.sleep(0.2)
        after_first = _count_extra_threads()

        sw.start()   # 第二次应被 is_running 挡住
        time.sleep(0.2)
        assert _count_extra_threads() <= after_first, "重复 start() 不应叠加线程"

        sw.stop()
        time.sleep(0.2)
        assert _count_extra_threads() <= base


class TestAudioListenerThreads:
    """音频监听器的线程回收（依赖缺失时自动跳过）"""

    def test_mic_stop_terminates_thread(self):
        try:
            from core.audio_listener import AudioListener
        except ImportError:
            return  # 依赖缺失，跳过

        base = _count_extra_threads()
        listener = AudioListener(on_speech_recognized=lambda t, e=None: None)
        if listener.engine_type == "none":
            return  # 无可用识别引擎，跳过

        listener.start()
        time.sleep(0.3)
        listener.stop()
        time.sleep(0.4)
        assert _count_extra_threads() <= base, "麦克风 stop() 后不应有残留线程"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
