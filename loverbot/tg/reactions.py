"""表情回应感知（A2 反应回流）：你点的 ❤️ 她知道，会开心，会提起。

- 绑定对话里的回应：记录"他给你的消息点了 {emoji}"事件 + 一点点开心；
- 频道动态的回应：具名回应记具体的人（管理员点的最重要），
  匿名聚合（message_reaction_count）只在数量增加时记一次。
事件进入事件流后，自然成为她的话题素材与主动分享的理由。
"""

from ..log import logger


def _emojis(reactions) -> list[str]:
    out = []
    for r in reactions or []:
        emoji = getattr(r, "emoji", None)
        if emoji:
            out.append(str(emoji))
        elif getattr(r, "custom_emoji_id", None):
            out.append("（自定义表情）")
    return out


class Reactions:
    def __init__(self, app):
        self.app = app

    async def on_update(self, update):
        mr = getattr(update, "message_reaction", None)
        if mr is not None:
            await self._on_named(mr)
            return
        mrc = getattr(update, "message_reaction_count", None)
        if mrc is not None:
            await self._on_count(mrc)

    # ------------------------------------------------------------------
    async def _on_named(self, mr):
        """具名回应：某个人对某条消息改了表情。"""
        app = self.app
        user = getattr(mr, "user", None)
        if user is None or user.is_bot:
            return
        new = _emojis(getattr(mr, "new_reaction", None))
        if not new:
            return  # 撤销回应不惊动她
        emoji = new[0]
        cid = str(mr.chat.id)
        is_admin = str(user.id) == app.cfg.admin_id

        if cid == await app.linked_chat() and is_admin:
            await app.dao.add_event(
                "interaction",
                f"他给你的消息点了 {emoji}",
                motivation="",
                meta={"emoji": emoji, "message_id": mr.message_id},
            )
            if app.mood:
                await app.mood.add("happy", 0.4, f"他点了个{emoji}", half_life_min=180)
            logger.info(f"[loverbot] 他点了 {emoji}，她记下了。")
        elif app.cfg.channel_id and self._is_channel(mr.chat):
            who = "他" if is_admin else "有网友"
            await app.dao.add_event(
                "interaction",
                f"{who}给你的动态点了 {emoji}",
                motivation="",
                meta={"emoji": emoji, "message_id": mr.message_id, "channel": True},
            )
            if is_admin and app.mood:
                await app.mood.add("happy", 0.5, f"他给动态点了{emoji}", half_life_min=240)

    async def _on_count(self, mrc):
        """匿名聚合（频道常见）：只在数量增长时记一次，避免刷屏。"""
        app = self.app
        if not app.cfg.channel_id or not self._is_channel(mrc.chat):
            return
        total = sum(getattr(rc, "total_count", 0) for rc in (mrc.reactions or []))
        key = f"reaction_count:{mrc.message_id}"
        last = await app.dao.kv_get(key, 0) or 0
        if total <= last:
            await app.dao.kv_set(key, total)
            return
        await app.dao.kv_set(key, total)
        emo = "、".join(
            f"{_emojis([getattr(rc, 'type', None)])[0]}×{rc.total_count}"
            for rc in (mrc.reactions or [])
            if _emojis([getattr(rc, "type", None)])
        )
        await app.dao.add_event(
            "interaction",
            f"你的动态收到了 {total} 个表情回应" + (f"（{emo}）" if emo else ""),
            motivation="",
            meta={"message_id": mrc.message_id, "total": total, "channel": True},
        )

    def _is_channel(self, chat) -> bool:
        raw = self.app.cfg.channel_id
        if not raw:
            return False
        if raw.startswith("@"):
            return str(getattr(chat, "username", "") or "").lower() == raw[1:].lower()
        return str(chat.id) == raw
