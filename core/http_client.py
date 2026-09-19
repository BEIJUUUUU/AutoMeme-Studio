"""
统一的日志与 HTTP 请求基础设施。

提供：
  - setup_logging()          全局日志初始化
  - post_json_with_retry()   带指数退避重试与熔断的 JSON POST

设计目标是让上层代码不必关心网络抖动、限流、服务端 5xx 等瞬态故障。
"""

import json
import logging
import random
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger("automeme")


# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------
def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    quiet_console: bool = False,
) -> logging.Logger:
    """
    初始化全局日志。

    Args:
        level: 日志级别
        log_file: 若提供，则同时写入该文件
        quiet_console: 打包为 GUI 程序时设为 True，只写文件不占控制台
    """
    root = logging.getLogger("automeme")
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if log_file:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(level)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception as e:  # 日志文件不可写不应导致程序崩溃
            print(f"[logging] 无法创建日志文件 {log_file}: {e}")

    if not quiet_console:
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    if not root.handlers:
        root.addHandler(logging.NullHandler())

    return root


def get_logger(name: str = "") -> logging.Logger:
    """获取子 logger，例如 get_logger('arbiter') -> automeme.arbiter"""
    return logging.getLogger(f"automeme.{name}" if name else "automeme")


# --------------------------------------------------------------------------
# 熔断器
# --------------------------------------------------------------------------
class CircuitBreaker:
    """
    连续失败达到阈值后打开熔断，在冷却期内直接快速失败，
    避免在服务端故障或限流时把请求无限打出去。
    """

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._failures < self.failure_threshold:
                return False
            if time.time() - self._opened_at >= self.cooldown_seconds:
                # 冷却结束，进入半开状态，允许一次试探
                self._failures = self.failure_threshold - 1
                return False
            return True

    def record_success(self):
        with self._lock:
            self._failures = 0
            self._opened_at = 0.0

    def record_failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.time()

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failures


# --------------------------------------------------------------------------
# 带重试的 POST
# --------------------------------------------------------------------------
# 可重试的状态码：限流与服务端瞬态故障
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def post_json_with_retry(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: float = 7.0,
    max_attempts: int = 3,
    base_delay: float = 0.6,
    max_delay: float = 8.0,
    breaker: Optional[CircuitBreaker] = None,
    session: Optional[requests.Session] = None,
) -> Tuple[Optional[requests.Response], Optional[str]]:
    """
    发送 JSON POST，对瞬态故障自动重试。

    返回:
        (response, error_message)
        成功时 error_message 为 None；失败时为可读的错误说明。

    重试策略：
        - 429 / 5xx / 连接错误 / 超时  -> 指数退避后重试
        - 429 且带 Retry-After 头      -> 优先遵守服务端建议
        - 4xx（除 408/425/429）        -> 不重试，直接返回错误
    """
    if breaker is not None and breaker.is_open:
        return None, f"熔断已开启（连续失败 {breaker.failure_count} 次），暂缓请求"

    sess = session or requests
    last_error = "未知错误"

    for attempt in range(1, max_attempts + 1):
        try:
            resp = sess.post(url, headers=headers, json=payload, timeout=timeout)

            if resp.status_code == 200:
                if breaker is not None:
                    breaker.record_success()
                return resp, None

            # 不可重试的客户端错误，直接返回
            if resp.status_code not in RETRYABLE_STATUS:
                if breaker is not None:
                    breaker.record_failure()
                snippet = (resp.text or "")[:160].replace("\n", " ")
                return None, f"HTTP {resp.status_code}: {snippet}"

            # 可重试
            last_error = f"HTTP {resp.status_code}"
            if breaker is not None:
                breaker.record_failure()

            if attempt >= max_attempts:
                snippet = (resp.text or "")[:160].replace("\n", " ")
                return None, f"重试 {max_attempts} 次后仍失败 {last_error}: {snippet}"

            delay = _compute_delay(attempt, base_delay, max_delay, resp.headers)

        except (requests.Timeout, requests.ConnectionError) as e:
            last_error = f"{type(e).__name__}"
            if breaker is not None:
                breaker.record_failure()
            if attempt >= max_attempts:
                return None, f"重试 {max_attempts} 次后网络异常: {last_error}"
            delay = _compute_delay(attempt, base_delay, max_delay, None)

        except Exception as e:
            # 编码、解析等非网络异常不重试
            if breaker is not None:
                breaker.record_failure()
            return None, f"请求异常: {type(e).__name__}: {e}"

        logger.warning("请求失败(%s)，%.2fs 后第 %d 次重试", last_error, delay, attempt + 1)
        time.sleep(delay)

    return None, f"请求最终失败: {last_error}"


def _compute_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    resp_headers: Optional[Any],
) -> float:
    """指数退避 + 抖动；若服务端给出 Retry-After 则优先遵守"""
    if resp_headers is not None:
        retry_after = resp_headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), max_delay)
            except (TypeError, ValueError):
                pass

    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
    # 加入 ±25% 抖动，避免多个请求同时重试造成二次冲击
    jitter = delay * 0.25 * (random.random() * 2 - 1)
    return max(0.05, delay + jitter)


# --------------------------------------------------------------------------
# 连接池复用
# --------------------------------------------------------------------------
# 注意：requests.Session 官方明确说明「不是线程安全的」。
# 本项目有多个线程可能同时发请求（抽帧线程、语音线程、截图线程），
# 因此这里为每个线程分配独立的 Session：既避免了共享状态的竞争，
# 又能在同线程内复用连接。
_thread_local = threading.local()


def _build_session() -> requests.Session:
    """构造一个带连接池的 Session"""
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=8,
        pool_maxsize=16,
        max_retries=0,  # 重试由本模块统一负责
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def get_shared_session() -> requests.Session:
    """
    获取当前线程专属的 Session（线程局部存储）。

    名字保留为 get_shared_session 以兼容既有调用；实际语义是
    「每个线程一个 Session」，从而规避 requests.Session 的线程安全问题。
    """
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = _build_session()
        _thread_local.session = sess
    return sess
