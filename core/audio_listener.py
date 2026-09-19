import json
import re
import queue
import time
import collections
import threading
from pathlib import Path
from typing import Callable, Optional, List, Dict
import numpy as np

from core.http_client import get_logger

logger = get_logger("audio")

try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except ImportError:
    _SD_AVAILABLE = False

try:
    import sherpa_onnx
    _SHERPA_AVAILABLE = True
except ImportError:
    _SHERPA_AVAILABLE = False

EMOTION_MAP = {
    "<|ANGRY|>": "红温/愤怒",
    "<|HAPPY|>": "兴奋/喜悦",
    "<|LAUGHTER|>": "爆笑/乐了",
    "<|SAD|>": "沮丧/悲伤",
    "<|FEARFUL|>": "害怕/惊恐",
    "<|DISGUSTED|>": "嫌弃/无语",
    "<|SURPRISED|>": "震惊/意外"
}

# 常见游戏口语、开黑黑话与同音字智能纠错字典
GAMING_SLANG_MAP = {
    "丹砂": "单杀", "耽杀": "单杀", "单沙": "单杀",
    "代飞": "带飞", "带废": "带飞", "袋飞": "带飞",
    "孔大": "空大", "恐大": "空大",
    "抱壁": "暴毙", "爆比": "暴毙", "爆闭": "暴毙",
    "哄温": "红温", "红问": "红温", "鸿温": "红温",
    "当服一大白": "当浮一大白", "当扶一大白": "当浮一大白",
    "五沙": "五杀", "无杀": "五杀", "误杀五杀": "五杀",
    "干马": "干嘛", "甘嘛": "干嘛", "杆嘛": "干嘛",
    "死鸭子追硬": "死鸭子嘴硬", "死鸭子自硬": "死鸭子嘴硬",
    "借口难耐": "饥渴难耐", "几渴难耐": "饥渴难耐",
    "称弟": "称帝", "称第": "称帝",
    "天下雾敌": "天下无敌",
    "谁的布将": "谁的部将"
}

def correct_slang(text: str) -> str:
    for wrong, right in GAMING_SLANG_MAP.items():
        if wrong in text:
            text = text.replace(wrong, right)
    return text

