"""语音服务（R3）：TTS 出去是原生语音条，STT 进来能听懂。

- Telegram 语音条要求 ogg/opus：ffmpeg 转码；无 ffmpeg 时退回原始
  音频文件（显示为文件而非波形条）并告警一次；
- 失败一律返回 None，由对话层自然圆场（"没听清"）。
"""

import asyncio
import shutil
import time
import uuid
from pathlib import Path

from ..log import logger
from ..providers.stt import STT
from ..providers.tts import TTS


class VoiceService:
    def __init__(self, app):
        self.app = app
        self.tts = TTS(app.cfg.tts_type, app.cfg.tts_conf(), app.voice_dir)
        self.stt = STT(app.cfg.stt_type, app.cfg.stt_conf())
        self.ffmpeg = shutil.which("ffmpeg")
        if not self.ffmpeg:
            logger.warning("[loverbot] 未找到 ffmpeg：语音将以音频文件形式发送（非语音条）。")
        self._cleanup()

    @property
    def tts_ready(self) -> bool:
        return self.tts.ready

    @property
    def stt_ready(self) -> bool:
        return self.stt.ready

    # ------------------------------------------------------------------
    async def tts_ogg(self, text: str) -> str | None:
        """合成并转为 ogg/opus，返回文件路径；失败返回 None。"""
        raw = await self.tts.synth(text)
        if raw is None:
            return None
        return await self._to_ogg(raw)

    async def _to_ogg(self, src: str) -> str:
        if not self.ffmpeg or src.lower().endswith(".ogg"):
            return src
        out = self.app.voice_dir / f"{uuid.uuid4().hex}.ogg"
        proc = await asyncio.create_subprocess_exec(
            self.ffmpeg, "-y", "-i", src,
            "-c:a", "libopus", "-b:a", "40k", "-ar", "48000", "-ac", "1",
            str(out),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=60)
        if proc.returncode != 0 or not out.exists():
            logger.warning("[loverbot] ffmpeg 转码失败，退回原始音频。")
            return src
        return str(out)

    # ------------------------------------------------------------------
    async def transcribe(self, audio_path: str) -> str | None:
        """收到的语音条转文字；本地 ogg 先转 wav 提高兼容性。"""
        if not self.stt.ready:
            return None
        source = audio_path
        try:
            if self.ffmpeg and Path(source).exists() and not source.lower().endswith(".wav"):
                wav = self.app.voice_dir / f"{uuid.uuid4().hex}.wav"
                proc = await asyncio.create_subprocess_exec(
                    self.ffmpeg, "-y", "-i", source, "-ar", "16000", "-ac", "1", str(wav),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=60)
                if proc.returncode == 0 and wav.exists():
                    source = str(wav)
            return await self.stt.transcribe(source)
        except Exception as e:
            logger.warning(f"[loverbot] 语音转写失败：{e}")
            return None

    # ------------------------------------------------------------------
    def _cleanup(self, keep_days: int = 7):
        try:
            cutoff = time.time() - keep_days * 86400
            if self.app.voice_dir.exists():
                for p in self.app.voice_dir.iterdir():
                    if p.is_file() and p.stat().st_mtime < cutoff:
                        p.unlink(missing_ok=True)
        except Exception:
            pass
