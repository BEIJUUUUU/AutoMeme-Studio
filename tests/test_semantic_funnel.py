"""
语义漏斗与线程安全测试。

关键点：
  - requests.Session 非线程安全，必须做到「每线程一个 Session」
  - 语义向量不可用时应安全回退关键词召回，而不是静默降级或崩溃
"""

import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.http_client import get_shared_session
from core.semantic_funnel import (
    EmbeddingClient,
    SemanticFunnel,
    _cosine,
    local_hash_embedding,
)


class TestThreadLocalSession:
    """requests.Session 非线程安全 —— 必须每线程独立"""

    def test_same_thread_reuses_session(self):
        a = get_shared_session()
        b = get_shared_session()
        assert a is b, "同一线程内应复用同一个 Session"

    def test_different_threads_get_different_sessions(self):
        main = get_shared_session()
        captured = {}

        def worker(name):
            captured[name] = get_shared_session()

        threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ids = {id(main)} | {id(s) for s in captured.values()}
        assert len(ids) == 4, "每个线程应拿到独立的 Session"

    def test_concurrent_access_is_safe(self):
        """并发获取不应抛异常或返回 None"""
        results = []
        lock = threading.Lock()

        def worker():
            try:
                s = get_shared_session()
                with lock:
                    results.append(s is not None)
            except Exception:
                with lock:
                    results.append(False)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(results), "并发获取 Session 应全部成功"


class TestEmbeddingBasics:

    def test_cosine_identical_is_one(self):
        v = local_hash_embedding("测试文本")
        assert abs(_cosine(v, v) - 1.0) < 1e-6

    def test_cosine_orthogonal_is_zero(self):
        assert _cosine([], [1.0]) == 0.0
        assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_local_embedding_deterministic(self):
        a = local_hash_embedding("同一段文字")
        b = local_hash_embedding("同一段文字")
        assert a == b, "本地向量应可复现"

    def test_local_embedding_same_length(self):
        assert len(local_hash_embedding("短")) == len(local_hash_embedding("长一些的文字"))

    def test_local_fallback_defaults_off(self):
        """本地哈希向量无法识别同义词，默认不应作为兜底"""
        client = EmbeddingClient({"embedding": {}})
        assert client.use_local_fallback is False

    def test_usable_requires_config(self):
        client = EmbeddingClient({"embedding": {"enabled": True}})
        assert client.usable is False, "未配 base_url 时不应视为可用"


class TestSemanticFunnelFallback:
    """语义不可用时必须安全回退，不能中断功能"""

    def test_rank_without_index_returns_empty(self):
        funnel = SemanticFunnel({"embedding": {"enabled": False}})
        assert funnel.rank("看我单杀带飞", top_k=5) == []

    def test_health_reports_not_enabled(self):
        funnel = SemanticFunnel({"embedding": {"enabled": False}})
        ok, msg = funnel.health()
        assert ok is False
        assert "未启用" in msg or "不可用" in msg

    def test_arbiter_falls_back_to_keyword(self):
        """语义引擎但无 embedding 服务时，应回退关键词召回"""
        from copy import deepcopy
        from core.arbiter import MemeArbiter

        lib = [
            {"id": 1, "title": "不可能，绝对不可能", "filename": "a.mp3",
             "triggers": "被打脸、空大、暴毙", "vibe": "破防"},
        ]
        cfg = {
            "api": {"base_url": "http://x", "api_key": "k" * 20,
                    "model": "m", "mode": "auto"},
            "behavior": {"funnel_engine": "semantic", "funnel_top_k": 5},
            "embedding": {"enabled": False},
        }
        arb = MemeArbiter(cfg, lib)
        hits = arb._build_scored_candidates("我空大了", top_k=5)
        assert hits, "语义不可用时应回退关键词召回并命中"
        assert hits[0][0] == 1

    def test_keyword_engine_default(self):
        from core.arbiter import MemeArbiter

        lib = [
            {"id": 1, "title": "冲我来了", "filename": "a.mp3",
             "triggers": "被集火", "vibe": "惊慌"},
        ]
        cfg = {
            "api": {"base_url": "http://x", "api_key": "k" * 20,
                    "model": "m", "mode": "auto"},
            "behavior": {},
        }
        arb = MemeArbiter(cfg, lib)
        assert arb._semantic_funnel is None, "默认应为关键词引擎"
        assert arb._build_scored_candidates("对面全冲我来了", top_k=3)


class TestSemanticIndex:

    def test_index_signature_changes_with_content(self):
        """梗库内容变化时指纹应变化，触发重建"""
        funnel = SemanticFunnel({"embedding": {"enabled": False}})
        memes_a = {1: type("M", (), {"title": "A", "triggers": "x", "vibe": "y"})()}
        memes_b = {1: type("M", (), {"title": "B", "triggers": "x", "vibe": "y"})()}
        assert funnel._signature_of(memes_a) != funnel._signature_of(memes_b)

    def test_index_signature_stable_for_same_content(self):
        funnel = SemanticFunnel({"embedding": {"enabled": False}})
        m = type("M", (), {"title": "A", "triggers": "x", "vibe": "y"})()
        assert funnel._signature_of({1: m}) == funnel._signature_of({1: m})


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
