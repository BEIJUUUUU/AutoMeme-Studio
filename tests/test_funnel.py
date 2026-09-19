"""
两段式漏斗粗筛测试。

这是 Token 优化的核心，也是最容易因梗库增删而失准的地方。
关键回归点：规则表按「标题」绑定，而非数字 id —— id 会随增删漂移。
"""

import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.arbiter import MemeArbiter

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sample_library():
    """一份最小可用的梗库，模拟真实结构"""
    return [
        {"id": 1, "title": "不可能，绝对不可能", "filename": "a.mp3",
         "triggers": "被打脸、空大、暴毙", "vibe": "破防、震惊"},
        {"id": 2, "title": "恭喜爹可以称帝了", "filename": "b.mp3",
         "triggers": "自吹自擂、盲目自大", "vibe": "反讽、捧杀"},
        {"id": 3, "title": "冲我来了", "filename": "c.mp3",
         "triggers": "被集火、敌人突脸", "vibe": "惊慌、求救"},
        {"id": 4, "title": "你找死吗", "filename": "d.mp3",
         "triggers": "残血反扑、敢来抓单", "vibe": "阴狠、反打"},
    ]


@pytest.fixture
def config():
    return {
        "api": {"base_url": "https://example.invalid/v1", "api_key": "test-key-12345678",
                "model": "test-model", "mode": "auto"},
        "behavior": {"use_funnel": True, "funnel_top_k": 5, "enable_thinking": False},
    }


@pytest.fixture
def arbiter(config, sample_library):
    return MemeArbiter(config, sample_library)


class TestFunnelScoring:

    def test_keyword_hit_returns_candidate(self, arbiter):
        cands = arbiter._build_scored_candidates("看我单杀带飞", top_k=5)
        assert cands, "命中触发词时应返回候选"
        ids = [c[0] for c in cands]
        assert 2 in ids, "『带飞』应召回《恭喜爹可以称帝了》"

    def test_no_hit_returns_empty(self, arbiter):
        cands = arbiter._build_scored_candidates("今天中午吃什么好呢", top_k=5)
        assert cands == [], "无关键词命中时应返回空列表"

    def test_empty_input(self, arbiter):
        assert arbiter._build_scored_candidates("", top_k=5) == []
        assert arbiter._build_scored_candidates("   ", top_k=5) == []

    def test_scores_sorted_descending(self, arbiter):
        cands = arbiter._build_scored_candidates("我空大了被打脸还暴毙", top_k=5)
        scores = [s for _, s in cands]
        assert scores == sorted(scores, reverse=True), "候选应按得分降序"

    def test_top_k_limits_results(self, arbiter):
        text = "带飞 空大 暴毙 集火 求救 反打 自吹自擂"
        cands = arbiter._build_scored_candidates(text, top_k=2)
        assert len(cands) <= 2

    def test_longer_keyword_scores_higher(self, arbiter):
        """
        更具体的长词应比短词权重更高。
        『冲我来了』（4 字）应比『救命』（2 字）得分高。
        """
        long_hit = arbiter._build_scored_candidates("对面全冲我来了", top_k=5)
        short_hit = arbiter._build_scored_candidates("救命", top_k=5)

        assert long_hit, "长词应能命中"
        assert short_hit, "短词应能命中"

        long_score = max(s for _, s in long_hit)
        short_score = max(s for _, s in short_hit)
        assert long_score > short_score, (
            f"长词得分 {long_score} 应高于短词 {short_score}"
        )

    def test_library_custom_triggers_participate(self, config):
        """
        用户自定义梗（不在内置规则表中的）应能通过 triggers 分词参与召回。
        """
        lib = [
            {"id": 1, "title": "自定义测试梗", "filename": "z.mp3",
             "triggers": "超级特殊关键词、别的什么", "vibe": "测试"},
        ]
        arb = MemeArbiter(config, lib)
        cands = arb._build_scored_candidates("这句话里有超级特殊关键词", top_k=5)
        assert cands, "自定义梗的 triggers 应参与粗筛"
        assert cands[0][0] == 1


class TestTitleBinding:
    """
    回归测试：规则表必须按标题绑定。
    历史 Bug —— 按数字 id 绑定，用户删过一个音效后 id 重排，导致全部错位，
    出现「说五杀却播放《死是凉爽的夏夜》」这类荒谬结果。
    """

    def test_resolve_returns_valid_id(self, arbiter):
        mid = arbiter._resolve_rule_id("不可能")
        assert mid in arbiter.memes

    def test_resolve_unknown_returns_none(self, arbiter):
        assert arbiter._resolve_rule_id("根本不存在的梗名") is None

    def test_rule_still_correct_after_id_reshuffle(self, config, sample_library):
        """删除中间一条、id 重排后，规则仍应指向正确音效"""
        lib = deepcopy(sample_library)
        del lib[1]  # 删掉「恭喜爹」
        for i, m in enumerate(lib, 1):
            m["id"] = i

        arb = MemeArbiter(config, lib)

        # 原本 id=3 的「冲我来了」现在是 id=2
        cands = arb._build_scored_candidates("对面全冲我来了救命", top_k=5)
        titles = [arb.memes[c[0]].title for c in cands]
        assert "冲我来了" in titles, f"id 重排后仍应正确绑定，实际得到 {titles}"

    def test_visual_fallback_uses_titles(self, arbiter):
        """画面兜底候选不应因 id 漂移而错配"""
        cands = arbiter._build_visual_fallback_candidates(top_k=5)
        for mid, _ in cands:
            assert mid in arbiter.memes


