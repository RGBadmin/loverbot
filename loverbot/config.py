"""配置：config.yaml 的类型化只读视图。

原则（需求·原则3）：这里只有"接线"与防打扰最小集合，
没有任何表现强度旋钮——表现由生命档案推导。
运行期可调参数（防打扰三参数等）支持 kv 覆盖层（导演 bot /config set），
覆盖只改数据库不改配置文件。
"""

from pathlib import Path
from typing import Any

import yaml


class Cfg:
    def __init__(self, raw: dict):
        self._raw = raw or {}

    @classmethod
    def load(cls, path: Path) -> "Cfg":
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {})

    def _g(self, *path: str, default: Any = "") -> Any:
        node: Any = self._raw
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node if node is not None else default

    def _int(self, *path: str, default: int) -> int:
        v = self._g(*path, default=None)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def apply_override(self, dotted: str, value: Any):
        """kv 覆盖层：'proactive.min_gap_minutes' 这类路径写入内存配置。"""
        node = self._raw
        keys = dotted.split(".")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    # ---- Telegram ----
    @property
    def main_token(self) -> str:
        return str(self._g("telegram", "main_bot_token")).strip()

    @property
    def director_token(self) -> str:
        return str(self._g("telegram", "director_bot_token")).strip()

    @property
    def admin_id(self) -> str:
        return str(self._g("telegram", "admin_id")).strip()

    @property
    def proxy(self) -> str:
        return str(self._g("telegram", "proxy")).strip()

    # ---- 频道 ----
    @property
    def channel_id(self) -> str:
        return str(self._g("channel", "channel_id")).strip()

    @property
    def discussion_group_id(self) -> str:
        return str(self._g("channel", "discussion_group_id")).strip()

    # ---- 模型（light/vlm 缺省回落到 chat）----
    def model_conf(self, role: str) -> dict:
        conf = self._g("models", role, default={})
        conf = dict(conf) if isinstance(conf, dict) else {}
        if role in ("light", "vlm") and not conf.get("model") and not conf.get("base_url"):
            return self.model_conf("chat")
        base = self.model_conf("chat") if role in ("light", "vlm") else {}
        return {**{k: v for k, v in base.items() if v}, **{k: v for k, v in conf.items() if v}}

    # ---- 语音 ----
    @property
    def tts_type(self) -> str:
        return str(self._g("tts", "type")).strip().lower()

    def tts_conf(self) -> dict:
        v = self._g("tts", self.tts_type, default={})
        return v if isinstance(v, dict) else {}

    @property
    def stt_type(self) -> str:
        return str(self._g("stt", "type")).strip().lower()

    def stt_conf(self) -> dict:
        v = self._g("stt", self.stt_type, default={})
        return v if isinstance(v, dict) else {}

    # ---- 生图 ----
    @property
    def imagegen_order(self) -> list[str]:
        v = self._g("imagegen", "backend_order", default=[])
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []

    def imagegen_backend(self, name: str) -> dict:
        v = self._g("imagegen", name, default={})
        return v if isinstance(v, dict) else {}

    # ---- 防打扰（仅有的三个行为参数）----
    @property
    def min_gap_minutes(self) -> int:
        return self._int("proactive", "min_gap_minutes", default=45)

    @property
    def max_silence_hours(self) -> int:
        return self._int("proactive", "max_silence_hours", default=30)

    @property
    def max_unanswered(self) -> int:
        return self._int("proactive", "max_unanswered", default=3)

    # ---- 面板 ----
    @property
    def panel_host(self) -> str:
        return str(self._g("panel", "host", default="0.0.0.0")).strip() or "0.0.0.0"

    @property
    def panel_port(self) -> int:
        return self._int("panel", "port", default=9700)

    @property
    def panel_token(self) -> str:
        return str(self._g("panel", "token")).strip()

    # ---- 系统 ----
    @property
    def timezone(self) -> str:
        return str(self._g("system", "timezone", default="Asia/Shanghai")).strip() or "Asia/Shanghai"

    @property
    def heartbeat_minutes(self) -> int:
        return max(1, self._int("system", "heartbeat_minutes", default=5))

    @property
    def data_dir(self) -> Path:
        return Path(str(self._g("system", "data_dir", default="./data")).strip() or "./data")

    @property
    def debug(self) -> bool:
        return bool(self._g("system", "debug", default=False))

    # ---- 校验 ----
    def missing_required(self) -> list[str]:
        missing = []
        if not self.main_token:
            missing.append("telegram.main_bot_token（她的 bot token）")
        if not self.admin_id:
            missing.append("telegram.admin_id（管理员 user id）")
        if not (self.model_conf("chat").get("model") and self.model_conf("chat").get("api_key")):
            missing.append("models.chat（对话模型 base_url/api_key/model）")
        return missing
