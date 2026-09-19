import time
from typing import List, Dict, Optional

class ContextMessage:
    def __init__(self, source: str, content: str, emotion: Optional[str] = None, timestamp: Optional[float] = None):
        self.source = source  # "麦克风", "系统音频", "屏幕视觉", "开黑对话"
        self.content = content
        self.emotion = emotion
        self.timestamp = timestamp or time.time()

    def format_relative(self, now: float) -> str:
        diff = max(0, int(now - self.timestamp))
        time_tag = "刚刚" if diff < 3 else f"{diff}秒前"
        emotion_tag = f" [情绪状态: {self.emotion}]" if self.emotion else ""
        return f"[{time_tag}] [{self.source}] \"{self.content}\"{emotion_tag}"

class ContextBuffer:
    def __init__(self, window_seconds: int = 30, max_messages: int = 6, cooldown_seconds: int = 12):
        self.window_seconds = window_seconds
        self.max_messages = max_messages
        self.cooldown_seconds = cooldown_seconds
        self.messages: List[ContextMessage] = []
        self.last_played_time: float = 0.0
        self.last_played_meme: str = ""

    def add(self, source: str, content: str, emotion: Optional[str] = None):
        now = time.time()
        self.messages.append(ContextMessage(source, content.strip(), emotion, now))
        self._prune(now)

    def _prune(self, now: float):
        self.messages = [m for m in self.messages if now - m.timestamp <= self.window_seconds]
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def is_in_cooldown(self) -> tuple[bool, float]:
        now = time.time()
        elapsed = now - self.last_played_time
        remaining = self.cooldown_seconds - elapsed
        if remaining > 0:
            return True, remaining
        return False, 0.0

    def mark_played(self, meme_title: str):
        self.last_played_time = time.time()
        self.last_played_meme = meme_title
        self.add("系统音效", f"已触发播放: 《{meme_title}》")

    def get_prompt_context(self) -> str:
        now = time.time()
        self._prune(now)
        if not self.messages:
            return "(当前暂无新的对话与事件)"
        return "\n".join(m.format_relative(now) for m in self.messages)

    def clear(self):
        self.messages.clear()
