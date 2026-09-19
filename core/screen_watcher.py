import time
import re
import base64
import threading
from typing import Callable, Optional, List, Tuple
import numpy as np

from core.http_client import get_logger

logger = get_logger("screen")

try:
    import mss
    import cv2
    from rapidocr_onnxruntime import RapidOCR
    _SCREEN_DEPS_OK = True
except ImportError:
    _SCREEN_DEPS_OK = False

def capture_screen_base64(target_size=(512, 512), quality=65) -> Optional[str]:
    """
    毫秒级抓取当前主屏幕实机画面并压缩为 JPEG Base64 供 VLM 多模态大模型理解。

    target_size 直接决定图片 Token 开销：
      384x384 -> 多数接口按最小档计费（约 258 tokens）
      512x512 -> 约 400~500 tokens
      768x768 -> 约 1000+ tokens
    """
    if not _SCREEN_DEPS_OK:
        return None
    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            raw = np.array(sct.grab(monitor))
            bgr = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
            small = cv2.resize(bgr, target_size, interpolation=cv2.INTER_AREA)
            _, buf = cv2.imencode('.jpg', small, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            return base64.b64encode(buf).decode('utf-8')
    except Exception as e:
        logger.error("截图压缩异常: %s", e)
        return None


def capture_screen_for_vision(vision_cfg: dict) -> Optional[str]:
    """按视觉配置抓取画面，分辨率与压缩质量均可调"""
    size = int(vision_cfg.get("image_size", 512))
    quality = int(vision_cfg.get("image_quality", 65))
    return capture_screen_base64(target_size=(size, size), quality=quality)

# 常见游戏高光与翻车关键词库 (支持绝地潜兵、瓦罗兰特、CS、黑神话、魂系、单机联机全覆盖)
GAME_KEYWORDS_MAP = [
    (
        "玩家阵亡/任务失败",
        [
            "阵亡", "死亡", "you died", "defeat", "战败", "失败", "饮恨",
            "failed", "eliminated", "复活中", "全军覆没", "任务失败", "重整队伍"
        ]
    ),
    (
        "游戏胜利/撤离成功",
        [
            "胜利", "victory", "champion", "大获全胜", "撤离成功", "extracted",
            "胜", "mission accomplished", "任务完成", "成功撤离"
        ]
    ),
    (
        "团灭/连续击杀",
        [
            "团灭", "ace", "pentakill", "quadrakill", "triple kill",
            "大杀特杀", "主宰比赛", "超神"
        ]
    )
]

class ScreenWatcher:
    """
    全域屏幕感知引擎：
    1. RapidOCR 离线字样检测 (0 显存 0 延迟，专抓结算大字)
    2. VLM 自动低频抽帧巡检 (动静画面门控 + 定时送检多模态大模型)
    """
    def __init__(
        self,
        on_event_detected: Callable[[str], None],
        on_vlm_frame: Optional[Callable[[str], None]] = None,
        check_interval: float = 1.0
    ):
        self.on_event_detected = on_event_detected
        self.on_vlm_frame = on_vlm_frame
        self.check_interval = check_interval
        
        self.is_running = False
        self.is_ocr_enabled = False       # 离线文字检测开关
        self.is_vlm_auto_enabled = False  # VLM 自动定时抽帧开关
        self.vlm_sample_interval = 6.0    # 自动抽帧间隔 (秒)
        self.image_size = 512             # 抽帧分辨率（决定图片 Token 开销）
        self.image_quality = 65

        self.thread_ocr: Optional[threading.Thread] = None
        self.thread_vlm: Optional[threading.Thread] = None
        self.ocr = None
        self.last_ocr_time = 0.0
        self.last_vlm_time = 0.0
        # 用于可被立即唤醒的休眠：stop() 时 set()，采样线程无需等满整个间隔
        self._stop_event = threading.Event()

        if _SCREEN_DEPS_OK:
            try:
                self.ocr = RapidOCR()
            except Exception as e:
                logger.error("初始化 RapidOCR 失败: %s", e)

    def _sleep(self, seconds: float):
        """可被 stop() 立即打断的休眠，避免长间隔导致线程迟迟不退出"""
        self._stop_event.wait(timeout=seconds)
        return not self.is_running

    def _ocr_monitor_loop(self):
        """RapidOCR 快速文字扫描循环"""
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            w = monitor["width"]
            h = monitor["height"]
            
            roi = {
                "top": int(h * 0.30),
                "left": int(w * 0.25),
                "width": int(w * 0.50),
                "height": int(h * 0.35)
            }

            while self.is_running:
                if not self.is_ocr_enabled or self.ocr is None:
                    if self._sleep(1.0):
                        break
                    continue

                now = time.time()
                if now - self.last_ocr_time < 12.0:
                    if self._sleep(self.check_interval):
                        break
                    continue

                try:
                    img_rgba = np.array(sct.grab(roi))
                    img_bgr = cv2.cvtColor(img_rgba, cv2.COLOR_BGRA2BGR)
                    result, _ = self.ocr(img_bgr)

                    if result:
                        full_detected_text = " ".join([line[1] for line in result]).lower()
                        matched_event = None
                        matched_key = ""
                        for event_name, keys in GAME_KEYWORDS_MAP:
                            for k in keys:
                                if k in full_detected_text:
                                    matched_event = event_name
                                    matched_key = k
                                    break
                            if matched_event:
                                break

                        if matched_event:
                            self.last_ocr_time = now
                            event_desc = f"屏幕检测到文字「{matched_key.upper()}」 (判定: {matched_event})"
                            self.on_event_detected(event_desc)

                except Exception:
                    pass

                if self._sleep(self.check_interval):
                    break

    def _vlm_sample_loop(self):
        """VLM 动态自动抽帧循环 (带画面动静门控，静止画面不消耗请求)"""
        last_thumb = None

        while self.is_running:
            if not self.is_vlm_auto_enabled or self.on_vlm_frame is None:
                if self._sleep(0.5):
                    break
                continue

            interval = max(0.05, float(self.vlm_sample_interval))
            # 轮询步长随间隔自适应：高频采样时用更细的粒度，保证 0.1s 档位能真正生效
            poll_step = min(0.2, max(0.02, interval / 2.0))
            static_step = min(0.5, max(0.05, interval))

            now = time.time()
            if now - self.last_vlm_time < interval:
                if self._sleep(poll_step):
                    break
                continue

            frame = None
            try:
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    raw = np.array(sct.grab(monitor))
                    bgr = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)

                    # 1. 动静检测门控：缩小到 32x32 灰度算画面变动
                    thumb = cv2.resize(bgr, (32, 32), interpolation=cv2.INTER_AREA)
                    gray = cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)

                    if last_thumb is not None:
                        diff = float(np.mean(np.abs(gray.astype(float) - last_thumb.astype(float))))
                        last_thumb = gray
                        # 画面静止（暂停 / 挂机 / 静态桌面）时不消耗请求
                        if diff < 3.0:
                            self.last_vlm_time = now
                            if self._sleep(static_step):
                                break
                            continue
                    else:
                        last_thumb = gray

                    # 2. 画面处于动态战斗中，按配置分辨率抓取 JPEG Base64
                    sz = int(self.image_size)
                    small = cv2.resize(bgr, (sz, sz), interpolation=cv2.INTER_AREA)
                    _, buf = cv2.imencode('.jpg', small, [int(cv2.IMWRITE_JPEG_QUALITY), int(self.image_quality)])
                    frame = base64.b64encode(buf).decode('utf-8')

            except Exception as e:
                logger.error("自动抽帧异常: %s", e)
                if self._sleep(static_step):
                    break
                continue

            if frame:
                self.last_vlm_time = now
                # 交由上层异步处理，避免网络请求阻塞采样节奏
                try:
                    self.on_vlm_frame(frame)
                except Exception as e:
                    logger.error("抽帧回调异常: %s", e)

            if self._sleep(poll_step):
                break

    def start(self):
        if not _SCREEN_DEPS_OK:
            return
        if self.is_running:
            return
        # 回收上一轮可能残留的线程，避免重复启动时线程叠加占用设备
        self._stop_event.clear()
        self._join_threads()
        self.is_running = True
        self.thread_ocr = threading.Thread(target=self._ocr_monitor_loop, daemon=True, name="ocr-monitor")
        self.thread_ocr.start()
        self.thread_vlm = threading.Thread(target=self._vlm_sample_loop, daemon=True, name="vlm-sample")
        self.thread_vlm.start()

    def stop(self):
        self.is_running = False
        # 立即唤醒正在休眠的采样线程，使其尽快退出
        self._stop_event.set()
        self._join_threads()

    def _join_threads(self, timeout: float = 3.0):
        """等待采样线程真正退出，避免重启后新旧线程并存抢占设备"""
        current = threading.current_thread()
        for t in (getattr(self, "thread_ocr", None), getattr(self, "thread_vlm", None)):
            if t is None or t is current or not t.is_alive():
                continue
            t.join(timeout=timeout)
            if t.is_alive():
                logger.warning("采样线程 %s 未能在 %.1fs 内退出", t.name, timeout)
