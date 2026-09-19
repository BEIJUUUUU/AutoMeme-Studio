"""
HTTP 重试与熔断测试。

全部使用假的 session 对象，不发真实网络请求。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.http_client import CircuitBreaker, post_json_with_retry


class FakeResponse:
    def __init__(self, status_code=200, text="ok", headers=None, payload=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._payload = payload or {"choices": [{"message": {"content": "{}"}}]}

    def json(self):
        return self._payload


class FakeSession:
    """按预设序列依次返回响应或抛异常"""

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        item = self.sequence.pop(0) if self.sequence else FakeResponse()
        if isinstance(item, Exception):
            raise item
        return item


class TestRetry:

    def test_success_first_try(self):
        sess = FakeSession([FakeResponse(200)])
        resp, err = post_json_with_retry("http://x", {}, {}, session=sess, max_attempts=3)
        assert err is None
        assert resp is not None
        assert sess.calls == 1

    def test_retries_on_500_then_succeeds(self):
        sess = FakeSession([FakeResponse(500), FakeResponse(200)])
        resp, err = post_json_with_retry(
            "http://x", {}, {}, session=sess, max_attempts=3, base_delay=0.01)
        assert err is None
        assert sess.calls == 2, "5xx 应触发重试"

    def test_retries_on_429(self):
        sess = FakeSession([FakeResponse(429), FakeResponse(429), FakeResponse(200)])
        resp, err = post_json_with_retry(
            "http://x", {}, {}, session=sess, max_attempts=4, base_delay=0.01)
        assert err is None
        assert sess.calls == 3

    def test_no_retry_on_401(self):
        """认证失败是确定性错误，重试无意义"""
        sess = FakeSession([FakeResponse(401, "unauthorized")])
        resp, err = post_json_with_retry("http://x", {}, {}, session=sess, max_attempts=3)
        assert resp is None
        assert err and "401" in err
        assert sess.calls == 1, "4xx 不应重试"

    def test_no_retry_on_400(self):
        sess = FakeSession([FakeResponse(400, "bad request")])
        _, err = post_json_with_retry("http://x", {}, {}, session=sess, max_attempts=3)
        assert err and "400" in err
        assert sess.calls == 1

    def test_exhausts_attempts(self):
        sess = FakeSession([FakeResponse(503)] * 5)
        resp, err = post_json_with_retry(
            "http://x", {}, {}, session=sess, max_attempts=3, base_delay=0.01)
        assert resp is None
        assert err
        assert sess.calls == 3, "应恰好尝试 max_attempts 次"

    def test_retries_on_timeout(self):
        import requests
        sess = FakeSession([requests.Timeout("timeout"), FakeResponse(200)])
        resp, err = post_json_with_retry(
            "http://x", {}, {}, session=sess, max_attempts=3, base_delay=0.01)
        assert err is None
        assert sess.calls == 2

    def test_retry_after_header_respected(self):
        """服务端给出 Retry-After 时应优先遵守（此处仅验证不报错且重试成功）"""
        sess = FakeSession([
            FakeResponse(429, headers={"Retry-After": "0"}),
            FakeResponse(200),
        ])
        resp, err = post_json_with_retry(
            "http://x", {}, {}, session=sess, max_attempts=3, base_delay=0.01)
        assert err is None


class TestCircuitBreaker:

    def test_initially_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=1)
        assert not cb.is_open

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open

    def test_success_resets(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert not cb.is_open
        assert cb.failure_count == 0

    def test_half_open_after_cooldown(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open
        import time
        time.sleep(0.15)
        assert not cb.is_open, "冷却结束后应允许试探请求"

    def test_breaker_blocks_requests(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=10)
        cb.record_failure()
        cb.record_failure()

        sess = FakeSession([FakeResponse(200)])
        resp, err = post_json_with_retry(
            "http://x", {}, {}, session=sess, max_attempts=3, breaker=cb)
        assert resp is None
        assert err and "熔断" in err
        assert sess.calls == 0, "熔断开启时不应发出请求"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
