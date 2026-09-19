import json
import re
import threading
import time
from typing import List, Dict, Optional, Tuple

from core.http_client import (
    CircuitBreaker,
    get_logger,
    get_shared_session,
    post_json_with_retry,
)

logger = get_logger("arbiter")


def parse_usage(usage: dict) -> Tuple[int, int, int, int, str]:
    """
    解析各家接口返回的 usage 字段，提取 Token 用量与前缀缓存命中数。

    返回: (prompt_tokens, completion_tokens, total_tokens, cached_tokens, 说明)

    兼容的字段命名：
      - OpenAI 系     : prompt_tokens / completion_tokens / total_tokens
                        prompt_tokens_details.cached_tokens
      - DeepSeek 系   : prompt_cache_hit_tokens / prompt_cache_miss_tokens
      - Anthropic 系  : input_tokens / output_tokens
                        cache_read_input_tokens / cache_creation_input_tokens
                        prompt_cache_hit_tokens (部分反代转换后)
      - Google Gemini : promptTokenCount / candidatesTokenCount / totalTokenCount
                        cachedContentTokenCount
      - 部分反代      : cache_hit_tokens / cache_read_tokens
    """
    if not isinstance(usage, dict):
        return 0, 0, 0, 0, "接口未返回 usage 字段"

    def pick(*keys, default=0):
        for k in keys:
            v = usage.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return int(v)
        return default

    # 输入 / 输出
    p = pick("prompt_tokens", "input_tokens", "promptTokenCount")
    c = pick("completion_tokens", "output_tokens", "candidatesTokenCount")
    t = pick("total_tokens", "totalTokenCount", default=(p + c))

    # 缓存命中：逐层深入查找
    cached = 0
    note = ""

    # 1) 扁平字段
    cached = pick(
        "prompt_cache_hit_tokens",   # DeepSeek
        "cache_read_input_tokens",   # Anthropic
        "cachedContentTokenCount",   # Gemini
        "cache_hit_tokens",
        "cache_read_tokens",
        "cached_tokens",
    )

    # 2) 嵌套字段
    if cached == 0:
        for parent in ("prompt_tokens_details", "input_tokens_details", "promptTokensDetails"):
            detail = usage.get(parent)
            if isinstance(detail, dict):
                v = detail.get("cached_tokens", 0) or detail.get("cache_read_tokens", 0) or 0
                if isinstance(v, (int, float)) and v > 0:
                    cached = int(v)
                    break

    # 3) Anthropic 的 cache_creation 说明（写了缓存但本次未读取）
    if cached == 0:
        created = pick("cache_creation_input_tokens")
        if created > 0:
            note = f"本次写入缓存 {created} tokens（该渠道不计入命中）"

    if cached > 0:
        note = ""

    return p, c, t, cached, note

class MemeItem:
    def __init__(self, id: int, title: str, filename: str, triggers: str, vibe: str):
        self.id = id
        self.title = title
        self.filename = filename
        self.triggers = triggers
        self.vibe = vibe

class DecisionResult:
    def __init__(
        self,
        choice: int,
        reason: str,
        meme: Optional[MemeItem] = None,
        thinking: str = "",
        prompt_sent: str = "",
        engine_used: str = "",
        latency_ms: float = 0.0,
        raw_response: str = "",
        is_error: bool = False,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cached_tokens: int = 0,
        cache_hit_rate: float = 0.0,
        cache_note: str = ""
    ):
        self.choice = choice          # 0 为保持沉默，>0 为音效 ID
        self.reason = reason
        self.meme = meme
        self.thinking = thinking
        self.prompt_sent = prompt_sent
        self.engine_used = engine_used
        self.latency_ms = latency_ms
        self.raw_response = raw_response
        self.is_error = is_error
        
        # Token 与缓存统计
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.cached_tokens = cached_tokens
        self.cache_hit_rate = cache_hit_rate
        self.cache_note = cache_note

    @property
    def should_play(self) -> bool:
        return self.choice > 0 and self.meme is not None

