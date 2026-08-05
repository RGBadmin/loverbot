"""主 bot：她本人的 Telegram 接入（python-telegram-bot 轮询）。

自持轮询订阅全部更新类型——包括 message_reaction，
所以"你给她的消息/动态点的表情回应她知道"（A2 反应回流）在这里成立。

路由策略：
- 绑定对话（导演 bot /link 指定）→ 对话管线，她全权接管；
- 未绑定时管理员私聊自动绑定；绑定在别处时给管理员每日一次提示；
- 陌生人私聊礼貌拒绝（她是专一的）；
- 频道讨论组 → 评论区互动；其余一概忽略。
"""

import time
import uuid

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, MessageReactionHandler, filters

from ..log import logger


class MainBot:
    def __init__(self, app):
        self.app = app
        self.application = None
        self.me = None

    @property
    def bot(self):
        return self.application.bot if self.application else None

    # ------------------------------------------------------------------
    async def start(self):
        builder = ApplicationBuilder().token(self.app.cfg.main_token)
        if self.app.cfg.proxy:
            builder = builder.proxy(self.app.cfg.proxy).get_updates_proxy(self.app.cfg.proxy)
        self.application = builder.build()
        self.application.add_handler(MessageHandler(filters.ALL, self._on_message))
        self.application.add_handler(MessageReactionHandler(self._on_reaction))

        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )
        self.me = await self.application.bot.get_me()
        logger.info(f"[loverbot] 主 bot 已上线：@{self.me.username}")

    async def stop(self):
        if self.application is None:
            return
        try:
            if self.application.updater and self.application.updater.running:
                await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
        except Exception:
            logger.warning("[loverbot] 主 bot 停止时出现异常。", exc_info=True)
        finally:
            self.application = None

    # ------------------------------------------------------------------
    # 消息路由
    # ------------------------------------------------------------------
    async def _on_message(self, update, context):
        app = self.app
        msg = update.effective_message
        if msg is None or msg.chat is None:
            return
        try:
            chat = msg.chat
            cid = str(chat.id)

            # 频道帖（她自己发的）不处理
            if chat.type == "channel":
                return
            sender = msg.from_user
            if sender is None or sender.is_bot:
                return

            # 登记见过的对话（导演 bot /chats 数据源）
            title = chat.title or " ".join(
                x for x in (sender.first_name, sender.last_name) if x
            ) or (sender.username or "")
            await app.dao.touch_chat(cid, chat.type, title)

            bound = await app.linked_chat()

            if chat.type == "private":
                is_admin = str(sender.id) == app.cfg.admin_id
                if bound and cid == bound:
                    await app.chat.on_partner_message(msg)
                elif is_admin and not bound:
                    await app.set_linked_chat(cid)
                    await app.chat.on_partner_message(msg)
                elif is_admin:
                    await self._elsewhere_notice(cid, bound)
                else:
                    await self._stranger_brushoff(msg)
                return

            # 群聊：绑定对话是这个群 → 对话管线；讨论组 → 评论区；其余忽略
            if bound and cid == bound:
                await app.chat.on_partner_message(msg)
                return
            if app.cfg.discussion_group_id and cid == app.cfg.discussion_group_id:
                await app.channel_hub.on_group_message(msg)
        except Exception:
            logger.error("[loverbot] 消息处理异常：", exc_info=True)

    async def _on_reaction(self, update, context):
        try:
            await self.app.reactions.on_update(update)
        except Exception:
            logger.error("[loverbot] 表情回应处理异常：", exc_info=True)

    # ------------------------------------------------------------------
    async def _elsewhere_notice(self, chat_id: str, bound: str):
        key = f"elsewhere_notice:{self.app.clock.today_str()}"
        if await self.app.dao.kv_get(key):
            return
        await self.app.dao.kv_set(key, 1)
        await self.bot.send_message(
            chat_id=int(chat_id),
            text=f"（她现在生活在另一个对话里：{bound}。去导演 bot 用 /chats 查看、/link 切换回来。）",
        )

    async def _stranger_brushoff(self, msg):
        app = self.app
        key = f"stranger_replied:{msg.from_user.id}:{time.strftime('%Y-%m-%d')}"
        if await app.dao.kv_get(key):
            return
        await app.dao.kv_set(key, 1)
        name = app.profile.nickname
        await msg.reply_text(f"你好呀，我是{name}。不过我只和一个人聊天哦，拜拜～")

    # ------------------------------------------------------------------
    # 媒体下载（进对话管线前的原料处理）
    # ------------------------------------------------------------------
    async def download_media(self, tg_file_obj) -> str | None:
        """把 Telegram 文件对象下载到临时目录，返回本地路径。"""
        try:
            tmp_dir = self.app.data_dir / "tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            self._cleanup_tmp(tmp_dir)
            file = await tg_file_obj.get_file()
            suffix = ""
            if file.file_path and "." in file.file_path:
                suffix = "." + file.file_path.rsplit(".", 1)[-1]
            target = tmp_dir / f"{uuid.uuid4().hex}{suffix}"
            await file.download_to_drive(custom_path=str(target))
            return str(target)
        except Exception as e:
            logger.warning(f"[loverbot] 媒体下载失败：{e}")
            return None

    @staticmethod
    def _cleanup_tmp(tmp_dir, keep_hours: int = 24):
        cutoff = time.time() - keep_hours * 3600
        try:
            for p in tmp_dir.iterdir():
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
        except Exception:
            pass
