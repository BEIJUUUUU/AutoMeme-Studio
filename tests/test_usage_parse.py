"""
Token / 缓存统计解析测试。

覆盖多家接口的 usage 字段命名差异，这是最容易因上游改动而静默失效的地方。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.arbiter import parse_usage


class TestParseUsage:
    """parse_usage 返回 (prompt, completion, total, cached, note)"""

    def test_openai_with_cache(self):
        usage = {
            "prompt_tokens": 1726,
            "completion_tokens": 171,
            "total_tokens": 1897,
            "prompt_tokens_details": {"cached_tokens": 1536},
        }
        p, c, t, cached, note = parse_usage(usage)
        assert (p, c, t) == (1726, 171, 1897)
        assert cached == 1536
        assert cached / p > 0.85

    def test_deepseek_official(self):
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "prompt_cache_hit_tokens": 896,
        }
        p, c, t, cached, _ = parse_usage(usage)
        assert cached == 896
        assert t == 1050

    def test_anthropic_cache_read(self):
        usage = {
            "input_tokens": 2000,
            "output_tokens": 80,
            "cache_read_input_tokens": 1792,
        }
        p, c, t, cached, _ = parse_usage(usage)
        assert p == 2000
        assert c == 80
        assert cached == 1792
        assert t == 2080  # 缺失 total 时应由 p+c 推导

    def test_anthropic_cache_creation_only(self):
        """只写入缓存、未命中时，应给出说明而不是假装命中"""
        usage = {
            "input_tokens": 2000,
            "output_tokens": 80,
            "cache_creation_input_tokens": 2048,
        }
        _, _, _, cached, note = parse_usage(usage)
        assert cached == 0
        assert "写入缓存" in note

    def test_gemini_native_fields(self):
        usage = {
            "promptTokenCount": 1500,
            "candidatesTokenCount": 120,
            "totalTokenCount": 1620,
            "cachedContentTokenCount": 1400,
        }
        p, c, t, cached, _ = parse_usage(usage)
        assert (p, c, t) == (1500, 120, 1620)
        assert cached == 1400

    def test_proxy_without_cache_fields(self):
        """反代不返回缓存字段时应为 0，且不报错"""
        usage = {"prompt_tokens": 900, "completion_tokens": 40, "total_tokens": 940}
        p, c, t, cached, _ = parse_usage(usage)
        assert (p, c, t) == (900, 40, 940)
        assert cached == 0

    def test_empty_usage(self):
        """空 usage 应安全返回全 0，不抛异常"""
        p, c, t, cached, note = parse_usage({})
        assert (p, c, t, cached) == (0, 0, 0, 0)

    def test_non_dict_usage_reports_reason(self):
        """非字典 usage 应给出可读说明，方便排查上游格式变更"""
        p, c, t, cached, note = parse_usage(None)
        assert (p, c, t, cached) == (0, 0, 0, 0)
        assert note, "非字典 usage 应附带说明"

    def test_non_dict_usage(self):
        """usage 为 None 或非字典时不应抛异常"""
        for bad in (None, [], "abc", 42):
            p, c, t, cached, note = parse_usage(bad)
            assert (p, c, t, cached) == (0, 0, 0, 0)

    def test_zero_values_treated_as_absent(self):
        """字段为 0 时不应被误判为有效值"""
        usage = {"prompt_tokens": 0, "input_tokens": 500, "completion_tokens": 0}
        p, _, _, _, _ = parse_usage(usage)
        assert p == 500


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
