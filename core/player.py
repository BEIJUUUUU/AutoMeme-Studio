import os
import threading
import time
from pathlib import Path

from core.http_client import get_logger

logger = get_logger("player")

try:
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    _PYGAME_AVAILABLE = False


class AudioPlayer:
    def __init__(self, base_voice_dir: str = "", volume: float = 0.85):
        self.base_voice_dir = Path(base_voice_dir)
        self.volume = max(0.0, min(1.0, volume))
        self.is_initialized = False
        self._lock = threading.Lock()
        self.last_play_time = 0.0
        self._init_mixer()

    def _init_mixer(self):
        if not _PYGAME_AVAILABLE:
            logger.warning("未安装 pygame，音效播放将被静音模拟")
            return
        try:
            pygame.mixer.init()
            self.is_initialized = True
        except Exception as e:
            logger.error("初始化音频混合器失败: %s", e)

    def set_volume(self, volume: float):
        self.volume = max(0.0, min(1.0, volume))

    def play(self, relative_or_abs_path: str) -> bool:
        """非阻塞播放指定音频文件"""
        with self._lock:
            path = Path(relative_or_abs_path)
            # 尝试多候选路径查找
            if not path.is_absolute():
                candidates = [
                    self.base_voice_dir / path,
                    Path(__file__).parent.parent.parent / path,
                    Path(__file__).parent.parent / path,
                    Path("..") / path,
                    path,
                ]
                for c in candidates:
                    if c.resolve().exists():
                        path = c.resolve()
                        break
                else:
                    path = (self.base_voice_dir / path).resolve()

            if not path.exists():
                logger.error("音频文件不存在: %s", path)
                return False

            if not self.is_initialized:
                logger.info("[模拟] 播放音效 %s", path.name)
                return True

            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.load(str(path))
                pygame.mixer.music.set_volume(self.volume)
                pygame.mixer.music.play()
                self.last_play_time = time.time()
                logger.info("播放《%s》(音量 %d%%)", path.stem, int(self.volume * 100))
                return True
            except Exception as e:
                logger.error("播放失败 %s: %s", path.name, e)
                return False

    def is_playing(self) -> bool:
        """检查当前是否正在播放音效，或刚刚播放完毕（防自声回环回录）"""
        if not self.is_initialized:
            return False
        try:
            if pygame.mixer.music.get_busy():
                return True
            if time.time() - self.last_play_time < 1.5:
                return True
        except Exception:
            pass
        return False

    def stop(self):
        with self._lock:
            if self.is_initialized:
                try:
                    pygame.mixer.music.stop()
                except Exception:
                    pass
