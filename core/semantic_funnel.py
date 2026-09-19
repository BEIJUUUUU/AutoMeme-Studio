"""
语义漏斗（Embedding 召回）。

与关键词漏斗的区别：
  关键词漏斗 —— 靠手写词表字面匹配，覆盖不全（「打得漂亮」匹配不到「赢了」）
  语义漏斗   —— 把上下文与每个音效的描述都转成向量，按余弦相似度召回，
                能识别意思相近但用词不同的表达

向量来源有两种：
  1. 远程 embedding 接口（OpenAI 兼容 /embeddings，或 Ollama）
  2. 本地轻量哈希向量（无需联网与模型，作为兜底，近似字符 n-gram 相似度）

音效向量在梗库变更后缓存，避免每次请求都重算。
"""

import hashlib
import math
import re
import threading
from typing import Dict, List, Optional, Tuple

from core.http_client import get_logger, get_shared_session

logger = get_logger("semantic")


def _cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------------------------------
# 本地哈希向量（离线兜底）
# --------------------------------------------------------------------------
def _char_ngrams(text: str, n: int = 2) -> List[str]:
    """提取中文友好的字符 n-gram（无需分词）"""
    t = re.sub(r"\s+", "", text.lower())
    if len(t) < n:
        return [t] if t else []
    return [t[i:i + n] for i in range(len(t) - n + 1)]


def local_hash_embedding(text: str, dim: int = 512) -> List[float]:
    """
    基于字符 n-gram 的哈希向量。

    不依赖任何模型，用来近似「字面重合度」。
    对「打得漂亮」vs「打赢」这类同义不同词的情况无能为力 —— 那种场景需要真实语义向量。
    """
    vec = [0.0] * dim
    grams = _char_ngrams(text, 2) + _char_ngrams(text, 3)
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest()[:8], 16)
        idx = h % dim
        # 用另一个哈希位决定符号，降低碰撞带来的偏差
        sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
        vec[idx] += sign
    return vec


