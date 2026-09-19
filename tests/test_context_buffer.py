"""
上下文缓冲与冷却机制测试。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context_buffer import ContextBuffer, ContextMessage


class TestContextBuffer:

    def test_add_and_retrieve(self):
        buf = ContextBuffer()
        buf.add("麦克风语音", "看我这把带飞")
        text = buf.get_prompt_context()
        assert "看我这把带飞" in text

    def test_max_messages_enforced(self):
        buf = ContextBuffer(max_messages=3)
        for i in range(10):
            buf.add("麦克风语音", f"消息{i}")
        text = buf.get_prompt_context()
        assert "消息9" in text
        assert "消息0" not in text, "超出窗口的旧消息应被丢弃"

    def test_window_prunes_old_messages(self):
        buf = ContextBuffer(window_seconds=1, max_messages=100)
        buf.add("麦克风语音", "旧消息")
        time.sleep(1.2)
        buf.add("麦克风语音", "新消息")
        text = buf.get_prompt_context()
        assert "新消息" in text
        assert "旧消息" not in text, "超时消息应被裁剪"

    def test_empty_context_message(self):
        buf = ContextBuffer()
        assert buf.get_prompt_context()  # 不应为空串或抛异常

    def test_clear(self):
        buf = ContextBuffer()
        buf.add("麦克风语音", "测试")
        buf.clear()
        assert "测试" not in buf.get_prompt_context()


class TestCooldown:
    """防连播冷却：避免音效刷屏"""

    def test_initially_not_in_cooldown(self):
        buf = ContextBuffer(cooldown_seconds=5)
        in_cd, remaining = buf.is_in_cooldown()
        assert not in_cd
        assert remaining == 0.0

    def test_in_cooldown_after_play(self):
        buf = ContextBuffer(cooldown_seconds=5)
        buf.mark_played("测试音效")
        in_cd, remaining = buf.is_in_cooldown()
        assert in_cd
        assert 0 < remaining <= 5

    def test_cooldown_expires(self):
        buf = ContextBuffer(cooldown_seconds=0.3)
        buf.mark_played("测试音效")
        time.sleep(0.4)
        in_cd, _ = buf.is_in_cooldown()
        assert not in_cd

    def test_mark_played_records_to_context(self):
        """播放后应写入上下文，避免同一事件重复触发"""
        buf = ContextBuffer()
        buf.mark_played("不可能绝对不可能")
        assert "不可能绝对不可能" in buf.get_prompt_context()


class TestMessageFormatting:

    def test_relative_time_formatting(self):
        msg = ContextMessage("麦克风", "测试")
        formatted = msg.format_relative(time.time())
        assert "刚刚" in formatted
        assert "麦克风" in formatted

    def test_emotion_tag_included(self):
        msg = ContextMessage("麦克风", "我破防了", emotion="红温/愤怒")
        formatted = msg.format_relative(time.time())
        assert "红温/愤怒" in formatted


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
