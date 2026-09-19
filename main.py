"""
AutoMeme Studio 无界面（CLI / Headless）入口。

适合：
  - 服务器或无桌面环境
  - 只想跑核心逻辑、不启动 GUI
  - 调试与自动化测试

用法:
    python main.py              # 使用 config.json 启动监听
    python main.py --check      # 仅做配置与依赖自检，不启动

图形界面请使用: python gui.py
"""

import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path

CURRENT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(CURRENT_DIR))

from core.arbiter import MemeArbiter
from core.audio_listener import AudioListener
from core.context_buffer import ContextBuffer
from core.http_client import get_logger, setup_logging
from core.player import AudioPlayer
from core.screen_watcher import ScreenWatcher
from core.system_audio_listener import SystemAudioListener

logger = get_logger("cli")

CONFIG_PATH = CURRENT_DIR / "config.json"
MEME_LIB_PATH = CURRENT_DIR / "meme_library.json"


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class AutoMemeService:
    """无界面运行的服务主体"""

    def __init__(self, config: dict, memes: list):
        self.config = config
        self.memes = memes
        self._eval_lock = threading.Lock()
        self._running = False

        audio_cfg = config.get("audio", {})
        voice_dir = (CURRENT_DIR / audio_cfg.get("voice_pack_dir", "../新三国语音包")).resolve()

        self.player = AudioPlayer(
            base_voice_dir=str(voice_dir),
            volume=audio_cfg.get("volume", 0.5),
        )

        beh = config.get("behavior", {})
        self.buffer = ContextBuffer(
            window_seconds=beh.get("context_window_seconds", 30),
            max_messages=beh.get("max_context_messages", 6),
            cooldown_seconds=beh.get("cooldown_seconds", 8),
        )

        self.arbiter = MemeArbiter(config, memes)

        sv_dir = CURRENT_DIR / "models" / "sense-voice"
        vad_path = CURRENT_DIR / "models" / "silero_vad.onnx"
        vosk_path = CURRENT_DIR.parent / "AutoMemeDetector-main" / "models" / "vosk-model-small-cn-0.22"

        self.mic = AudioListener(
            on_speech_recognized=self.on_mic_speech,
            sense_voice_dir=str(sv_dir),
            silero_vad_path=str(vad_path),
            vosk_model_path=str(vosk_path),
            gain=audio_cfg.get("mic_gain", 3.5),
        )

        self.system_audio = SystemAudioListener(
            on_speech_recognized=self.on_teammate_speech,
            is_self_playing_fn=self.player.is_playing,
            sense_voice_dir=str(sv_dir),
            silero_vad_path=str(vad_path),
            gain=audio_cfg.get("speaker_gain", 1.5),
        )

        self.screen = ScreenWatcher(on_event_detected=self.on_screen_event)

    # ---------------- 事件回调 ----------------
    def on_mic_speech(self, text: str, emotion=None):
        logger.info("[麦克风] %s", text)
        self.buffer.add("麦克风语音", text, emotion=emotion)
        self._trigger_eval()

    def on_teammate_speech(self, text: str, emotion=None):
        logger.info("[队友语音] %s", text)
        self.buffer.add("队友开黑语音", text, emotion=emotion)
        self._trigger_eval()

    def on_screen_event(self, desc: str):
        logger.info("[画面事件] %s", desc)
        self.buffer.add("屏幕视觉", desc)
        self._trigger_eval()

    def _trigger_eval(self):
        threading.Thread(target=self._evaluate, daemon=True).start()

    def _evaluate(self):
        """决策主流程：冷却检查 -> 裁决 -> 播放"""
        if not self._eval_lock.acquire(blocking=False):
            return  # 上一次还没结束，跳过避免堆积
        try:
            in_cd, remaining = self.buffer.is_in_cooldown()
            if in_cd:
                logger.debug("冷却中，剩余 %.1fs", remaining)
                return

            context_str = self.buffer.get_prompt_context()
            decision = self.arbiter.decide(context_str)

            logger.info(
                "决策 %d (%s, %.0fms) — %s",
                decision.choice, decision.engine_used,
                decision.latency_ms, decision.reason,
            )

            if decision.should_play:
                self.player.play(decision.meme.filename)
                self.buffer.mark_played(decision.meme.title)
        finally:
            self._eval_lock.release()

    # ---------------- 生命周期 ----------------
    def start(self):
        self._running = True
        audio_cfg = self.config.get("audio", {})

        if audio_cfg.get("enable_mic", True):
            self.mic.start()
            logger.info("麦克风监听已启动")

        if audio_cfg.get("enable_speaker", True):
            self.system_audio.is_enabled = True
            self.system_audio.start()
            logger.info("扬声器回环监听已启动")

        if self.config.get("screen", {}).get("enable_screen_ocr", False):
            self.screen.is_ocr_enabled = True
            logger.info("屏幕文字检测已启动")

        self.screen.start()

    def stop(self):
        self._running = False
        self.mic.stop()
        self.system_audio.stop()
        self.screen.stop()
        self.player.stop()
        logger.info("服务已停止")

    def wait(self):
        """阻塞运行直到收到 Ctrl+C"""
        try:
            while self._running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("收到中断信号，正在停止…")
        finally:
            self.stop()


def run_self_check(config: dict, memes: list) -> bool:
    """启动前的轻量自检，提前暴露常见配置问题"""
    ok = True

    # 1) 配置结构
    for section in ("api", "behavior", "audio"):
        if section not in config:
            logger.error("配置缺少必需段落: %s", section)
            ok = False

    # 2) 音效库
    if not memes:
        logger.error("音效库为空: %s", MEME_LIB_PATH)
        ok = False
    else:
        logger.info("音效库已载入 %d 项", len(memes))

    # 3) 离线模型
    sv = CURRENT_DIR / "models" / "sense-voice"
    if not (sv / "tokens.txt").exists():
        logger.warning("未找到 SenseVoice 模型，语音识别可能不可用")
        logger.warning("请先执行: python download_models.py")

    vad = CURRENT_DIR / "models" / "silero_vad.onnx"
    if not vad.exists():
        logger.warning("未找到 Silero VAD 模型: %s", vad)

    # 4) API 配置
    api = config.get("api", {})
    key = api.get("api_key", "")
    if not key or "YOUR_API_KEY" in key:
        logger.warning("未配置 API Key，将使用内置规则引擎（离线可用，但接梗能力有限）")
    else:
        logger.info("已配置 API: %s (%s)", api.get("model"), api.get("base_url"))

    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoMeme Studio 无界面入口")
    parser.add_argument("--check", action="store_true", help="仅做自检，不启动监听")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="日志级别")
    args = parser.parse_args()

    setup_logging(
        level=getattr(logging, args.log_level),
        log_file=str(CURRENT_DIR / "logs" / "automeme-cli.log"),
    )

    if not CONFIG_PATH.exists():
        logger.error("未找到配置文件: %s", CONFIG_PATH)
        logger.error("请先复制模板: copy config.example.json config.json")
        return 1

    config = load_json(CONFIG_PATH)
    memes = load_json(MEME_LIB_PATH)

    if not run_self_check(config, memes):
        return 1

    if args.check:
        logger.info("自检完成")
        return 0

    logger.info("=" * 56)
    logger.info("AutoMeme Studio 无界面模式已启动")
    logger.info("监听麦克风与开黑语音，按 Ctrl+C 停止")
    logger.info("=" * 56)

    service = AutoMemeService(config, memes)
    service.start()
    service.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