class AudioListener:
    """
    高精度离线麦克风监听器 (用于监听自己说话)
    特性：
    - Windows 系统内核级 16000Hz 直采 (杜绝重采样高频混叠刺耳失真)
    - 150ms 语音前置预冲 (Pre-pad)，彻底杜绝开头发音吃字、吞辅音
    - 动态峰值声学归一化 (AGC)，自动提升微弱说话信噪比
    - 游戏口语与常见同音黑话自动修正
    """
    def __init__(
        self,
        on_speech_recognized: Callable[[str, Optional[str]], None],
        sense_voice_dir: Optional[str] = None,
        silero_vad_path: Optional[str] = None,
        vosk_model_path: Optional[str] = None,
        sample_rate: int = 16000,
        on_level_meter: Optional[Callable[[int], None]] = None,
        device_index: Optional[int] = None,
        gain: float = 3.5
    ):
        self.on_speech_recognized = on_speech_recognized
        self.on_level_meter = on_level_meter
        self.device_index = device_index
        self.sample_rate = sample_rate
        self.gain = gain
        self.enable_emotion = False # 默认关闭不可靠的声音情绪头，纯靠上下文文本判断
        self.is_running = False
        self.is_enabled = True
        self.thread: Optional[threading.Thread] = None
        self._need_restart = False
        self.active_device_name = "默认麦克风"

        self.engine_type = "none"
        self.recognizer = None
        self.vad = None

        if _SHERPA_AVAILABLE and sense_voice_dir and silero_vad_path:
            sv_dir = Path(sense_voice_dir)
            vad_file = Path(silero_vad_path)
            model_file = sv_dir / "model.int8.onnx"
            if not model_file.exists():
                model_file = sv_dir / "model.onnx"
            tokens_file = sv_dir / "tokens.txt"

            if model_file.exists() and tokens_file.exists() and vad_file.exists():
                try:
                    self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                        model=str(model_file),
                        tokens=str(tokens_file),
                        language="zh",
                        use_itn=True,
                        num_threads=2
                    )

                    vad_config = sherpa_onnx.VadModelConfig()
                    vad_config.silero_vad.model = str(vad_file)
                    vad_config.silero_vad.threshold = 0.32  # 提高敏感度，避免首字开头弱辅音被削
                    vad_config.silero_vad.min_speech_duration = 0.18
                    vad_config.silero_vad.min_silence_duration = 0.32
                    vad_config.sample_rate = self.sample_rate
                    self.vad = sherpa_onnx.VoiceActivityDetector(vad_config, buffer_size_in_seconds=30)

                    self.engine_type = "sensevoice"
                    logger.info("麦克风 SenseVoice + Silero-VAD 实例已就绪")
                except Exception as e:
                    logger.error("初始化 SenseVoice 失败: %s", e)

        if self.engine_type == "none" and vosk_model_path:
            vk_path = Path(vosk_model_path)
            if vk_path.exists():
                try:
                    from vosk import Model, KaldiRecognizer
                    self.vosk_model = Model(str(vk_path))
                    self.vosk_rec = KaldiRecognizer(self.vosk_model, self.sample_rate)
                    self.engine_type = "vosk"
                except Exception as e:
                    logger.error("初始化 Vosk 失败: %s", e)

    @staticmethod
    def get_available_mic_devices() -> List[Dict]:
        if not _SD_AVAILABLE:
            return []
        devices = []
        seen_names = set()
        try:
            for i, d in enumerate(sd.query_devices()):
                if d['max_input_channels'] > 0:
                    name = d['name'].strip()
                    if name not in seen_names and not name.startswith("Microsoft"):
                        seen_names.add(name)
                        devices.append({
                            "index": i,
                            "name": name,
                            "rate": int(d['default_samplerate'])
                        })
        except Exception:
            pass
        return devices

    def set_gain(self, gain: float):
        self.gain = max(0.5, min(15.0, gain))

    def switch_device(self, new_index: Optional[int]):
        self.device_index = new_index
        self._need_restart = True

    def _listen_loop_sensevoice(self):
        while self.is_running:
            self._need_restart = False
            audio_queue = queue.Queue()

            dev_idx = self.device_index
            try:
                if dev_idx is not None:
                    dev_info = sd.query_devices(dev_idx, 'input')
                    self.active_device_name = dev_info['name']
                else:
                    self.active_device_name = "默认系统麦克风"
            except Exception:
                dev_idx = None
                self.active_device_name = "默认系统麦克风"

            logger.info("挂载麦克风设备: %s (内核级 16000Hz 直采)", self.active_device_name)

            def callback(indata, frames, time_info, status):
                audio_queue.put(indata.copy())

            # 保持前置 150ms 缓冲池 (3块 * 50ms)，防止吃掉句首爆破音
            pre_buffer = collections.deque(maxlen=3)
            blocksize = 800  # 16000Hz 下的 50ms
            last_level_emit = 0.0
            accumulated_seconds = 0.0

            try:
                with sd.InputStream(
                    device=dev_idx,
                    samplerate=16000,
                    channels=1,
                    dtype='float32',
                    blocksize=blocksize,
                    callback=callback
                ):
                    while self.is_running and not self._need_restart:
                        if not self.is_enabled:
                            time.sleep(0.3)
                            while not audio_queue.empty():
                                audio_queue.get()
                            pre_buffer.clear()
                            if self.on_level_meter:
                                self.on_level_meter(0)
                            continue

                        try:
                            chunk = audio_queue.get(timeout=0.3)
                        except queue.Empty:
                            continue

                        raw_flat = chunk.flatten()
                        flat_samples = np.clip(raw_flat * self.gain, -1.0, 1.0)

                        # 电平跳动
                        now = time.time()
                        if self.on_level_meter and now - last_level_emit >= 0.08:
                            rms = float(np.sqrt(np.mean(flat_samples**2)))
                            level = min(100, int(rms * 420))
                            self.on_level_meter(level)
                            last_level_emit = now

                        self.vad.accept_waveform(flat_samples)
                        pre_buffer.append(flat_samples)
                        accumulated_seconds += len(flat_samples) / 16000.0

                        # 防锁死保护
                        if accumulated_seconds > 4.5:
                            self.vad.flush()
                            accumulated_seconds = 0.0

                        while not self.vad.empty():
                            segment = self.vad.front
                            samples = np.array(segment.samples, dtype=np.float32)
                            self.vad.pop()
                            accumulated_seconds = 0.0

                            if len(samples) < self.sample_rate * 0.20:
                                continue

                            # 拼上前置 150ms 缓冲，找回句首被截掉的微弱辅音（b, p, d, t, c, s）
                            if len(pre_buffer) > 0:
                                pre_chunk = np.concatenate(list(pre_buffer))
                                full_samples = np.concatenate([pre_chunk, samples])
                            else:
                                full_samples = samples

                            # 动态峰值声学归一化 (AGC)
                            max_peak = np.max(np.abs(full_samples))
                            if max_peak > 0.003:
                                gain = min(5.0, 0.75 / max_peak)
                                full_samples = full_samples * gain

                            stream = self.recognizer.create_stream()
                            stream.accept_waveform(self.sample_rate, full_samples)
                            self.recognizer.decode_stream(stream)
                            
                            raw_text = stream.result.text.strip()
                            emotion_raw = stream.result.emotion.strip()
                            event_raw = stream.result.event.strip()

                            clean_text = re.sub(r"<\|.*?\|>", "", raw_text).strip()
                            clean_text = re.sub(r"[\u3040-\u30ff\u31f0-\u31ff]", "", clean_text)
                            clean_text = clean_text.replace(" ", "").strip()

                            # 游戏口语与常见同音黑话自动纠错
                            clean_text = correct_slang(clean_text)

                            if clean_text and re.search(r"[\u4e00-\u9fa5a-zA-Z0-9]", clean_text):
                                emotion_tag = None
                                if self.enable_emotion:
                                    if emotion_raw in EMOTION_MAP:
                                        emotion_tag = EMOTION_MAP[emotion_raw]
                                    elif "<|LAUGHTER|>" in event_raw:
                                        emotion_tag = "爆笑/乐了"

                                logger.debug("麦克风识别: %s", clean_text)
                                self.on_speech_recognized(clean_text, emotion_tag)

            except Exception as e:
                logger.error("麦克风监听流异常: %s", e)
                time.sleep(1.0)

    def start(self):
        if not _SD_AVAILABLE or self.engine_type == "none":
            return
        if self.is_running:
            return
        self._join_thread()
        self.is_running = True
        self.thread = threading.Thread(
            target=self._listen_loop_sensevoice, daemon=True, name="mic-listener")
        self.thread.start()

    def stop(self):
        self.is_running = False
        self._join_thread()
        if self.on_level_meter:
            self.on_level_meter(0)

    def _join_thread(self, timeout: float = 3.0):
        """等待监听线程真正退出，避免停止后仍持有麦克风设备"""
        t = getattr(self, "thread", None)
        if t is None or t is threading.current_thread() or not t.is_alive():
            return
        t.join(timeout=timeout)
        if t.is_alive():
            logger.warning("麦克风监听线程未能在 %.1fs 内退出", timeout)
