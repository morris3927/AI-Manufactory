"""Chroma 向量庫封裝。兩個 collection：docs（SOP、法規、指引、故障模式）、cases（維修案例）。

Embedding 由程式自己算好再傳給 Chroma（不依賴 Chroma 內建模型，避免啟動時下載）：
- bge-m3（sentence-transformers，選配，正式使用）
- hash（字元 bigram 雜湊，只供開發與測試；沒裝 sentence-transformers 時的退路）
"""
from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

COLLECTIONS = ("docs", "cases")


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    def __init__(self, dim: int = 512):
        self.dim = dim
        self.name = f"hash-{dim}"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        t = "".join(text.lower().split())
        grams = [t[i:i + 2] for i in range(max(1, len(t) - 1))] + list(t)
        for g in grams:
            h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class SentenceTransformerEmbedder:
    def __init__(self, model: str = "BAAI/bge-m3"):
        self.model_name = model
        self.name = model.split("/")[-1]
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # 延遲載入

            self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model.encode(texts, normalize_embeddings=True).tolist()


def get_embedder(kind: str = "auto", model: str = "BAAI/bge-m3") -> Embedder:
    if kind in ("auto", "bge-m3", "sentence-transformers"):
        try:
            import sentence_transformers  # noqa: F401

            return SentenceTransformerEmbedder(model)
        except ImportError:
            if kind != "auto":
                raise
            log.warning("未安裝 sentence-transformers，知識庫改用 hash embedding（僅供開發）")
    return HashEmbedder()


class VectorStore:
    def __init__(self, path: Path | str, embedder: Embedder):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.embedder = embedder
        self.client = chromadb.PersistentClient(path=str(path), settings=ChromaSettings(anonymized_telemetry=False))

    def collection(self, name: str):
        col = self.client.get_or_create_collection(
            name, metadata={"hnsw:space": "cosine", "embedder": self.embedder.name}, embedding_function=None)
        used = (col.metadata or {}).get("embedder")
        if used and used != self.embedder.name:
            log.warning("collection %s 用 %s 建立，現在是 %s；請執行 make kb 重建", name, used, self.embedder.name)
        return col

    def reset(self) -> None:
        for name in COLLECTIONS:
            try:
                self.client.delete_collection(name)
            except Exception:
                pass

    def upsert(self, name: str, chunks: list[dict[str, Any]]) -> int:
        if not chunks:
            return 0
        col = self.collection(name)
        for i in range(0, len(chunks), 64):
            batch = chunks[i:i + 64]
            col.upsert(ids=[c["chunk_id"] for c in batch], documents=[c["text"] for c in batch],
                       embeddings=self.embedder.embed([c["text"] for c in batch]),
                       metadatas=[flatten_meta(c["metadata"]) for c in batch])
        return len(chunks)

    def query(self, name: str, text: str, k: int = 5, where: dict | None = None) -> list[dict[str, Any]]:
        col = self.collection(name)
        if col.count() == 0:
            return []
        res = col.query(query_embeddings=self.embedder.embed([text]), n_results=min(k, col.count()),
                        where=where or None)
        out = []
        for i, cid in enumerate(res["ids"][0]):
            out.append({"chunk_id": cid, "text": res["documents"][0][i], "metadata": res["metadatas"][0][i],
                        "distance": res["distances"][0][i] if res.get("distances") else None})
        return out

    def count(self, name: str) -> int:
        return self.collection(name).count()


def flatten_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Chroma metadata 只接受純量：list 轉成以 | 包夾的字串（例如 |a|b|），方便包含查詢。"""
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            out[k] = "|" + "|".join(map(str, v)) + "|"
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out
