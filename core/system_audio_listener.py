import time
import re
import queue
import collections
import threading
from pathlib import Path
from typing import Callable, Optional
import numpy as np

try:
    import pyaudiowpatch as pyaudio
    _PYAUDIO_OK = True
except ImportError:
    _PYAUDIO_OK = False

try:
    import sherpa_onnx
    _SHERPA_OK = True
except ImportError:
    _SHERPA_OK = False

from core.audio_listener import EMOTION_MAP, correct_slang
from core.http_client import get_logger

logger = get_logger("system_audio")

class SystemAudioListener:
    """
    系统扬声器与游戏/开黑语音回环监听器 (WASAPI Loopback)
    负责监听：队友在 Discord / QQ / 游戏内开麦的声音，以及游戏内台词
    具备：实时电平跳动反馈、热插拔切换设备、防自声死循环、针对游戏BGM的长语音强制切片保护
    """
    def __init__(
        self,
        on_speech_recognized: Callable[[str, Optional[str]], None],
        is_self_playing_fn: Callable[[], bool],
        sense_voice_dir: str,
        silero_vad_path: str,
        device_index: Optional[int] = None,
        on_level_meter: Optional[Callable[[int], None]] = None,
        gain: float = 1.5
    ):
        self.on_speech_recognized = on_speech_recognized
        self.is_self_playing_fn = is_self_playing_fn
        self.device_index = device_index
        self.on_level_meter = on_level_meter
        self.gain = gain
        self.enable_emotion = False
        self.is_running = False
        self.is_enabled = False
        self.thread: Optional[threading.Thread] = None
        # 用于在 stop() 时唤醒阻塞中的取数据循环
        self._stop_event = threading.Event()

        self.recognizer = None
        self.vad = None
        self.sample_rate = 16000
        self._need_restart_stream = False
        self.active_device_name = "未连接"

        if not _PYAUDIO_OK:
            logger.warning("未安装 PyAudioWPatch，系统音频回环不可用")
            return

        sv_dir = Path(sense_voice_dir)
        vad_file = Path(silero_vad_path)
        model_file = sv_dir / "model.int8.onnx"
        if not model_file.exists():
            model_file = sv_dir / "model.onnx"
        tokens_file = sv_dir / "tokens.txt"

        if _SHERPA_OK and model_file.exists() and tokens_file.exists() and vad_file.exists():
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
                vad_config.silero_vad.min_speech_duration = 0.20
                vad_config.silero_vad.min_silence_duration = 0.35 # 停顿 350ms 视为说完了
                vad_config.sample_rate = self.sample_rate
                self.vad = sherpa_onnx.VoiceActivityDetector(vad_config, buffer_size_in_seconds=30)
                logger.info("扬声器 SenseVoice + Silero-VAD 实例已就绪")
            except Exception as e:
                logger.error("初始化识别器失败: %s", e)

    @staticmethod
    def get_available_loopback_devices():
        """列出系统中所有的扬声器/耳机回环设备"""
        if not _PYAUDIO_OK:
            return []
        devices = []
        try:
            p = pyaudio.PyAudio()
            for loopback in p.get_loopback_device_info_generator():
                clean_name = loopback["name"].replace("[Loopback]", "").strip()
                devices.append({
                    "name": clean_name,
                    "index": loopback["index"],
                    "rate": int(loopback["defaultSampleRate"]),
                    "channels": loopback["maxInputChannels"]
                })
            p.terminate()
        except Exception:
            pass
        return devices

    def set_gain(self, gain: float):
        self.gain = max(0.5, min(15.0, gain))

    def switch_device(self, new_index: Optional[int]):
        """热切换监听设备"""
        self.device_index = new_index
        self._need_restart_stream = True

    def _loopback_listen_loop(self):
        while self.is_running:
            self._need_restart_stream = False
            p = pyaudio.PyAudio()
            stream = None
            try:
                wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
                
                target_device = None
                if self.device_index is not None:
                    try:
                        dev_info = p.get_device_info_by_index(self.device_index)
                        if dev_info.get("isLoopbackDevice"):
                            target_device = dev_info
                        else:
                            for lb in p.get_loopback_device_info_generator():
                                if dev_info["name"] in lb["name"]:
                                    target_device = lb
                                    break
                    except Exception:
                        target_device = None

                if not target_device:
                    default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
                    for loopback in p.get_loopback_device_info_generator():
                        if default_speakers["name"] in loopback["name"]:
                            target_device = loopback
                            break
                    if not target_device:
                        for loopback in p.get_loopback_device_info_generator():
                            target_device = loopback
                            break

                if not target_device:
                    logger.warning("未找到任何有效的系统回环输出设备")
                    p.terminate()
                    time.sleep(2.0)
                    continue

                dev_rate = int(target_device["defaultSampleRate"])
                dev_channels = int(target_device["maxInputChannels"])
                dev_index = int(target_device["index"])
                self.active_device_name = target_device["name"]
                logger.info("挂载扬声器回环: %s (%dHz, %d通道)", self.active_device_name, dev_rate, dev_channels)

                audio_queue = queue.Queue()

                def callback(in_data, frame_count, time_info, status):
                    audio_queue.put(in_data)
                    return (None, pyaudio.paContinue)

                stream = p.open(
                    format=pyaudio.paInt16,
                    channels=dev_channels,
                    rate=dev_rate,
                    input=True,
                    input_device_index=dev_index,
                    frames_per_buffer=2400,
                    stream_callback=callback
                )

                stream.start_stream()

                last_level_emit_time = 0.0
                accumulated_speech_seconds = 0.0
                pre_buffer = collections.deque(maxlen=3)

                while self.is_running and not self._need_restart_stream:
                    if not self.is_enabled:
                        # 可被 stop() 立即唤醒
                        self._stop_event.wait(timeout=0.3)
                        while not audio_queue.empty():
                            audio_queue.get()
                        if self.on_level_meter:
                            self.on_level_meter(0)
                        continue

                    # 自身播放保护：如果正在播放音效，跳过
                    if self.is_self_playing_fn():
                        time.sleep(0.08)
                        while not audio_queue.empty():
                            audio_queue.get()
                        if self.vad:
                            self.vad.reset()
                        accumulated_speech_seconds = 0.0
                        if self.on_level_meter:
                            self.on_level_meter(0)
                        continue

                    try:
                        raw_bytes = audio_queue.get(timeout=0.3)
                    except queue.Empty:
                        continue

                    if not raw_bytes:
                        continue

                    int_data = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                    if dev_channels == 2:
                        mono = (int_data[0::2] + int_data[1::2]) * 0.5
                    else:
                        mono = int_data

                    mono = np.clip(mono * self.gain, -1.0, 1.0)

                    # 计算实时 RMS 电平
                    now = time.time()
                    if self.on_level_meter and now - last_level_emit_time >= 0.08:
                        rms = float(np.sqrt(np.mean(mono**2)))
                        # 将 0.0~0.2 映射到 0~100 的电平值
                        level = min(100, int(rms * 450))
                        self.on_level_meter(level)
                        last_level_emit_time = now

                    # 重采样至 16000Hz (如果是 48000Hz 使用 3 抽样抗混叠均值滤波)
                    if dev_rate == 48000:
                        rem = len(mono) % 3
                        trimmed = mono[:-rem] if rem > 0 else mono
                        samples_16k = trimmed.reshape(-1, 3).mean(axis=1)
                    elif dev_rate != 16000:
                        step = dev_rate / 16000.0
                        indices = np.arange(0, len(mono), step).astype(int)
                        indices = indices[indices < len(mono)]
                        samples_16k = mono[indices]
                    else:
                        samples_16k = mono

                    if len(samples_16k) == 0:
                        continue

                    self.vad.accept_waveform(samples_16k)
                    pre_buffer.append(samples_16k)
                    accumulated_speech_seconds += len(samples_16k) / 16000.0

                    # 游戏 BGM 连续声保护：如果持续积累超过 4.5 秒没有检测到断句，强制 flush 切片一次
                    if accumulated_speech_seconds > 4.5:
                        self.vad.flush()
                        accumulated_speech_seconds = 0.0

                    while not self.vad.empty():
                        segment = self.vad.front
                        seg_samples = np.array(segment.samples, dtype=np.float32)
                        self.vad.pop()
                        accumulated_speech_seconds = 0.0

                        if self.is_self_playing_fn() or len(seg_samples) < self.sample_rate * 0.20:
                            continue

                        # 前置 150ms 缓冲防吃字
                        if len(pre_buffer) > 0:
                            pre_chunk = np.concatenate(list(pre_buffer))
                            full_samples = np.concatenate([pre_chunk, seg_samples])
                        else:
                            full_samples = seg_samples

                        # 动态峰值声学归一化 (AGC)
                        max_peak = np.max(np.abs(full_samples))
                        if max_peak > 0.003:
                            gain = min(5.0, 0.75 / max_peak)
                            full_samples = full_samples * gain

                        # 送入 SenseVoice
                        stream_rec = self.recognizer.create_stream()
                        stream_rec.accept_waveform(self.sample_rate, full_samples)
                        self.recognizer.decode_stream(stream_rec)

                        raw_text = stream_rec.result.text.strip()
                        emotion_raw = stream_rec.result.emotion.strip()
                        event_raw = stream_rec.result.event.strip()

                        clean_text = re.sub(r"<\|.*?\|>", "", raw_text).strip()
                        clean_text = re.sub(r"[\u3040-\u30ff\u31f0-\u31ff]", "", clean_text)
                        clean_text = clean_text.replace(" ", "").strip()

                        # 游戏口语与同音黑话智能修正
                        clean_text = correct_slang(clean_text)

                        if clean_text and re.search(r"[\u4e00-\u9fa5a-zA-Z0-9]", clean_text):
                            emotion_tag = None
                            if self.enable_emotion:
                                if emotion_raw in EMOTION_MAP:
                                    emotion_tag = EMOTION_MAP[emotion_raw]
                                elif "<|LAUGHTER|>" in event_raw:
                                    emotion_tag = "爆笑/乐了"

                            self.on_speech_recognized(clean_text, emotion_tag)

            except Exception as e:
                logger.error("扬声器回环异常: %s", e)
                time.sleep(1.0)
            finally:
                if stream:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
                p.terminate()

    def start(self):
        if not _PYAUDIO_OK or not self.recognizer:
            return
        if self.is_running:
            return
        self._join_thread()
        self.is_running = True
        self._stop_event.clear()
        self.thread = threading.Thread(
            target=self._loopback_listen_loop, daemon=True, name="speaker-listener")
        self.thread.start()

    def stop(self):
        self.is_running = False
        # 唤醒可能正处于阻塞取数据的循环，加快退出
        self._stop_event.set()
        self._join_thread()
        if self.on_level_meter:
            self.on_level_meter(0)

    def _join_thread(self, timeout: float = 3.0):
        """等待回环监听线程真正退出，避免释放后仍占用 WASAPI 设备"""
        t = getattr(self, "thread", None)
        if t is None or t is threading.current_thread() or not t.is_alive():
            return
        t.join(timeout=timeout)
        if t.is_alive():
            logger.warning("扬声器回环线程未能在 %.1fs 内退出", timeout)
