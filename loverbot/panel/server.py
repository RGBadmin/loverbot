"""Web 管理面板（系统性需求·双控制台）。

aiohttp 自持服务：静态 SPA + /api/*。
所有请求须携带 Bearer token（config: panel.token）——面板等同导演权限，
未设置 token 则整个面板停用；切勿把端口暴露公网。
"""

import asyncio
import base64
import time
from pathlib import Path

import yaml
from aiohttp import web

from ..log import logger
from ..persona.profile import Profile

_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


class PanelServer:
    def __init__(self, app):
        self.app = app
        self.runner: web.AppRunner | None = None
        self._tagall_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    async def start(self):
        if not self.app.cfg.panel_token:
            logger.info("[loverbot] 未设置 panel.token，Web 面板停用。")
            return
        try:
            webapp = web.Application(middlewares=[self._auth_middleware], client_max_size=64 * 1024 * 1024)
            self._routes(webapp)
            self.runner = web.AppRunner(webapp)
            await self.runner.setup()
            site = web.TCPSite(self.runner, self.app.cfg.panel_host, self.app.cfg.panel_port)
            await site.start()
            logger.info(
                f"[loverbot] Web 面板已启动：http://{self.app.cfg.panel_host}:{self.app.cfg.panel_port}"
            )
        except Exception:
            logger.error("[loverbot] Web 面板启动失败：", exc_info=True)
            self.runner = None

    async def stop(self):
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None

    # ------------------------------------------------------------------
    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if not request.path.startswith("/api/"):
            return await handler(request)  # 静态页面本身不含数据
        token = ""
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        token = token or request.query.get("token", "")
        if token != self.app.cfg.panel_token:
            return web.json_response({"message": "unauthorized"}, status=401)
        return await handler(request)

    def _routes(self, webapp: web.Application):
        r = webapp.router
        r.add_get("/", self._index)
        r.add_get("/api/overview", self.overview)
        r.add_get("/api/profile", self.get_profile)
        r.add_post("/api/profile/save", self.save_profile)
        r.add_get("/api/diaries", self.diaries)
        r.add_get("/api/facts", self.facts)
        r.add_get("/api/cheatsheet", self.cheatsheet)
        r.add_get("/api/chatlog", self.chatlog)
        r.add_get("/api/events", self.events)
        r.add_get("/api/pending", self.pending)
        r.add_post("/api/pending/cancel", self.pending_cancel)
        r.add_post("/api/action", self.run_action)
        r.add_get("/api/gallery/list", self.gallery_list)
        r.add_get("/api/gallery/image/{image_id}", self.gallery_image)
        r.add_post("/api/gallery/upload", self.gallery_upload)
        r.add_post("/api/gallery/scan", self.gallery_scan)
        r.add_post("/api/gallery/tagall", self.gallery_tagall)
        r.add_post("/api/gallery/update", self.gallery_update)
        r.add_get("/api/export", self.export)
        r.add_static("/", _WEB_DIR, show_index=False)

    async def _index(self, request):
        return web.FileResponse(_WEB_DIR / "index.html")

    # ------------------------------------------------------------------
    async def overview(self, request):
        app = self.app
        stats = await app.dao.gallery_stats()
        pending_tags = sum(v for k, v in stats.items() if k.endswith("/pending"))
        last_user = await app.dao.kv_get("last_user_ts", 0) or 0
        return web.json_response({
            "ready": app.ready,
            "linked_umo": await app.linked_chat(),
            "name": app.profile.name if app.profile else "",
            "now": app.clock.describe_now(app.profile.met_on, app.profile.anniversary),
            "activity": await app.life.current_activity() if app.life else "",
            "sleeping": app.life.sleeping_now() if app.life else False,
            "mood": await app.mood.prompt_text() if app.mood else "",
            "stage": app.dynamic.stage(str(app.profile.relationship.get("stage", ""))),
            "signature": app.dynamic.signature,
            "avatar_desc": app.dynamic.avatar_desc,
            "capabilities": sorted(app.capabilities()),
            "unanswered": await app.dao.kv_get("proactive_unanswered", 0) or 0,
            "last_user_minutes": int((time.time() - last_user) / 60) if last_user else None,
            "gallery_stats": stats,
            "pending_tags": pending_tags,
            "vector_ok": app.vectors.available or not app.vectors._init_failed,
            "imagegen_ok": bool(app.imagegen and app.imagegen.available),
            "tts_ok": bool(app.voice and app.voice.tts_ready),
            "schedule": await app.dao.day_schedule(app.clock.today_str()),
        })

    # ------------------------------------------------------------------
    async def get_profile(self, request):
        p = self.app.persona_dir / "profile.yaml"
        d = self.app.persona_dir / "dynamic.yaml"
        return web.json_response({
            "profile": p.read_text(encoding="utf-8") if p.exists() else "",
            "dynamic": d.read_text(encoding="utf-8") if d.exists() else "",
        })

    async def save_profile(self, request):
        payload = await request.json()
        text = str(payload.get("profile") or "")
        try:
            data = yaml.safe_load(text) or {}
            Profile(data)  # 校验必填
        except Exception as e:
            return web.json_response({"message": f"档案格式不合法：{e}"}, status=400)
        (self.app.persona_dir / "profile.yaml").write_text(text, encoding="utf-8")
        self.app.profile = Profile(data)
        return web.json_response({"saved": True})

    # ------------------------------------------------------------------
    async def diaries(self, request):
        limit = int(request.query.get("limit", 14))
        dtype = request.query.get("type", "daily")
        return web.json_response({"items": await self.app.dao.recent_diaries(limit, dtype)})

    async def facts(self, request):
        subject = request.query.get("subject") or None
        return web.json_response({"items": await self.app.dao.list_facts(subject=subject, limit=300)})

    async def cheatsheet(self, request):
        return web.json_response({"item": await self.app.dao.latest_cheatsheet()})

    async def chatlog(self, request):
        limit = int(request.query.get("limit", 100))
        return web.json_response({"items": await self.app.dao.recent_chat(limit)})

    async def events(self, request):
        limit = int(request.query.get("limit", 50))
        return web.json_response({"items": await self.app.dao.recent_events(limit)})

    # ------------------------------------------------------------------
    async def pending(self, request):
        return web.json_response({"items": await self.app.dao.pending_list(50)})

    async def pending_cancel(self, request):
        payload = await request.json()
        aid = payload.get("id")
        if not isinstance(aid, int):
            return web.json_response({"message": "缺少 id"}, status=400)
        await self.app.dao.finish_action(aid, "cancelled")
        return web.json_response({"ok": True})

    async def run_action(self, request):
        payload = await request.json()
        kind = str(payload.get("kind") or "")
        if kind not in ("say", "voice", "post", "avatar", "signature"):
            return web.json_response({"message": "不支持的动作"}, status=400)
        body = payload.get("payload") or {}
        due_ts = payload.get("due_ts")
        if isinstance(due_ts, (int, float)) and due_ts > time.time():
            aid = await self.app.actions.schedule(kind, body, int(due_ts))
            return web.json_response({"scheduled": aid})
        ok = await self.app.actions.run(kind, body)
        return web.json_response({"ok": ok})

    # ------------------------------------------------------------------
    async def gallery_list(self, request):
        category = request.query.get("category") or None
        status = request.query.get("status") or None
        limit = min(int(request.query.get("limit", 30)), 60)
        offset = int(request.query.get("offset", 0))
        rows = await self.app.dao.list_images(category, status, limit, offset)
        for row in rows:
            row["thumb"] = await asyncio.to_thread(self._thumb_b64, row)
        return web.json_response({"items": rows})

    def _thumb_b64(self, row: dict) -> str:
        src = self.app.data_dir / row["file"]
        if not src.exists():
            return ""
        cache_dir = self.app.gallery_dir.parent / ".thumbs"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = cache_dir / f"{row['id']}.jpg"
        try:
            if not cache.exists() or cache.stat().st_mtime < src.stat().st_mtime:
                from PIL import Image

                with Image.open(src) as im:
                    im = im.convert("RGB")
                    im.thumbnail((160, 160))
                    im.save(cache, "JPEG", quality=70)
            return "data:image/jpeg;base64," + base64.b64encode(cache.read_bytes()).decode()
        except Exception:
            return ""

    async def gallery_image(self, request):
        image_id = request.match_info["image_id"]
        if not image_id.isdigit():
            return web.json_response({"message": "bad id"}, status=400)
        row = await self.app.dao.get_image(int(image_id))
        if row is None:
            return web.json_response({"message": "not found"}, status=404)
        path = (self.app.data_dir / row["file"]).resolve()
        if not str(path).startswith(str(self.app.data_dir.resolve())) or not path.exists():
            return web.json_response({"message": "not found"}, status=404)
        return web.FileResponse(path)

    async def gallery_upload(self, request):
        data = await request.post()
        upload = data.get("file")
        if upload is None or not getattr(upload, "filename", None):
            return web.json_response({"message": "missing file"}, status=400)
        safe_name = f"{int(time.time())}_{Path(upload.filename).name}"
        target_dir = self.app.gallery_dir / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_name
        target.write_bytes(upload.file.read())
        rel = str(target.relative_to(self.app.data_dir)).replace("\\", "/")
        image_id = await self.app.dao.add_image(rel, category="life", source="user", status="pending")
        return web.json_response({"id": image_id, "file": rel})

    async def gallery_scan(self, request):
        n = await self.app.gallery.ingest.scan_dir()
        return web.json_response({"added": n})

    async def gallery_tagall(self, request):
        if self._tagall_task and not self._tagall_task.done():
            return web.json_response({"running": True})
        self._tagall_task = asyncio.create_task(self._tagall())
        return web.json_response({"started": True})

    async def _tagall(self):
        try:
            n = await self.app.gallery.ingest.tag_all()
            await self.app.refresh_capabilities()
            logger.info(f"[loverbot] 面板触发的全量打标完成：{n} 张。")
        except Exception:
            logger.error("[loverbot] 全量打标异常：", exc_info=True)

    async def gallery_update(self, request):
        payload = await request.json()
        image_id = payload.get("id")
        op = str(payload.get("op") or "")
        if not isinstance(image_id, int):
            return web.json_response({"message": "缺少 id"}, status=400)
        if op == "anchor":
            await self.app.dao.set_anchor(image_id, bool(payload.get("value")))
        elif op == "delete":
            row = await self.app.dao.get_image(image_id)
            if row:
                await self.app.vectors.delete_gallery_doc(row.get("vec_id", ""))
                await self.app.dao.delete_image(image_id)
        elif op == "retag":
            await self.app.dao.set_image_status(image_id, "pending")
        elif op == "category":
            row = await self.app.dao.get_image(image_id)
            if row:
                await self.app.dao.tag_image(
                    image_id, str(payload.get("value") or row["category"]),
                    row["desc"], row["tags"], row["appearance"], row["vec_id"],
                )
        else:
            return web.json_response({"message": "不支持的操作"}, status=400)
        return web.json_response({"ok": True})

    # ------------------------------------------------------------------
    async def export(self, request):
        from ..store.export import export_all

        include_gallery = request.query.get("gallery", "1") == "1"
        path = await export_all(self.app, include_gallery=include_gallery)
        return web.FileResponse(
            path,
            headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
        )
