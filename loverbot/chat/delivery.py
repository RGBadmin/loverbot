"""统一投递器：被动回复与主动消息共用的"她说话的样子"。

无论消息由对话管线、心跳还是导演编排发起，分段节奏、
语音/表情包/照片的降级策略都一致——她只有一种说话方式。
"""

import asyncio

from ..log import logger
from .composer import ParsedReply, typing_delay


class Deliverer:
    def __init__(self, app, chat_id: int | str):
        self.app = app
        self.chat_id = int(chat_id)

    def _bot(self):
        return self.app.tg.bot if self.app.tg else None

    async def deliver(self, parsed: ParsedReply):
        app = self.app
        for seg in parsed.segments:
            if app.tgsvc:
                await app.tgsvc.typing(self.chat_id)
            await asyncio.sleep(typing_delay(seg.text))
            try:
                if seg.type == "voice":
                    await self._voice(seg.text)
                elif seg.type == "sticker":
                    await self._sticker(seg.text)
                elif seg.type == "photo":
                    await self._photo(seg.text)
                else:
                    await self.send_text(seg.text)
                    await app.working.log_her(seg.text)
            except Exception:
                logger.error(f"[loverbot] 段投递失败（{seg.type}）：", exc_info=True)

    # ------------------------------------------------------------------
    async def send_text(self, text: str):
        await self._bot().send_message(chat_id=self.chat_id, text=text)

    async def send_photo(self, path: str):
        with open(path, "rb") as f:
            await self._bot().send_photo(chat_id=self.chat_id, photo=f)

    async def send_voice(self, ogg_path: str) -> bool:
        try:
            with open(ogg_path, "rb") as f:
                await self._bot().send_voice(chat_id=self.chat_id, voice=f)
            return True
        except Exception as e:
            logger.warning(f"[loverbot] 语音条发送失败：{e}")
            return False

    # ------------------------------------------------------------------
    async def _voice(self, text: str):
        app = self.app
        ogg = await app.voice.tts_ogg(text) if app.voice else None
        if ogg and await self.send_voice(ogg):
            await app.working.log_her(text, kind="voice")
        else:  # 语音不可用 → 文字照发
            await self.send_text(text)
            await app.working.log_her(text)

    async def _sticker(self, desc: str):
        app = self.app
        path = await app.pick_sticker(desc)
        if path:
            await self.send_photo(path)
            await app.working.log_her(desc, kind="sticker")
        # 没有合适的就不发——没有比发错表情包更穿帮的事

    async def _photo(self, desc: str):
        app = self.app
        path = await app.provide_picture(desc)
        if path:
            await self.send_photo(path)
            await app.working.log_her(desc, kind="photo")
        else:
            fallback = "拍好的照片居然发不出去，气。回头再给你看！"
            await self.send_text(fallback)
            await app.working.log_her(fallback)
