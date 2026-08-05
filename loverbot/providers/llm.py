"""LLM 调用：OpenAI 兼容接口（chat.completions），含视觉输入。

模型分工（成本意识）：chat 强模型、light 轻模型（心跳决策/意图解析）、
vlm 视觉打标；light/vlm 未配置时自动回落到 chat。
"""

import asyncio
import base64
import json
import mimetypes
import re
from pathlib import Path

import aiohttp

from ..log import logger

_JSON_RE = re.compile(r"[\[{].*[\]}]", re.S)


def _image_part(path_or_url: str) -> dict:
    if str(path_or_url).startswith(("http://", "https://", "data:")):
        url = str(path_or_url)
    else:
        p = Path(path_or_url)
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        url = f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"
    return {"type": "image_url", "image_url": {"url": url}}


class LLM:
    def __init__(self, cfg):
        self.cfg = cfg

    async def _call(
        self,
        role: str,
        messages: list[dict],
        retries: int = 1,
    ) -> str:
        conf = self.cfg.model_conf(role)
        base = str(conf.get("base_url", "")).rstrip("/")
        key = str(conf.get("api_key", ""))
        model = str(conf.get("model", ""))
        if not (base and key and model):
            raise RuntimeError(f"模型未配置（models.{role}）")

        payload = {"model": model, "messages": messages}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=180)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"{base}/chat/completions", json=payload, headers=headers
                    ) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                        data = await resp.json()
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                if text.strip():
                    return text.strip()
                raise RuntimeError("模型返回了空内容")
            except Exception as e:
                last_err = e
                if attempt < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败（{role}）：{last_err}")

    @staticmethod
    def _messages(
        prompt: str | None,
        contexts: list[dict] | None,
        system_prompt: str | None,
        image_urls: list[str] | None,
    ) -> list[dict]:
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(contexts or [])
        if prompt is not None or image_urls:
            if image_urls:
                content: list[dict] = [{"type": "text", "text": prompt or ""}]
                content.extend(_image_part(u) for u in image_urls)
                messages.append({"role": "user", "content": content})
            else:
                messages.append({"role": "user", "content": prompt or ""})
        return messages

    # ---- 对外接口（与调用方约定保持稳定）----
    async def chat(
        self,
        *,
        prompt: str | None = None,
        contexts: list[dict] | None = None,
        system_prompt: str | None = None,
        image_urls: list[str] | None = None,
    ) -> str:
        return await self._call("chat", self._messages(prompt, contexts, system_prompt, image_urls))

    async def light(self, prompt: str, system_prompt: str | None = None) -> str:
        return await self._call("light", self._messages(prompt, None, system_prompt, None))

    async def light_json(self, prompt: str, system_prompt: str | None = None):
        """要求轻模型输出 JSON；解析失败自动补救一次，仍失败返回 None。"""
        sp = (system_prompt or "") + "\n只输出合法 JSON，不要解释，不要代码块围栏。"
        for attempt in range(2):
            try:
                raw = await self._call("light", self._messages(prompt, None, sp, None))
                parsed = self.extract_json(raw)
                if parsed is not None:
                    return parsed
            except Exception as e:
                logger.warning(f"[loverbot] light_json 调用失败（第{attempt + 1}次）：{e}")
            prompt = "你上次的输出不是合法 JSON。重新只输出 JSON。\n" + prompt
        return None

    async def vlm(self, prompt: str, image_path: str) -> str:
        return await self._call("vlm", self._messages(prompt, None, None, [image_path]))

    # 健康检查用
    def role_configured(self, role: str) -> bool:
        conf = self.cfg.model_conf(role)
        return bool(conf.get("base_url") and conf.get("api_key") and conf.get("model"))

    @staticmethod
    def extract_json(raw: str):
        raw = raw.strip()
        raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw, flags=re.S).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            m = _JSON_RE.search(raw)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None
        return None
