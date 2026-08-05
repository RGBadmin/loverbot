"""STT：Whisper 兼容接口（audio/transcriptions）。她听懂你的语音条。"""

from pathlib import Path

import aiohttp

from ..log import logger


class STT:
    def __init__(self, stt_type: str, conf: dict):
        self.type = stt_type
        self.conf = conf or {}

    @property
    def ready(self) -> bool:
        return self.type == "openai" and bool(
            self.conf.get("base_url") and self.conf.get("api_key")
        )

    async def transcribe(self, audio_path: str) -> str | None:
        if not self.ready:
            return None
        p = Path(audio_path)
        if not p.exists():
            return None
        try:
            base = str(self.conf["base_url"]).rstrip("/")
            headers = {"Authorization": f"Bearer {self.conf['api_key']}"}
            form = aiohttp.FormData()
            form.add_field("model", self.conf.get("model", "whisper-1"))
            form.add_field("file", p.read_bytes(), filename=p.name, content_type="audio/wav")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
                async with session.post(
                    f"{base}/audio/transcriptions", data=form, headers=headers
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                    data = await resp.json()
            text = str(data.get("text") or "").strip()
            return text or None
        except Exception as e:
            logger.warning(f"[loverbot] STT 识别失败：{e}")
            return None
