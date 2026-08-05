"""向量检索：SQLite 存向量 + numpy 余弦相似度。

个人恋人系统的语料量级（记忆几千条、图库几百张）用不上向量数据库；
numpy 点积毫秒级完成，零原生依赖、零外部服务，备份就是复制一个库文件。
Embedding 未配置或失败时优雅降级：available=False，
上层改用"最近优先/标签匹配"等非语义策略，功能不崩。
"""

import json
import uuid

import numpy as np

from ..log import logger

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
    id      TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,           -- memory / gallery
    content TEXT NOT NULL,
    meta    TEXT NOT NULL DEFAULT '{}',
    vec     BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vectors_kind ON vectors (kind);
"""


class Vectors:
    def __init__(self, db, embedder):
        self.db = db
        self.embedder = embedder
        self.available = False
        self._init_failed = False
        self._cache: dict[str, dict] = {}  # kind -> {ids, contents, metas, matrix}

    async def ensure(self) -> bool:
        if self.available:
            return True
        if self._init_failed:
            return False
        if self.embedder is None or not self.embedder.configured:
            self._init_failed = True
            logger.warning("[loverbot] Embedding 未配置，语义检索降级为非语义策略。")
            return False
        try:
            await self.db.conn.executescript(_SCHEMA)
            await self.db.conn.commit()
            self.available = True
            logger.info("[loverbot] 向量检索就绪（numpy 余弦）。")
            return True
        except Exception as e:
            self._init_failed = True
            logger.warning(f"[loverbot] 向量库初始化失败：{e}")
            return False

    # ------------------------------------------------------------------
    async def _insert(self, kind: str, text: str, meta: dict) -> str:
        if not await self.ensure():
            return ""
        try:
            vec = np.asarray(await self.embedder.embed(text), dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec = vec / norm
            vid = str(uuid.uuid4())
            await self.db.execute(
                "INSERT INTO vectors(id, kind, content, meta, vec) VALUES (?,?,?,?,?)",
                (vid, kind, text, json.dumps(meta or {}, ensure_ascii=False), vec.tobytes()),
            )
            self._cache.pop(kind, None)
            return vid
        except Exception as e:
            logger.warning(f"[loverbot] 向量写入失败（{kind}）：{e}")
            return ""

    async def _load_cache(self, kind: str) -> dict:
        cached = self._cache.get(kind)
        if cached is not None:
            return cached
        rows = await self.db.fetchall(
            "SELECT id, content, meta, vec FROM vectors WHERE kind=?", (kind,)
        )
        ids, contents, metas, vecs = [], [], [], []
        for r in rows:
            try:
                v = np.frombuffer(r["vec"], dtype=np.float32)
            except (ValueError, TypeError):
                continue
            ids.append(r["id"])
            contents.append(r["content"])
            try:
                metas.append(json.loads(r["meta"] or "{}"))
            except json.JSONDecodeError:
                metas.append({})
            vecs.append(v)
        matrix = np.vstack(vecs) if vecs else np.zeros((0, 1), dtype=np.float32)
        cached = {"ids": ids, "contents": contents, "metas": metas, "matrix": matrix}
        self._cache[kind] = cached
        return cached

    async def _search(self, kind: str, query: str, k: int, filters: dict | None) -> list[dict]:
        if not await self.ensure():
            return []
        try:
            cache = await self._load_cache(kind)
            if not cache["ids"]:
                return []
            q = np.asarray(await self.embedder.embed(query), dtype=np.float32)
            norm = float(np.linalg.norm(q))
            if norm > 0:
                q = q / norm
            scores = cache["matrix"] @ q  # 余弦相似度（向量已归一化）
            order = np.argsort(-scores)
            out = []
            for idx in order:
                meta = cache["metas"][idx]
                if filters and any(meta.get(fk) != fv for fk, fv in filters.items()):
                    continue
                out.append({
                    "text": cache["contents"][idx],
                    "meta": meta,
                    "similarity": float(scores[idx]),
                })
                if len(out) >= k:
                    break
            return out
        except Exception as e:
            logger.warning(f"[loverbot] 向量检索失败（{kind}）：{e}")
            return []

    # ---- 对外接口（与调用方约定保持稳定）----
    async def add_memory(self, text: str, meta: dict) -> str:
        return await self._insert("memory", text, meta)

    async def search_memory(self, query: str, k: int = 5, filters: dict | None = None) -> list[dict]:
        return await self._search("memory", query, k, filters)

    async def add_gallery(self, text: str, meta: dict) -> str:
        return await self._insert("gallery", text, meta)

    async def search_gallery(self, query: str, k: int = 5, filters: dict | None = None) -> list[dict]:
        return await self._search("gallery", query, k, filters)

    async def delete_gallery_doc(self, vec_id: str):
        if not vec_id:
            return
        try:
            await self.db.execute("DELETE FROM vectors WHERE id=?", (vec_id,))
            self._cache.pop("gallery", None)
        except Exception as e:
            logger.warning(f"[loverbot] 向量删除失败：{e}")

    async def close(self):
        self._cache.clear()