class EmbeddingClient:
    """
    把文本转成向量的客户端。

    重要说明：
      - 只有「真实语义向量」才能解决同义不同词的问题（「打得漂亮」≈「打赢」）。
      - 本地哈希向量只能反映字面重合度，无法识别同义词。
        因此默认不启用本地兜底 —— 宁可让用户明确知道需要配置 embedding 服务，
        也不要用一个效果更差的方案悄悄替代关键词漏斗。
    """

    def __init__(self, config: dict):
        cfg = config.get("embedding", {})
        self.enabled = bool(cfg.get("enabled", False))
        self.base_url = cfg.get("base_url", "").rstrip("/")
        self.api_key = cfg.get("api_key", "")
        self.model = cfg.get("model", "text-embedding-3-small")
        self.timeout = float(cfg.get("timeout", 8.0))
        # 默认 False：本地哈希向量效果不如关键词匹配，不应作为默认兜底
        self.use_local_fallback = bool(cfg.get("local_fallback", False))
        self._remote_ok = bool(self.base_url)
        self.last_error = ""

    @property
    def usable(self) -> bool:
        """是否具备可用的真实语义向量能力"""
        return bool(self.enabled and self.base_url and self._remote_ok)

    def embed(self, text: str) -> List[float]:
        """获取单个文本的向量"""
        if self.enabled and self._remote_ok and self.base_url:
            vec = self._embed_remote([text])
            if vec:
                return vec[0]
            logger.warning("远程 embedding 失败，本次降级为本地向量")
        if not self.use_local_fallback:
            return []
        return local_hash_embedding(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量获取向量（音效库初始化时用，减少请求次数）"""
        if not texts:
            return []
        if self.enabled and self._remote_ok and self.base_url:
            vecs = self._embed_remote(texts)
            if vecs and len(vecs) == len(texts):
                return vecs
            logger.warning("远程批量 embedding 失败")
        if not self.use_local_fallback:
            return [[] for _ in texts]
        return [local_hash_embedding(t) for t in texts]

    def probe(self) -> Tuple[bool, str]:
        """
        探测 embedding 服务是否可用。
        返回 (是否可用, 说明文字)
        """
        if not self.base_url:
            return False, "未配置 embedding 接口地址"
        vecs = self._embed_remote(["连通性测试"])
        if vecs and vecs[0]:
            return True, f"可用（向量维度 {len(vecs[0])}）"
        return False, self.last_error or "接口无响应或返回格式不符"

    def _embed_remote(self, texts: List[str]) -> Optional[List[List[float]]]:
        """调用 OpenAI 兼容的 /embeddings 接口"""
        try:
            url = f"{self.base_url}/embeddings"
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            payload = {"model": self.model, "input": texts}
            resp = get_shared_session().post(
                url, headers=headers, json=payload, timeout=self.timeout)
            if resp.status_code != 200:
                self.last_error = f"HTTP {resp.status_code}: {(resp.text or '')[:120]}"
                logger.warning("embedding %s", self.last_error)
                self._remote_ok = False
                return None
            data = resp.json()
            items = data.get("data", [])
            if not items:
                self.last_error = "响应中没有 data 字段"
                self._remote_ok = False
                return None
            # 按 index 排序，保证与输入顺序一致
            items = sorted(items, key=lambda x: x.get("index", 0))
            return [it.get("embedding", []) for it in items]
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.warning("embedding 请求异常: %s", e)
            self._remote_ok = False
            return None


class SemanticFunnel:
    """
    语义召回漏斗。

    用法:
        funnel = SemanticFunnel(config)
        funnel.build_index(memes)          # 音效库变更后重建索引
        ranked = funnel.rank(context, top_k)
    """

    def __init__(self, config: dict):
        self.client = EmbeddingClient(config)
        self._index: Dict[int, List[float]] = {}
        self._lock = threading.Lock()
        self._signature = ""   # 用于判断索引是否过期
        # 相似度阈值：低于此值视为「不相关」，避免强行召回
        self.min_score = float(config.get("embedding", {}).get("min_score", 0.15))

    @staticmethod
    def _meme_text(m) -> str:
        """把一个音效拼成用于向量化的描述文本"""
        return f"{m.title}。{m.triggers}。{m.vibe}"

    def _signature_of(self, memes: Dict[int, object]) -> str:
        """梗库内容指纹，内容变化时重建索引"""
        parts = [f"{mid}:{self._meme_text(m)}" for mid, m in sorted(memes.items())]
        raw = "|".join(parts)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def build_index(self, memes: Dict[int, object], force: bool = False):
        """构建（或按需重建）音效向量索引"""
        if not memes:
            return
        sig = self._signature_of(memes)
        with self._lock:
            if not force and sig == self._signature and self._index:
                return
            ids = list(memes.keys())
            texts = [self._meme_text(memes[i]) for i in ids]
            vecs = self.client.embed_batch(texts)
            index = {}
            for mid, vec in zip(ids, vecs):
                if vec:
                    index[mid] = vec
            self._index = index
            self._signature = sig
            logger.info("语义漏斗索引已构建：%d 个音效（%s）",
                        len(index), "远程向量" if self.client.enabled else "本地向量")

    def rank(self, context: str, top_k: int = 5) -> List[Tuple[int, float]]:
        """
        按语义相似度召回 top_k 个音效。

        返回 [(meme_id, 相似度), ...]，已过滤掉低于阈值的项。
        若 embedding 不可用或索引为空，返回空列表（调用方会回退关键词召回）。
        """
        if not context.strip():
            return []
        with self._lock:
            index = dict(self._index)
        if not index:
            return []

        qvec = self.client.embed(context)
        if not qvec:
            return []

        scored = [(mid, _cosine(qvec, vec)) for mid, vec in index.items()]
        scored = [x for x in scored if x[1] >= self.min_score]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def health(self) -> Tuple[bool, str]:
        """
        自检：语义漏斗当前是否真的可用。
        返回 (是否可用, 说明)
        """
        if not self.client.enabled:
            return False, "未启用 embedding（配置 embedding.enabled = true）"
        ok, msg = self.client.probe()
        if not ok:
            return False, f"embedding 服务不可用：{msg}"
        with self._lock:
            n = len(self._index)
        if n == 0:
            return False, "音效向量索引为空"
        if self.client.use_local_fallback and not self.client._remote_ok:
            return False, "正在使用本地哈希向量（无法识别同义词，建议改用真实 embedding 服务）"
        return True, f"正常（{n} 个音效已向量化）"
