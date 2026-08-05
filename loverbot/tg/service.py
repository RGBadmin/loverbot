"""她的 Telegram 原生动作（R2）：换头像、改签名、频道发帖、线程回复。"""

from pathlib import Path

from ..log import logger


class TgService:
    def __init__(self, app):
        self.app = app

    def _bot(self):
        return self.app.tg.bot if self.app.tg else None

    def channel_chat(self) -> str | int | None:
        raw = self.app.cfg.channel_id
        if not raw:
            return None
        if raw.startswith("@"):
            return raw
        try:
            return int(raw)
        except ValueError:
            return raw

    # ------------------------------------------------------------------
    # 资料页（R2）
    # ------------------------------------------------------------------
    async def set_avatar(self, image_path: str) -> bool:
        bot = self._bot()
        if bot is None:
            return False
        try:
            from telegram import InputProfilePhotoStatic

            with open(image_path, "rb") as f:
                await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(f.read()))
            return True
        except Exception as e:
            logger.warning(f"[loverbot] 换头像失败：{e}")
            return False

    async def set_signature(self, text: str) -> bool:
        bot = self._bot()
        if bot is None:
            return False
        ok = False
        try:
            await bot.set_my_short_description(short_description=text[:70])
            ok = True
        except Exception as e:
            logger.warning(f"[loverbot] 改简介（short）失败：{e}")
        try:
            await bot.set_my_description(description=text[:512])
            ok = True
        except Exception as e:
            logger.debug(f"[loverbot] 改简介（long）失败：{e}")
        return ok

    # ------------------------------------------------------------------
    # 频道（R2：纯文字/单图/相册）
    # ------------------------------------------------------------------
    async def post_channel(self, text: str, image_paths: list[str] | None = None) -> list[int]:
        bot = self._bot()
        chat = self.channel_chat()
        if bot is None or chat is None:
            return []
        image_paths = [p for p in (image_paths or []) if p and Path(p).exists()]
        try:
            if not image_paths:
                msg = await bot.send_message(chat_id=chat, text=text)
                return [msg.message_id]
            if len(image_paths) == 1:
                with open(image_paths[0], "rb") as f:
                    msg = await bot.send_photo(chat_id=chat, photo=f, caption=text[:1024] or None)
                return [msg.message_id]
            from telegram import InputMediaPhoto

            media = []
            for i, p in enumerate(image_paths[:10]):
                with open(p, "rb") as f:
                    data = f.read()
                media.append(InputMediaPhoto(data, caption=text[:1024] if i == 0 else None))
            msgs = await bot.send_media_group(chat_id=chat, media=media)
            return [m.message_id for m in msgs]
        except Exception as e:
            logger.warning(f"[loverbot] 频道发帖失败：{e}")
            return []

    # ------------------------------------------------------------------
    async def reply_in_group(self, group_chat_id: int | str, text: str, reply_to_message_id: int) -> bool:
        bot = self._bot()
        if bot is None:
            return False
        try:
            await bot.send_message(
                chat_id=int(group_chat_id),
                text=text,
                reply_to_message_id=reply_to_message_id,
            )
            return True
        except Exception as e:
            logger.warning(f"[loverbot] 评论回复失败：{e}")
            return False

    async def typing(self, chat_id: int | str):
        bot = self._bot()
        if bot is None:
            return
        try:
            await bot.send_chat_action(chat_id=int(chat_id), action="typing")
        except Exception:
            pass
