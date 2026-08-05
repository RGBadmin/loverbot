"""App：loverbot 的装配中心与共享门面。

单进程 asyncio：主 bot、导演 bot、心跳、面板四个长生命周期组件，
其余子系统经 App 互相取用。
"""

import asyncio
import shutil
from pathlib import Path

from .actions import ActionExecutor
from .chat.handler import ChatPipeline
from .config import Cfg
from .director.bot import DirectorBot
from .gallery.service import Gallery
from .heart.desire import Desire
from .heart.heartbeat import Heartbeat
from .heart.impulses import Impulses
from .heart.planner import Planner
from .imagegen.base import ImageGen
from .life.clock import Clock
from .life.engine import LifeEngine
from .life.mood import MoodEngine
from .log import logger
from .memory.pipeline import MemoryPipeline
from .memory.working import WorkingMemory
from .panel.server import PanelServer
from .persona.dynamic import DynamicState
from .persona.profile import Profile
from .persona.prompt import build_system_prompt
from .providers.embedding import Embedder
from .providers.llm import LLM
from .store.dao import Dao
from .store.db import Database
from .store.vectors import Vectors
from .tg.channel import ChannelHub
from .tg.mainbot import MainBot
from .tg.reactions import Reactions
from .tg.service import TgService
from .voice.service import VoiceService

_PKG_ROOT = Path(__file__).resolve().parent.parent