class MemeArbiter:
    def __init__(self, config: dict, meme_library: List[dict]):
        self.config = config
        self.api_config = config.get("api", {})
        self.behavior_config = config.get("behavior", {})
        
        self.base_url = self.api_config.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
        self.api_key = self.api_config.get("api_key", "")
        self.model = self.api_config.get("model", "deepseek-chat")
        self.temperature = self.api_config.get("temperature", 0.4)
        self.mode = self.api_config.get("mode", "auto") # "auto", "llm", "heuristic"

        # 会话累计 Token 统计（多线程并发更新，用锁保护）
        self._stats_lock = threading.Lock()
        self.session_calls = 0
        self.session_total_tokens = 0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_cached_tokens = 0

        # 网络层：熔断保护
        # 不在此处缓存 Session —— requests.Session 非线程安全，
        # 每次请求时通过 get_shared_session() 取当前线程专属实例。
        self._http_cfg = config.get("http", {})
        self._vision_breaker = CircuitBreaker(
            failure_threshold=int(self._http_cfg.get("breaker_threshold", 5)),
            cooldown_seconds=float(self._http_cfg.get("breaker_cooldown", 30)),
        )
        self._brain_breaker = CircuitBreaker(
            failure_threshold=int(self._http_cfg.get("breaker_threshold", 5)),
            cooldown_seconds=float(self._http_cfg.get("breaker_cooldown", 30)),
        )

        self.memes: Dict[int, MemeItem] = {}
        # 语义漏斗（embedding 召回）。默认不启用，按需开启。
        # 必须先于 reload_memes 初始化，因为 reload_memes 会重建语义索引。
        self._semantic_funnel = None
        self._init_semantic_funnel()
        self.reload_memes(meme_library)

    def _init_semantic_funnel(self):
        """按配置初始化语义漏斗对象（索引在 reload_memes 中构建）"""
        try:
            from core.semantic_funnel import SemanticFunnel
        except ImportError as e:
            logger.warning("语义漏斗模块不可用: %s", e)
            self._semantic_funnel = None
            return

        engine = self.config.get("behavior", {}).get("funnel_engine", "keyword")
        if engine != "semantic":
            self._semantic_funnel = None
            return

        try:
            self._semantic_funnel = SemanticFunnel(self.config)
            logger.info("已启用语义漏斗（embedding 召回）")
        except Exception as e:
            logger.warning("语义漏斗初始化失败，回退关键词漏斗: %s", e)
            self._semantic_funnel = None

    def rebuild_semantic_index(self):
        """梗库变更后重建语义索引"""
        if self._semantic_funnel is not None:
            try:
                self._semantic_funnel.build_index(self.memes, force=True)
            except Exception as e:
                logger.warning("重建语义索引失败: %s", e)

    def stats_snapshot(self) -> Dict[str, int]:
        """线程安全地读取会话统计快照"""
        with self._stats_lock:
            return {
                "calls": self.session_calls,
                "total_tokens": self.session_total_tokens,
                "prompt_tokens": self.session_prompt_tokens,
                "completion_tokens": self.session_completion_tokens,
                "cached_tokens": self.session_cached_tokens,
            }

    def reload_memes(self, meme_library: List[dict]):
        """热重载梗库，使自定义新梗立即生效"""
        self.memes.clear()
        for m in meme_library:
            item = MemeItem(
                id=m["id"],
                title=m["title"],
                filename=m["filename"],
                triggers=m.get("triggers", ""),
                vibe=m.get("vibe", "")
            )
            self.memes[item.id] = item
        self._cached_options_prompt = self._build_options_prompt()
        # 梗库变化后同步语义索引（若启用）
        self.rebuild_semantic_index()

    def _build_options_prompt(self) -> str:
        lines = ["0: [保持沉默] (如果气氛普通、无明显梗点、或时机不合适，务必选 0)"]
        for m in self.memes.values():
            lines.append(f"{m.id}: [{m.title}] - 适用场景: {m.triggers} (情绪/氛围: {m.vibe})")
        return "\n".join(lines)

    def _build_scored_candidates(self, context_str: str, top_k: int = 5) -> List[Tuple[int, float]]:
        """
        第一段：本地粗筛（零 Token 消耗），返回得分最高的 top_k 个候选音效。

        支持两种召回引擎（由 behavior.funnel_engine 切换）：
          - "keyword"  关键词匹配：手写词表 + 自定义梗 triggers 拆词（默认）
          - "semantic" 语义向量召回：按意思相近度排序，可识别同义不同词的表达

        返回 [(meme_id, score), ...]，按得分降序。
        """
        text = context_str.lower()
        if not text.strip():
            return []

        engine = self.config.get("behavior", {}).get("funnel_engine", "keyword")

        # ---------- 语义召回 ----------
        if engine == "semantic" and self._semantic_funnel is not None:
            try:
                hits = self._semantic_funnel.rank(context_str, top_k=top_k)
                if hits:
                    return hits
            except Exception as e:
                logger.warning("语义召回异常，回退关键词召回: %s", e)

        # ---------- 关键词召回（默认 / 语义召回为空时的兜底） ----------
        scores: Dict[int, float] = {}

        def add(mid: int, delta: float):
            if mid in self.memes:
                scores[mid] = scores.get(mid, 0.0) + delta

        # 1) 统一关键词表（高质量手写词，按标题解析实际 id）
        builtin_ids = set()
        for title_key, keywords, _reason, _think in self.KEYWORD_RULES:
            mid = self._resolve_rule_id(title_key)
            if mid is None:
                continue
            builtin_ids.add(mid)
            for k in keywords:
                if k in text:
                    # 词越长越具体，权重越高
                    add(mid, 2.0 + min(len(k), 6) * 0.5)

        # 2) 用户自定义梗的 triggers 拆词（本地规则集以外的条目）
        for m in self.memes.values():
            if m.id in builtin_ids:
                continue
            for k in re.split(r"[、,，/；;\s]+", m.triggers):
                k = k.strip().lower()
                if len(k) >= 2 and k in text:
                    add(m.id, 2.0 + min(len(k), 6) * 0.5)
            for v in re.split(r"[、,，/；;\s]+", m.vibe):
                v = v.strip().lower()
                if len(v) >= 2 and v in text:
                    add(m.id, 1.5)

        # 3) 标题字面命中（用户直接说了梗名），最高权重
        for m in self.memes.values():
            if m.title and m.title.lower() in text:
                add(m.id, 5.0)

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_k]

    def _build_funnel_options_prompt(self, candidates: List[Tuple[int, float]], compact: bool = False) -> str:
        """把粗筛候选编译成精简版候选清单"""
        lines = ["0: [保持沉默] (若无绝佳时机必须选 0)"]
        for mid, sc in candidates:
            m = self.memes.get(mid)
            if not m:
                continue
            if compact:
                # 紧凑模式：截断场景描述，只留前半段核心词
                trig = m.triggers if len(m.triggers) <= 18 else m.triggers[:18]
                lines.append(f"{m.id}: [{m.title}] {trig}")
            else:
                lines.append(f"{m.id}: [{m.title}] - {m.triggers} ({m.vibe})")
        return "\n".join(lines)

    # 画面类场景的通用兜底候选（按标题关键词绑定，避免 id 漂移）
    VISUAL_FALLBACK_KEYS = ["不可能", "乱世", "冲我来了", "当浮一大白", "帝王之征"]

    # 纯文本零命中时的通用兜底候选 —— 覆盖最常用、最百搭的梗，
    # 让 LLM 即使在没有关键词命中时也能凭语义挑一个，避免"该触发却没触发"。
    GENERIC_FALLBACK_KEYS = [
        "不可能", "恭喜爹", "二弟", "乱世", "当浮一大白",
        "放屁", "冲我来了", "知错改错不认错", "煮熟的鸭子", "帝王之征",
    ]

    def _build_visual_fallback_candidates(self, top_k: int) -> List[Tuple[int, float]]:
        """仅凭图片、无数值命中时，给出一组画面向候选"""
        out = []
        total = len(self.VISUAL_FALLBACK_KEYS)
        for i, key in enumerate(self.VISUAL_FALLBACK_KEYS):
            mid = self._resolve_rule_id(key)
            if mid is not None:
                out.append((mid, float(total - i)))
        return out[:max(1, top_k)]

    def _build_generic_fallback_candidates(self, top_k: int) -> List[Tuple[int, float]]:
        """纯文本零命中时，给出一组最常用、最百搭的通用候选"""
        out = []
        total = len(self.GENERIC_FALLBACK_KEYS)
        for i, key in enumerate(self.GENERIC_FALLBACK_KEYS):
            mid = self._resolve_rule_id(key)
            if mid is not None:
                out.append((mid, float(total - i)))
        return out[:max(1, top_k)]

    def _build_visual_fallback_block(self, top_k: int) -> str:
        """画面兜底候选块，体积恒定且远小于全量清单"""
        cands = self._build_visual_fallback_candidates(top_k)
        if cands:
            return self._build_funnel_options_prompt(cands, compact=True)
        lines = ["0: [保持沉默]"]
        for m in list(self.memes.values())[:top_k]:
            lines.append(f"{m.id}: [{m.title}]")
        return "\n".join(lines)

    def resolve_vision_endpoint(self) -> Tuple[str, str, str, str]:
        """
        统一解析视觉模型端点配置
        返回: (url, key, model, 来源标签)
        vision_source:
          - "main"   复用主决策大脑的 API 渠道
          - "custom" 使用独立的第三方视觉模型渠道
        """
        vision_cfg = self.config.get("vision_api", {})
        source = vision_cfg.get("vision_source", "main")
        if source == "custom":
            return (
                vision_cfg.get("base_url", "http://localhost:11434/v1").rstrip("/"),
                vision_cfg.get("api_key", "ollama"),
                vision_cfg.get("model", "moondream"),
                "独立视觉模型"
            )
        return self.base_url, self.api_key, self.model, "复用主大脑"

    def analyze_screen_vision(self, image_base64: str) -> Tuple[str, int, str]:
        """
        专职视觉研判接口：负责看图，提取一句话战局摘要
        返回: (战局一句话描述, 消耗Tokens, 所用视觉模型名称)
        """
        v_url, v_key, v_model, source_label = self.resolve_vision_endpoint()

        v_prompt = (
            "你是游戏战局观察员。观察这张游戏截图，用一句话描述玩家的关键处境，"
            "只关注：是否阵亡、是否被围攻、是否残血、是否刚拿下击杀或胜利、局势是否平淡。"
            "例如：'玩家被多只敌人围攻，血量见底' / '玩家刚拿下五杀' / '战局平淡无交火'。"
            "严格只输出这一句话，不要任何解释。"
        )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {v_key}"
        }

        payload = {
            "model": v_model,
            "messages": [
                {"role": "system", "content": "You are a concise game vision observer. Summarize the game situation in one sentence."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": v_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                    ]
                }
            ],
            "temperature": 0.2,
            "max_tokens": 100
        }

        resp, err = post_json_with_retry(
            f"{v_url}/chat/completions",
            headers=headers,
            payload=payload,
            timeout=float(self._http_cfg.get("vision_timeout", 6.0)),
            max_attempts=int(self._http_cfg.get("vision_retries", 3)),
            breaker=self._vision_breaker,
            session=get_shared_session(),
        )
        if err or resp is None:
            logger.warning("视觉模型请求失败: %s", err)
            return f"[视觉不可用] {err}", 0, v_model

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return content, tokens, v_model
        except Exception as e:
            logger.warning("视觉模型响应解析失败: %s", e)
            return f"[视觉响应异常: {str(e)[:50]}]", 0, v_model

    def _silent_no_candidate(self, start_time: float, context_str: str, prompt_sent: str = "") -> DecisionResult:
        """本地漏斗零命中：直接判定沉默，零 Token 消耗，不发任何请求"""
        latency_ms = (time.time() - start_time) * 1000
        thinking = (
            f"【本地漏斗粗筛】: 未从上下文匹配到任何音效触发词。\n"
            f"【输入切片】: \"{context_str.strip()}\"\n"
            f"【裁定结果】: 跳过远程请求，直接保持沉默 (节省 Token)"
        )
        return DecisionResult(
            choice=0,
            reason="[本地漏斗] 无候选命中，静默跳过",
            meme=None,
            thinking=thinking,
            prompt_sent=prompt_sent,
            engine_used="本地漏斗粗筛 (0 Token)",
            latency_ms=latency_ms
        )

    def is_api_configured(self) -> bool:
        k = self.api_key.strip()
        return bool(k and "YOUR_API_KEY" not in k and len(k) > 8)

    # 统一关键词表：漏斗粗筛与内置规则引擎共用同一份数据源
    # 格式: (标题关键词, [触发词...], 简短结论, 推理说明)
    #
    # 注意：这里用「标题关键词」而不是数字 id 来绑定音效。
    # 因为用户在梗库里删除或新增音效时，id 会被重新连续编号，
    # 数字 id 会漂移，导致规则指错音效；标题则是稳定的。
    KEYWORD_RULES = [
        ("不可能", ["不可能", "怎么可能", "空大", "融化", "抽卡", "沉船", "暴毙", "被打脸", "离谱", "绝杀", "秒了", "直接被秒"], "检测到离谱翻车、死不承认或打脸震惊", "命中关键词 '不可能/空大/暴毙'，语境呈现极端震惊与不认账态势，契合《不可能，绝对不可能》"),
        ("不要愤怒", ["我破防了", "红温了", "气死我了", "心态崩了", "为什么不救我", "搞我心态", "有完没完", "愤怒", "急眼"], "检测到有人破防红温，火上浇油劝架", "捕获到破防愤怒信号，符合损友火上浇油的人设，契合《不要愤怒，愤怒会降低你的智慧》"),
        ("恭喜爹", ["带飞", "吹牛", "无敌", "乱杀", "我c", "飘了", "称帝", "单杀", "看我操作"], "检测到自吹自擂与盲目狂妄，极度反讽捧杀", "玩家发言处于自吹自擂、膨胀装逼期，需要立即进行阴阳怪气捧杀，契合《恭喜爹可以称帝了》"),
        ("乱世", ["阵亡", "死了", "送了", "黑白", "变灰", "背锅", "害了你", "乱世", "大意了", "复活倒计时", "任务失败", "全队覆没", "全军覆没", "倒地", "已阵亡"], "检测到角色死亡或送命，假装惋惜甩锅", "检测到阵亡与角色死亡事件，甩锅给版本乱世，契合《是这个乱世害了你啊》"),
        ("当浮一大白", ["赢了", "胜利", "翻盘", "团灭对面", "爽", "痛快", "干杯", "大胜", "大白", "团战胜利", "取得胜利", "打赢"], "打赢团战或反败为胜，举杯痛饮庆祝", "团队取得阶段性胜利或翻盘，契合豪迈干杯的《当浮一大白》"),
        ("二弟", ["二弟", "兄弟", "义气", "抱大腿", "神仙", "带我"], "兄弟结盟或崇拜神级操作", "检测到吹捧队友神级操作与兄弟义气，契合《我二弟天下无敌》"),
        ("骄兵", ["嚣张", "浪死", "骄兵", "必败", "毒奶", "得意忘形"], "冷眼旁观预言翻车", "检测到对手或队友过度狂妄，冷眼毒奶，契合《已成骄兵而骄兵必败》"),
        ("大斧", ["开打", "大斧", "接团", "干他", "冲锋", "饥渴难耐", "迫不及待"], "摩拳擦掌准备大干一场", "好斗情绪高涨，契合莽夫冲锋《我的大斧早就饥渴难耐了》"),
        ("生死不明", ["残血", "下落不明", "补刀", "跑了吗", "死了吧", "血量见底", "濒死"], "冷酷补刀下定论", "残血追击盖棺定论，契合《生死不明那就是死了》"),
        ("谁的部将", ["谁的部将", "太猛了", "战神", "被秀了", "好强"], "感叹对方战神突脸", "被对面精彩操作折服，契合《这是谁的部将》"),
        ("不怕酸", ["嘴硬", "不怕", "还能打", "硬撑", "小问题"], "死鸭子嘴硬不肯认输", "被揍了之后死撑面子，契合《咱家不怕酸》"),
        ("自刎", ["投降", "自刎", "不玩了", "谢罪", "退游", "自闭"], "犯下滔天大罪绝望破防", "操作失误想要谢罪自绝于人民，契合《自刎归天》"),
        ("换大盏", ["加大力度", "不够嗨", "更猛", "大盏", "继续来"], "局面大好，豪横加码", "气氛热烈要求加大力度，契合《换大盏》"),
        ("不奇怪", ["原来如此", "不奇怪", "难怪", "事出有因"], "离谱原因曝光恍然大悟", "得知离谱原因后恍然大悟，契合《这就不奇怪了》"),
        ("放屁", ["放屁", "胡扯", "鬼扯", "假的", "瞎说", "扯淡", "胡说八道"], "驳斥对手或队友胡说八道", "检测到严重荒谬言论，张飞暴躁怒斥《放屁》"),
        ("什么东西", ["什么东西", "你也配", "算什么", "菜狗", "瞧不起"], "居高临下蔑视挑衅者", "张飞霸气压制对手《你是什么东西啊》"),
        ("冲我来了", ["冲我来了", "救命", "都在打我", "集火我", "切我", "刺客来", "救救救", "全冲我", "围攻", "被包围", "深陷重围", "多只敌人"], "被敌人集火围攻慌乱求救", "曹操惊慌失措大喊《冲我来了》"),
        ("大刀", ["大刀不斩", "虐菜", "好菜", "新手吧", "太菜了", "不斩老幼"], "嘲讽对手水平太差像个老幼菜鸟", "关羽傲视群雄《关某的大刀不斩老幼》"),
        ("万万没有", ["没有此事", "不是我", "我没送", "冤枉", "不知道"], "疯狂否认甩锅装无辜", "司马懿否认三连《万万没有此事啊》"),
        ("呆子", ["呆子", "教条", "攻略", "抄出装", "纸上谈兵"], "嘲讽死板按教程打法的理论大师", "曹操辛辣嘲讽《兵法教出来的都是呆子》"),
        ("帝王之征", ["五杀", "暴走", "救世主", "天秀", "帝王之征", "封神", "神仙操作", "连续击杀"], "队友打出顶级天秀绝境翻盘", "至高无上隆重吹捧《龙，可是帝王之征啊》"),
        ("夏夜", ["安详", "无所谓了", "躺平", "超脱", "凉爽的夏夜", "佛系", "随便吧"], "阵亡后佛系看破生死等复活", "哲理超脱看淡红尘《死是凉爽的夏夜》"),
        ("找死", ["找死", "敢来抓我", "反杀", "还敢追", "送上门", "反打"], "对手残血反扑或敢来挑衅霸气反杀", "司马懿阴狠反打《你找死吗》"),
        ("乱世", ["打野来抓", "来抓我", "抓我", "被抓", "敌人来了", "有人来了"], "敌人来抓、被入侵野区", "被对方针对，契合《是这个乱世害了你啊》"),
        ("生死不明", ["没血了", "快死了", "血条见底", "要死了", "残血", "一丝血", "快没了"], "残血濒死状态", "残血判定，契合《生死不明那就是死了》"),
        ("当浮一大白", ["打得漂亮", "漂亮", "好操作", "秀啊", "牛啊", "666", "牛皮", "完美"], "夸赞好操作、漂亮团战", "团队精彩操作，契合《当浮一大白》"),
        ("不要愤怒", ["救我", "不救", "队友呢", "人在哪", "怎么不来"], "求救无人理、质疑队友不支援", "求助落空破防，契合《不要愤怒，愤怒会降低你的智慧》"),
        ("接着奏乐接着舞", ["接着奏乐", "接着舞", "继续奏乐", "继续浪", "继续嗨", "不要停"], "优势巨大继续浪、气氛正好不要停", "豪横得意尽情享受，契合《接着奏乐接着舞》"),
        ("享受享受", ["打了一辈子仗", "享受享受", "休息一下", "摸鱼", "想回城", "该收手", "贪一波"], "想休息摸鱼、贪图享受、该收手却还想贪", "摆烂理直气壮，契合《我打了一辈子仗就不能享受享受嘛》"),
        ("放肆", ["放肆", "大胆", "以下犯上", "顶撞", "猖狂"], "对方胆大妄为、当众顶撞、以下犯上", "威严怒斥，契合《放肆》"),
        ("搜我的身", ["搜我的身", "砍你的头", "敢搜", "敢动我"], "被质疑盘问、威胁反杀", "色厉内荏威胁，契合《胆敢搜我的身我砍你的头》"),
        ("不认错", ["不认错", "嘴硬", "不肯认错", "死要面子", "明明错了"], "死要面子不肯认错、嘴上不服", "口是心非，契合《知错改错不认错》"),
        ("赖到底", ["赖到底", "敢赖", "耍无赖", "死不承认", "抵赖"], "疯狂甩锅、一赖到底、死不承认", "耍无赖抵赖，契合《敢赖会赖一赖到底》"),
        ("煮熟的鸭子", ["煮熟的鸭子", "飞走了", "稳赢", "翻车", "必杀", "到手的胜利", "眼看赢了"], "稳赢局翻车、到手的胜利飞了", "痛失好局，契合《煮熟的鸭子飞走了》"),
        ("叉出去", ["叉出去", "叉出来", "拖出去", "赶出去", "赶人"], "队友太离谱想赶人、恼羞成怒驱逐", "恼怒驱逐，契合《叉出去》"),
        ("砍了砍了", ["推出去", "砍了砍了", "砍了他", "处决", "严惩"], "暴怒处决、怒不可遏", "暴怒处决，契合《给我推出去砍了砍了》"),
        ("负天下人", ["负天下人", "宁我负人", "休教天下人负我", "背刺", "坑别人"], "抢人头背刺、自私自利、宁我负人", "枭雄自私，契合《宁肯我负天下人》"),
        ("癞皮狗", ["癞皮狗", "养条狗", "废物", "累赘", "不如养"], "队友太废物、嫌弃累赘、失望透顶", "极度嫌弃，契合《还不如养一条癞皮狗》"),
        ("我爱死他了", ["我爱死他", "爱死他了", "阴阳怪气夸", "反讽赞美"], "阴阳怪气夸奖、表面夸实际损", "阴阳怪气反讽，契合《我爱死他了》"),
        ("胡言乱语", ["胡言乱语", "春秋", "满嘴跑火车", "不知所云", "胡编"], "对方胡说八道、不知所云", "讥讽胡扯，契合《春秋胡言乱语》"),
        ("匹夫", ["匹夫", "刘备匹夫", "可恶的人", "咒骂", "咬牙切齿"], "怒骂某人、咬牙切齿咒骂对手", "指名怒骂，契合《刘备匹夫》"),
        ("董卓大笑", ["董卓", "狂笑", "爆笑", "幸灾乐祸", "哈哈哈哈"], "全场爆笑、幸灾乐祸、看对面出丑狂笑", "狂放爆笑，契合《董卓大笑》"),
        ("呼噜", ["呼噜", "挂机", "睡着了", "没动静", "人没了"], "队友挂机睡着、长时间没动静", "挂机睡着，契合《董卓呼噜》"),
        ("透明窟窿", ["透明窟窿", "捅成筛子", "放狠话", "气急败坏", "威胁"], "暴怒威胁、气急败坏放狠话", "暴躁威胁，契合《一万个透明窟窿》"),
        ("鸟人", ["鸟人", "猪队友", "这帮人", "不靠谱", "恨铁不成钢"], "吐槽队友猪队友、恨铁不成钢", "恼怒吐槽，契合《这帮鸟人》"),
        ("鸡毛", ["鸡毛", "给个毛", "不屑", "懒得理", "回绝"], "不屑拒绝、懒得搭理", "不屑回绝，契合《给你个鸡毛》"),
        ("曹贼", ["曹贼", "奸贼", "恶贼", "逆贼", "血海深仇", "连串骂"], "连串怒骂敌人、血海深仇", "怒不可遏连珠炮，契合《曹贼奸贼恶贼逆贼》"),
        ("吃汝肉", ["吃汝肉", "寝汝皮", "生吞活剥", "恨不得", "怒极"], "极度愤怒、恨之入骨", "怒极恨之入骨，契合《吃汝肉》"),
        ("决斗", ["决斗", "单挑", "约架", "solo", "敢不敢"], "下战书单挑、约架solo", "挑衅单挑，契合《敢与我决斗吗》"),
        ("不负天下人", ["我不负天下人", "大义凛然", "仁德", "感人", "宁愿自己吃亏"], "大义凛然、宁愿自己吃亏", "大义仁德，契合《宁肯天下人负我》"),
        ("狂徒", ["狂徒", "狂妄至极", "威震", "怒喝", "霸气侧漏"], "怒斥狂徒、对方狂妄至极", "威猛怒喝，契合《狂徒》"),
        ("五十金", ["五十金", "头颅", "自嘲", "心灰意冷", "不值钱"], "自嘲、心灰意冷、被轻视", "自嘲心冷，契合《我的头颅只值五十金》"),
        ("先帝爷", ["先帝爷", "痛心疾首", "悲愤", "悲壮", "感叹世事"], "悲愤呼喊、痛心疾首", "悲愤痛心，契合《先帝爷》"),
        ("有情有义", ["有情有义", "各个有情有义", "反讽夸", "真够义气"], "阴阳怪气夸人有情有义、反讽", "阴阳反讽，契合《各各有情有义》"),
    ]

    def _resolve_rule_id(self, title_key: str) -> Optional[int]:
        """把规则表里的标题关键词解析成当前梗库中的实际 id（id 会随增删漂移）"""
        # 优先精确包含匹配
        for m in self.memes.values():
            if title_key in m.title:
                return m.id
        return None

    def decide(self, context_str: str, image_base64: Optional[str] = None,
               visual_summary: Optional[str] = None) -> DecisionResult:
        """
        多模态智能仲裁。

        两段式漏斗：
          第一段 — 本地粗筛，零 Token 选出最相关的若干候选
          第二段 — 只把候选送给大模型裁决，Prompt 体积大幅缩小

        Args:
            context_str: 时序上下文（语音 / 事件）
            image_base64: 可选的实机截图（直接送入主模型看图）
            visual_summary: 可选的战局摘要（由独立视觉模型产出，替代传图）
        """
        start_time = time.time()

        # ---------- 第一段：本地粗筛（零 Token） ----------
        funnel_cfg = self.config.get("behavior", {})
        use_funnel = funnel_cfg.get("use_funnel", True)
        top_k = int(funnel_cfg.get("funnel_top_k", 5))

        candidates: List[Tuple[int, float]] = []
        if use_funnel:
            ranking_text = context_str
            if visual_summary:
                ranking_text = f"{context_str}\n{visual_summary}"
            candidates = self._build_scored_candidates(ranking_text, top_k=top_k)

            # 触发灵敏度（0-100）：决定零命中时是否兜底、以及提示词的激进程度。
            #   0~33  保守：零命中直接本地静默（最省 Token，但容易漏）
            #   34~66 均衡（默认）：零命中送通用兜底候选，让 LLM 判断
            #   67~100 激进：零命中送更多候选，提示词更鼓励出手
            sensitivity = int(funnel_cfg.get("trigger_sensitivity", 50))

            silent_on_no_candidate = funnel_cfg.get("silent_on_no_candidate", False)
            if (not candidates and not image_base64 and not visual_summary
                    and self.mode != "llm"
                    and (silent_on_no_candidate or sensitivity <= 33)):
                return self._silent_no_candidate(start_time, context_str, prompt_sent="")

            # 有关键词命中：只送命中候选
            # 零命中时按输入类型送兜底候选，让 LLM 做最终判断
            if not candidates:
                if image_base64 or visual_summary:
                    candidates = self._build_visual_fallback_candidates(top_k)
                else:
                    candidates = self._build_generic_fallback_candidates(top_k)

        # 关键：开启漏斗时永远使用紧凑块，杜绝回退到 1700 字符的全量清单
        if use_funnel and candidates:
            options_block = self._build_funnel_options_prompt(candidates)
        elif use_funnel:
            # 漏斗开启但确实无候选（例如仅图片且未匹配到画面词）：
            # 只给出一个极小的通用候选集，避免全量清单撑爆 Token
            options_block = self._build_visual_fallback_block(top_k)
        else:
            options_block = self._cached_options_prompt

        # ---------- 第二段：构造精简 Prompt ----------
        enable_thinking = funnel_cfg.get("enable_thinking", False)
        sensitivity = int(funnel_cfg.get("trigger_sensitivity", 50))

        if enable_thinking:
            output_spec = (
                '{\n'
                '  "choice": <数字编号>,\n'
                '  "reason": "<一句话极短结论>",\n'
                '  "thinking": "<详细思考过程：1.当前语境分析；2.各候选梗匹配度；3.最终选择原因>"\n'
                '}'
            )
        else:
            output_spec = '{"choice": <数字编号>, "reason": "<一句话极短结论>"}'

        vision_hint = ""
        if visual_summary:
            vision_hint = f"\n【当前战局（由视觉模型观察得出）】：{visual_summary}\n"
        elif image_base64:
            vision_hint = "\n【画面截图】：已附带玩家当前游戏屏幕截图，请结合画面中的角色处境、血量、敌人分布综合判断。\n"

        # 根据灵敏度动态生成"出手倾向"提示
        if sensitivity <= 33:
            stance = "你的原则是「宁缺毋滥」——只在有比较明确的梗点时出手，拿不准就保持沉默，避免话痨。"
        elif sensitivity <= 66:
            stance = "你的原则是「该出手时就出手」——只要出现值得接梗的点就果断选，宁可偶尔冒进一点，也不要漏掉好时机。"
        else:
            stance = "你的原则是「越欢脱越好」——极其活跃，抓住任何一点苗头就接梗，哪怕牵强也要放一个，把气氛炒起来。"

        prompt = f"""你是开黑损友，精通三国梗和网络抽象文化。你的乐趣就是抓住机会阴阳怪气、接梗、搞节目效果。

【你的任务】
根据下面最近的对话与事件，判断是否该播放一个音效。{stance}
{vision_hint}
【最近对话与事件】
{context_str}

【可选音效】
{options_block}

【判断准则】
1. 出现以下任何情况，就应该选对应音效（选 1 个最贴切的）：
   - 有人装逼吹牛、自吹自擂、说"我带飞/我无敌"（捧杀他）
   - 有人被打脸、翻车、暴毙、死了、稳赢局翻盘、空技能（嘲讽他）
   - 有人红温破防、急眼、骂人、心态崩、气急败坏（火上浇油）
   - 有人打出神级操作、五杀、团灭、翻盘胜利（吹捧/庆祝）
   - 有人胡说八道、甩锅、耍赖、嘴硬、不认错（戳穿他）
   - 有人求救、被集火、被围攻、残血濒死（接一句损的）
   - 有人装死、挂机、摆烂、摸鱼（调侃他）
   - 场面很爽、胜利、大优势、值得庆祝（庆祝）
2. 选的时候挑最贴切、最戏剧性的那一个。
3. 只输出一个纯 JSON 对象，不要任何解释、标点或多余文字：
{output_spec}
"""

        # 如果未配置 API Key 或强制设定为本地启发式模式
        if not self.is_api_configured() or self.mode == "heuristic":
            return self._heuristic_decide(context_str, prompt, start_time)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        # 支持多模态图文输入
        if image_base64:
            user_content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }
                }
            ]
            engine_name = f"VLM 多模态视觉 ({self.model})"
        else:
            user_content = prompt
            engine_name = f"LLM 文本 ({self.model})"

        # 输出长度：开启 thinking 时需要更多空间
        max_out = 300 if enable_thinking else 120
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是一个开黑损友玩梗机器人。只输出严格合法的 JSON，格式 {\"choice\": 数字, \"reason\": \"短句\"}。有梗就果断选音效，只有完全无关的闲聊才选 0。"},
                {"role": "user", "content": user_content}
            ],
            "temperature": self.temperature,
            "max_tokens": max_out
        }

        url = f"{self.base_url}/chat/completions"
        resp, err = post_json_with_retry(
            url,
            headers=headers,
            payload=payload,
            timeout=float(self._http_cfg.get("brain_timeout", 7.0)),
            max_attempts=int(self._http_cfg.get("brain_retries", 2)),
            breaker=self._brain_breaker,
            session=get_shared_session(),
        )
        latency_ms = (time.time() - start_time) * 1000

        if err or resp is None:
            logger.warning("主大脑请求失败，回退本地规则: %s", err)
            fallback = self._heuristic_decide(context_str, prompt, start_time)
            fallback.reason = f"[网络异常] 已回退本地引擎: {fallback.reason}"
            fallback.is_error = True
            return fallback

        try:
            data = resp.json()
            msg_obj = data["choices"][0]["message"]
            raw_content = msg_obj.get("content", "").strip()
            reasoning_content = msg_obj.get("reasoning_content", "")

            # 解析 Token 与缓存信息（兼容多家接口的字段命名差异）
            p_tokens, c_tokens, t_tokens, cached_tokens, cache_note = parse_usage(data.get("usage", {}))
            hit_rate = (cached_tokens / p_tokens * 100.0) if p_tokens > 0 else 0.0

            # 累计会话统计（多线程并发调用时需加锁，否则 += 会丢更新）
            with self._stats_lock:
                self.session_calls += 1
                self.session_total_tokens += t_tokens
                self.session_prompt_tokens += p_tokens
                self.session_completion_tokens += c_tokens
                self.session_cached_tokens += cached_tokens

            parsed = self._extract_json(raw_content)
            choice = int(parsed.get("choice", 0))
            reason = str(parsed.get("reason", "无说明"))
            thinking = str(parsed.get("thinking", ""))

            if reasoning_content:
                thinking = f"【DeepSeek 原生推理思考链】:\n{reasoning_content}\n\n【决策总结】:\n{thinking}"

            selected_meme = self.memes.get(choice) if choice > 0 else None
            return DecisionResult(
                choice=choice,
                reason=f"[LLM大脑] {reason}",
                meme=selected_meme,
                thinking=thinking,
                prompt_sent=prompt,
                engine_used=engine_name,
                latency_ms=latency_ms,
                raw_response=raw_content,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=t_tokens,
                cached_tokens=cached_tokens,
                cache_hit_rate=hit_rate,
                cache_note=cache_note
            )

        except Exception as e:
            # 响应解析失败（JSON 结构异常 / 字段缺失）——回退本地规则保证不断线
            logger.warning("主大脑响应解析失败，回退本地规则: %s: %s", type(e).__name__, e)
            fallback = self._heuristic_decide(context_str, prompt, start_time)
            fallback.reason = f"[响应解析异常] 已回退本地引擎: {fallback.reason}"
            fallback.is_error = True
            return fallback

    def _heuristic_decide(self, context_str: str, prompt_sent: str, start_time: float) -> DecisionResult:
        """内置高灵敏度语义规则引擎（零网络依赖），与漏斗共用 KEYWORD_RULES"""
        latency_ms = (time.time() - start_time) * 1000
        text = context_str.lower()

        for title_key, keywords, reason, think_step in self.KEYWORD_RULES:
            meme_id = self._resolve_rule_id(title_key)
            if meme_id is None:
                continue
            matched_keys = [k for k in keywords if k in text]
            if matched_keys:
                meme = self.memes.get(meme_id)
                thinking = (
                    f"【时序输入切片】: \"{context_str.strip()}\"\n"
                    f"【关键词命中】: {matched_keys}\n"
                    f"【意图推导】: {think_step}\n"
                    f"【裁定结果】: 命中音效 [{meme_id}] 《{meme.title}》"
                )
                return DecisionResult(
                    choice=meme_id,
                    reason=f"[内置智能规则] {reason}",
                    meme=meme,
                    thinking=thinking,
                    prompt_sent=prompt_sent,
                    engine_used="内置高敏规则引擎 (离线即玩)",
                    latency_ms=latency_ms
                )

        # 动态扫描用户自定义添加的梗 (根据 triggers 关键词自动匹配)
        rule_ids = set()
        for title_key, _k, _r, _t in self.KEYWORD_RULES:
            rid = self._resolve_rule_id(title_key)
            if rid is not None:
                rule_ids.add(rid)
        for m in self.memes.values():
            if m.id not in rule_ids:
                keys = [k.strip().lower() for k in re.split(r"[,，、\s/]+", m.triggers) if len(k.strip()) >= 2]
                matched_keys = [k for k in keys if k in text]
                if matched_keys:
                    thinking = (
                        f"【时序输入切片】: \"{context_str.strip()}\"\n"
                        f"【命中自定义关键词】: {matched_keys}\n"
                        f"【自定义触发设定】: {m.triggers}\n"
                        f"【裁定结果】: 命中自定义音效 [{m.id}] 《{m.title}》"
                    )
                    return DecisionResult(
                        choice=m.id,
                        reason=f"[自定义规则] 匹配关键词: {', '.join(matched_keys)}",
                        meme=m,
                        thinking=thinking,
                        prompt_sent=prompt_sent,
                        engine_used="用户自定义规则",
                        latency_ms=latency_ms
                    )

        # 兜底：本地规则引擎未命中任何关键词时，保持沉默（交由 LLM 兜底的场景除外）
        thinking = (
            f"【时序输入切片】: \"{context_str.strip()}\"\n"
            f"【意图推导】: 本地关键词表未命中，无明确梗点。\n"
            f"【裁定结果】: 选择 [0] 保持沉默。"
        )
        return DecisionResult(
            choice=0,
            reason="[内置智能规则] 未命中关键词，保持沉默",
            meme=None,
            thinking=thinking,
            prompt_sent=prompt_sent,
            engine_used="内置高敏规则引擎 (离线即玩)",
            latency_ms=latency_ms
        )

    def _extract_json(self, text: str) -> dict:
        text = text.strip()
        text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^```\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\{.*?\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(text)
