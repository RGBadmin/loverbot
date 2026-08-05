"""TTS 后端：openai 兼容 / GPT-SoVITS(api_v2) / Fish Audio。

产出任意格式音频文件路径；转 ogg/opus 语音条由 voice.service 负责。
引擎可替换不锁定（需求 R3）：换后端只改配置 tts.type。
"""

import time
import uuid
from pathlib import Path

import aiohttp

from ..log import logger


class TTS:
    def __init__(self, tts_type: str, conf: dict, work_dir: Path):
        self.type = tts_type
        self.conf = conf or {}
        self.work_dir = work_dir

    @property
    def ready(self) -> bool:
        if self.type == "openai":
            return bool(self.conf.get("base_url") and self.conf.get("api_key"))
        if self.type == "gpt-sovits":
            return bool(self.conf.get("base_url") and self.conf.get("ref_audio"))
        if self.type == "fishaudio":
            return bool(self.conf.get("api_key"))
        return False

    async def synth(self, text: str) -> str | None:
        if not self.ready or not text.strip():
            return None
        try:
            if self.type == "openai":
                data, ext = await self._openai(text)
            elif self.type == "gpt-sovits":
                data, ext = await self._gpt_sovits(text)
            elif self.type == "fishaudio":
                data, ext = await self._fishaudio(text)
            else:
                return None
            self.work_dir.mkdir(parents=True, exist_ok=True)
            out = self.work_dir / f"tts_{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"
            out.write_bytes(data)
            return str(out)
        except Exception as e:
            logger.warning(f"[loverbot] TTS 合成失败（{self.type}）：{e}")
            return None

    # ------------------------------------------------------------------
    async def _openai(self, text: str) -> tuple[bytes, str]:
        base = str(self.conf["base_url"]).rstrip("/")
        payload = {
            "model": self.conf.get("model", "tts-1"),
            "voice": self.conf.get("voice", "alloy"),
            "input": text,
            "response_format": "mp3",
        }
        headers = {"Authorization": f"Bearer {self.conf['api_key']}"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            async with session.post(f"{base}/audio/speech", json=payload, headers=headers) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                return await resp.read(), "mp3"

    async def _gpt_sovits(self, text: str) -> tuple[bytes, str]:
        """GPT-SoVITS api_v2：POST /tts。"""
        base = str(self.conf["base_url"]).rstrip("/")
        payload = {
            "text": text,
            "text_lang": self.conf.get("text_lang", "zh"),
            "ref_audio_path": self.conf["ref_audio"],
            "prompt_text": self.conf.get("prompt_text", ""),
            "prompt_lang": self.conf.get("prompt_lang", "zh"),
            "media_type": "wav",
            "streaming_mode": False,
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
            async with session.post(f"{base}/tts", json=payload) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                return await resp.read(), "wav"

    async def _fishaudio(self, text: str) -> tuple[bytes, str]:
        payload = {
            "text": text,
            "reference_id": self.conf.get("reference_id") or None,
            "format": "mp3",
        }
        headers = {"Authorization": f"Bearer {self.conf['api_key']}"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            async with session.post(
                "https://api.fish.audio/v1/tts", json=payload, headers=headers
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                return await resp.read(), "mp3"
