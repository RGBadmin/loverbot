"""主对话管线：绑定对话里的消息，被她完整接管。

节奏设计：
- 短时间内连发的多条消息合并为一轮（真人不会每条都单独回）；
- 回复分段发送，段间有"打字中"与拟真延迟；
- 语音/表情包/照片等能力缺席时自动降级，永不报错给对方。
"""

import asyncio
import time

from ..log import logger
from .composer import parse_reply
from .delivery import Deliverer

_DEBOUNCE_SECONDS = 2.2


class ChatPipeline:
    def __init__(self, app):
        self.app = app
        self._buffer: list[dict] = []
        self._worker: asyncio.Task | None = None
        self._turn_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 入口（msg: telegram.Message，已由 MainBot 确认来自绑定对话）
    # ------------------------------------------------------------------
    async def on_partner_message(self, msg):
        app = self.app
        await app.dao.kv_set("last_user_ts", int(time.time()))
        await app.dao.kv_set("proactive_unanswered", 0)

        piece = await self._extract(msg)
        if piece is None:
            return
        self._buffer.append(piece)

        # 入库（工作记忆）+ 情绪响应（P1：他开口，负面情绪消散）
        await app.working.log_user(piece["text"], kind=piece["kind"])
        if app.mood:
            await app.mood.on_user_message(piece["text"])

        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._debounced_turn())

    # ------------------------------------------------------------------
    # 合并短时间内的连发消息，然后跑一轮对话
    # ------------------------------------------------------------------
    async def _debounced_turn(self):
        try:
            await asyncio.sleep(_DEBOUNCE_SECONDS)
            while self._buffer:
                async with self._turn_lock:
                    pieces, self._buffer = self._buffer, []
                    try:
                        await self._run_turn(pieces)
                    except Exception:
                        logger.error("[loverbot] 对话轮异常：", exc_info=True)
                        await self._safe_send_text("呜，我脑子突然卡了一下…你刚说什么？")
                if self._buffer:
                    await asyncio.sleep(_DEBOUNCE_SECONDS)
        finally:
            self._worker = None

    async def _run_turn(self, pieces: list[dict]):
        app = self.app
        chat_id = await app.linked_chat()
        if not chat_id:
            return
        images: list[str] = []
        for p in pieces:
            images.extend(p.get("images", []))

        # 回复节奏（A4）：睡着/忙碌时晚一点回，醒来/回来自有交代
        if app.life:
            extra = await app.life.pre_reply_delay()
            if extra > 0:
                await asyncio.sleep(extra)

        contexts = await app.working.contexts()
        prompt = None
        if contexts and contexts[-1]["role"] == "user":
            prompt = contexts.pop()["content"]
        if not prompt:
            prompt = "\n".join(p["text"] for p in pieces) or "（无内容）"

        system_prompt = await app.build_master_prompt(prompt)

        try:
            raw = await app.llm.chat(
                prompt=prompt,
                contexts=contexts,
                system_prompt=system_prompt,
                image_urls=images or None,
            )
        except Exception as e:
            if images:  # 多模态失败时退回纯文本再试一次
                logger.warning(f"[loverbot] 带图对话失败，退回纯文本重试：{e}")
                raw = await app.llm.chat(
                    prompt=prompt + "\n（他刚发了图片，但你手机加载不出来）",
                    contexts=contexts,
                    system_prompt=system_prompt,
                )
            else:
                raise

        parsed = parse_reply(raw)
        await Deliverer(app, chat_id).deliver(parsed)
        await self._post_turn(parsed)

    # ------------------------------------------------------------------
    # 轮后处理：编造固化、事件提及状态、沉淀标记
    # ------------------------------------------------------------------
    async def _post_turn(self, parsed):
        app = self.app
        for note in parsed.improvs:
            await app.fix_improvised(note)
        for eid in parsed.told_events:
            await app.dao.set_event_mention(eid, "told")
        for eid in parsed.found_events:
            await app.dao.set_event_mention(eid, "discovered")
        await app.dao.kv_set("memory_dirty", 1)  # 心跳空闲时做记忆沉淀

    async def _safe_send_text(self, text: str):
        try:
            chat_id = await self.app.linked_chat()
            if chat_id:
                await Deliverer(self.app, chat_id).send_text(text)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 消息内容提取（telegram.Message → 文本/图片/语音）
    # ------------------------------------------------------------------
    async def _extract(self, msg) -> dict | None:
        app = self.app
        texts: list[str] = []
        images: list[str] = []
        kind = "text"

        if msg.text:
            texts.append(msg.text.strip())
        if msg.caption:
            texts.append(msg.caption.strip())

        if msg.voice or msg.audio:
            kind = "voice"
            media = msg.voice or msg.audio
            path = await app.tg.download_media(media)
            stt_text = await app.voice.transcribe(path) if (path and app.voice) else None
            texts.append(stt_text if stt_text else "[发来一条语音，但你这边没加载出来，没听清]")

        if msg.photo:
            if kind == "text":
                kind = "photo"
            path = await app.tg.download_media(msg.photo[-1])
            if path:
                images.append(path)

        if msg.sticker and not msg.sticker.is_animated and not msg.sticker.is_video:
            kind = "sticker"
            path = await app.tg.download_media(msg.sticker)
            if path:
                images.append(path)
                texts.append("[发来一个表情包]")

        text = "\n".join(t for t in texts if t).strip()
        if not text and not images:
            return None
        if not text and images:
            text = "[发来一张图片]"
        return {"text": text, "images": images, "kind": kind}