class TestFunnelPrompt:
    """漏斗块体积必须远小于全量清单"""

    def test_funnel_block_smaller_than_full(self, arbiter):
        cands = arbiter._build_scored_candidates("看我单杀带飞", top_k=5)
        funnel = arbiter._build_funnel_options_prompt(cands)
        full = arbiter._cached_options_prompt
        assert len(funnel) < len(full) * 0.5, "漏斗块应显著小于全量清单"

    def test_funnel_block_starts_with_silence_option(self, arbiter):
        cands = arbiter._build_scored_candidates("看我单杀带飞", top_k=5)
        block = arbiter._build_funnel_options_prompt(cands)
        assert block.startswith("0:"), "候选清单必须以『保持沉默』选项开头"

    def test_compact_mode_shorter(self, arbiter):
        cands = arbiter._build_scored_candidates("看我单杀带飞", top_k=5)
        normal = arbiter._build_funnel_options_prompt(cands, compact=False)
        compact = arbiter._build_funnel_options_prompt(cands, compact=True)
        assert len(compact) <= len(normal)


class TestLocalSilence:
    """零命中时不再直接静默，而是送通用兜底候选让 LLM 判断"""

    def test_no_candidate_uses_generic_fallback(self, arbiter):
        """零命中时应回退到通用候选，而非直接拦截（避免该触发却没触发）"""
        cands = arbiter._build_scored_candidates("今天天气不错", top_k=5)
        assert cands == []
        fallback = arbiter._build_generic_fallback_candidates(5)
        assert len(fallback) > 0, "零命中应提供通用兜底候选"

    def test_silent_only_when_configured(self, config, sample_library):
        """默认配置下，零命中也不应静默拦截，应走通用兜底"""
        cfg = deepcopy(config)
        cfg["api"]["mode"] = "heuristic"
        arb = MemeArbiter(cfg, sample_library)

        # 默认 silent_on_no_candidate=False，零命中应回退通用候选（启发式会返回一个相关梗或0）
        result = arb.decide("今天天气不错")
        # 启发式引擎在没有关键词命中时会返回 0（保持沉默），这是兜底行为
        assert result.choice == 0


class TestTriggerSensitivity:
    """触发灵敏度（0-100）应正确控制零命中时的拦截行为"""

    def test_low_sensitivity_silences_locally(self, config, sample_library):
        """灵敏度 ≤33（保守）时，零命中应本地静默、不发请求"""
        cfg = deepcopy(config)
        cfg["behavior"]["trigger_sensitivity"] = 20
        cfg["api"]["mode"] = "heuristic"
        arb = MemeArbiter(cfg, sample_library)
        result = arb.decide("今天天气不错")
        assert result.prompt_sent == ""
        assert "漏斗" in result.engine_used

    def test_high_sensitivity_falls_back_to_candidates(self, config, sample_library):
        """灵敏度 >33 时，零命中应构造兜底候选（不再本地静默）"""
        cfg = deepcopy(config)
        cfg["behavior"]["trigger_sensitivity"] = 80
        cfg["api"]["mode"] = "heuristic"
        arb = MemeArbiter(cfg, sample_library)
        result = arb.decide("今天天气不错")
        # 启发式引擎最终会因无关键词返回 0，但应构造了 prompt（走了兜底候选）
        assert result.prompt_sent, "高灵敏度下应构造兜底 prompt"

    def test_default_sensitivity_is_balanced(self, config, sample_library):
        """默认灵敏度 50 应走均衡档（>33，不静默拦截）"""
        cfg = deepcopy(config)
        cfg["api"]["mode"] = "heuristic"
        arb = MemeArbiter(cfg, sample_library)
        result = arb.decide("今天天气不错")
        assert result.prompt_sent, "默认灵敏度应构造兜底 prompt"


class TestHeuristicFallback:

    def test_heuristic_engine_works(self, config, sample_library):
        cfg = deepcopy(config)
        cfg["api"]["mode"] = "heuristic"
        arb = MemeArbiter(cfg, sample_library)

        result = arb.decide("看我单杀带飞")
        assert result.meme is not None
        assert result.choice > 0

    def test_heuristic_no_match_is_silent(self, config, sample_library):
        cfg = deepcopy(config)
        cfg["api"]["mode"] = "heuristic"
        arb = MemeArbiter(cfg, sample_library)

        result = arb.decide("今天中午吃什么")
        assert result.choice == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
