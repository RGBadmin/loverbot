"""Embedding：OpenAI 兼容 embeddings 接口。记忆召回与图库检索的地基。"""

import aiohttp


class Embedder:
    def __init__(self, conf: dict):
        self.conf = conf or {}

    @property
    def configured(self) -> bool:
        return bool(self.conf.get("base_url") and self.conf.get("api_key") and self.conf.get("model"))

    async def embed(self, text: str) -> list[float]:
        base = str(self.conf["base_url"]).rstrip("/")
        headers = {"Authorization": f"Bearer {self.conf['api_key']}"}
        payload = {"model": self.conf["model"], "input": [text]}
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{base}/embeddings", json=payload, headers=headers) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                data = await resp.json()
        return data["data"][0]["embedding"]