class App:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.ready = False

        # 路径
        self.data_dir: Path = cfg.data_dir.resolve()
        self.persona_dir = self.data_dir / "persona"
        self.gallery_dir = self.data_dir / "gallery" / "files"
        self.voice_dir = self.data_dir / "voice"
        self.export_dir = self.data_dir / "exports"

        # 子系统
        self.db: Database | None = None
        self.dao: Dao | None = None
        self.vectors: Vectors | None = None
        self.profile: Profile | None = None
        self.dynamic: DynamicState | None = None
        self.clock: Clock | None = None
        self.llm: LLM | None = None
        self.working: WorkingMemory | None = None
        self.memory: MemoryPipeline | None = None
        self.chat: ChatPipeline | None = None
        self.life: LifeEngine | None = None
        self.mood: MoodEngine | None = None
        self.heart: Heartbeat | None = None
        self.desire: Desire | None = None
        self.planner: Planner | None = None
        self.tgsvc: TgService | None = None
        self.actions: ActionExecutor | None = None
        self.impulses: Impulses | None = None
        self.channel_hub: ChannelHub | None = None
        self.gallery: Gallery | None = None
        self.imagegen: ImageGen | None = None
        self.voice: VoiceService | None = None
        self.tg: MainBot | None = None
        self.reactions: Reactions | None = None
        self.director: DirectorBot | None = None
        self.panel: PanelServer | None = None

        self._caps: set[str] = set()

    # ==================================================================
    # 生命周期
    # ==================================================================
    async def initialize(self):
        for d in (self.persona_dir, self.gallery_dir, self.voice_dir, self.export_dir):
            d.mkdir(parents=True, exist_ok=True)

        # 生命档案：首启复制模板
        profile_path = self.persona_dir / "profile.yaml"
        if not profile_path.exists():
            shutil.copy(_PKG_ROOT / "examples" / "persona.example.yaml", profile_path)
            logger.info(f"[loverbot] 已生成生命档案模板：{profile_path}，请按需编辑。")
        self.profile = Profile.load(profile_path)
        self.dynamic = DynamicState(self.persona_dir / "dynamic.yaml")
        self.dynamic.load()

        # 里程碑：认识/在一起的日子自动登记（A12）
        if self.profile.met_on:
            self.dynamic.add_milestone(self.profile.met_on, "认识纪念日")
        if self.profile.anniversary:
            self.dynamic.add_milestone(self.profile.anniversary, "在一起的纪念日")

        self.clock = Clock(self.cfg.timezone)

        self.db = Database(self.data_dir / "loverbot.db")
        await self.db.open()
        self.dao = Dao(self.db)
        await self._load_overrides()

        self.vectors = Vectors(self.db, Embedder(self.cfg.model_conf("embedding")))
        self.llm = LLM(self.cfg)

        self.working = WorkingMemory(self.dao)
        self.memory = MemoryPipeline(self)
        self.chat = ChatPipeline(self)

        self.life = LifeEngine(self)
        self.mood = MoodEngine(self.dao)
        self.desire = Desire(self)
        self.planner = Planner(self)
        self.heart = Heartbeat(self)

        self.tg = MainBot(self)
        self.tgsvc = TgService(self)
        self.reactions = Reactions(self)
        self.impulses = Impulses(self)
        self.actions = ActionExecutor(self)
        self.channel_hub = ChannelHub(self)

        self.imagegen = ImageGen(self)
        self.gallery = Gallery(self)
        await self.gallery.ingest.scan_dir()

        self.voice = VoiceService(self)
        self.director = DirectorBot(self)
        self.panel = PanelServer(self)

        await self._seed_backstory()
        await self.refresh_capabilities()

        await self.tg.start()
        await self.director.start()
        await self.panel.start()
        self.heart.start()
        self.ready = True
        logger.info("[loverbot] 初始化完成，她醒来了。")

    async def run(self):
        await self.initialize()
        try:
            await asyncio.Event().wait()  # 直到进程收到停止信号
        finally:
            await self.terminate()

    async def terminate(self):
        self.ready = False
        for closer in (
            lambda: self.heart.stop() if self.heart else None,
            lambda: self.panel.stop() if self.panel else None,
            lambda: self.director.stop() if self.director else None,
            lambda: self.tg.stop() if self.tg else None,
            lambda: self.vectors.close() if self.vectors else None,
            lambda: self.db.close() if self.db else None,
        ):
            try:
                result = closer()
                if result is not None:
                    await result
            except Exception:
                logger.warning("[loverbot] 关闭组件时出现异常。", exc_info=True)
        logger.info("[loverbot] 已停止。")

    # ==================================================================
    # 绑定对话（导演 bot /link 管理；她的一切收发都以此为家）
    # ==================================================================
    async def linked_chat(self) -> str:
        return str(await self.dao.kv_get("linked_chat") or "")

    async def set_linked_chat(self, chat_id: str):
        await self.dao.kv_set("linked_chat", str(chat_id))
        logger.info(f"[loverbot] 绑定对话已切换为：{chat_id or '（无）'}")

    # ==================================================================
    # 运行期参数覆盖（导演 bot /config set，只进数据库不改配置文件）
    # ==================================================================
    async def _load_overrides(self):
        overrides = await self.dao.kv_get("cfg_overrides", {}) or {}
        for path, value in overrides.items():
            self.cfg.apply_override(path, value)

    async def set_param(self, path: str, value):
        overrides = await self.dao.kv_get("cfg_overrides", {}) or {}
        overrides[path] = value
        await self.dao.kv_set("cfg_overrides", overrides)
        self.cfg.apply_override(path, value)

    # ==================================================================
    # 能力
    # ==================================================================
    def capabilities(self) -> set[str]:
        return set(self._caps)

    async def refresh_capabilities(self):
        caps: set[str] = set()
        if self.dao is not None:
            stats = await self.dao.gallery_stats()
            if stats.get("sticker/ok"):
                caps.add("sticker")
            has_photos = any(
                k.endswith("/ok") and not k.startswith("sticker/") and v > 0
                for k, v in stats.items()
            )
            if has_photos or (self.imagegen and self.imagegen.available):
                caps.add("photo")
        if self.voice is not None and self.voice.tts_ready:
            caps.add("voice")
        self._caps = caps

    # ==================================================================
    # 提示词组装（被动回复与主动消息共用）
    # ==================================================================
    async def build_master_prompt(self, query_text: str, extra_note: str = "") -> str:
        clock_text = self.clock.describe_now(self.profile.met_on, self.profile.anniversary)
        specials = self.clock.upcoming_specials(
            self.dynamic.milestones, self.profile.birthday, within_days=3
        )
        if specials:
            clock_text += "（" + "；".join(specials) + "）"
        return build_system_prompt(
            self.profile,
            self.dynamic,
            clock_text=clock_text,
            life_text=await self.life_text(),
            mood_text=await self.mood_text(),
            cheatsheet=await self.cheatsheet_text(),
            diaries_text=await self.diaries_text(),
            memories_text=await self.recall_text(query_text),
            events_text=await self.events_text(),
            capabilities=self.capabilities(),
            extra_note=extra_note,
        )

    # ==================================================================
    # 提示词素材
    # ==================================================================
    async def cheatsheet_text(self) -> str:
        row = await self.dao.latest_cheatsheet()
        return row["content"] if row else ""

    async def recall_text(self, query: str) -> str:
        return await self.memory.recall(query)

    async def diaries_text(self) -> str:
        return await self.memory.diaries_text()

    async def events_text(self) -> str:
        rows = await self.dao.unmentioned_events(n=5)
        if not rows:
            return ""
        lines = []
        for r in rows:
            motive = f"（当时的想法：{r['motivation']}）" if r["motivation"] else ""
            lines.append(f"[{r['id']}] {r['description']}{motive}")
        lines.append(
            "这些事他还不知道。想说就自然地讲；不说也行，等他自己发现——发现时要用当时真实的理由回答。"
        )
        return "\n".join(lines)

    async def life_text(self) -> str:
        return await self.life.prompt_text() if self.life else ""

    async def mood_text(self) -> str:
        return await self.mood.prompt_text() if self.mood else ""

    # ==================================================================
    # 跨子系统动作
    # ==================================================================
    async def fix_improvised(self, note: str):
        """A6 编造固化：临场发挥立刻成为永久事实。"""
        fact_id = await self.dao.add_fact("self", note, category="编造固化", source="improvise")
        vec_id = await self.vectors.add_memory(note, {"type": "fact", "fact_id": fact_id})
        if vec_id:
            await self.dao.set_fact_vec(fact_id, vec_id)
        logger.info(f"[loverbot] 编造固化：{note}")

    async def pick_sticker(self, desc: str) -> str | None:
        return await self.gallery.pick_sticker(desc) if self.gallery else None

    async def provide_picture(self, desc: str) -> str | None:
        """R5：情境需求描述 → 图库检索 → 不足降级生图。"""
        return await self.gallery.provide(desc) if self.gallery else None

    async def _seed_backstory(self):
        """身世条目一次性播种进事实层（R1：分条目存放，聊到才取用）。"""
        if await self.dao.kv_get("backstory_seeded"):
            return
        for item in self.profile.backstory:
            fact_id = await self.dao.add_fact("self", item, category="身世", source="init")
            vec_id = await self.vectors.add_memory(item, {"type": "fact", "fact_id": fact_id})
            if vec_id:
                await self.dao.set_fact_vec(fact_id, vec_id)
        await self.dao.kv_set("backstory_seeded", 1)
