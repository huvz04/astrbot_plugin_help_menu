"""AstrBot command entry and authenticated Plugin Page endpoints."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import shutil
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .catalog import read_catalog
from .menu import THEMES, build_pages, parse_query, render_html, text_menu
from .state import RevisionConflict, StateStore, validate_state

PLUGIN = "astrbot_plugin_help_menu"


class HelpMenuPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context, config)
        self.config = config
        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN
        self.store = StateStore(self.data_dir / "menu.json")
        self.render_lock = asyncio.Lock()
        self.template = (Path(__file__).parent / "templates" / "menu.html").read_text(encoding="utf-8")
        self.closed = False
        for suffix, handler, methods in (
            ("state", self.page_state, ["GET"]),
            ("settings", self.page_save, ["POST"]),
            ("validate", self.page_validate, ["POST"]),
            ("preview", self.page_preview, ["POST"]),
            ("screenshot", self.page_screenshot, ["POST"]),
        ):
            context.register_web_api(f"/{PLUGIN}/{suffix}", handler, methods, "Help menu editor")

    def _number(self, name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(maximum, int(self.config.get(name, default))))
        except (ValueError, TypeError):
            return default

    async def terminate(self):
        self.closed = True

    def _ensure_active(self):
        if self.closed:
            raise ValueError("插件已停用，请重载插件后重新打开编辑器")

    async def _render_image(self, page: dict) -> Path:
        self._ensure_active()
        fingerprint = hashlib.sha256((self.template + json.dumps(page, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
        cache_dir = self.data_dir / "rendered"
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"{fingerprint}.png"
        ttl = self._number("cache_seconds", 300, 0, 86400)
        async with self.render_lock:
            self._ensure_active()
            if target.exists() and time.time() - target.stat().st_mtime < ttl:
                return target
            path = await asyncio.wait_for(
                self.html_render(
                    self.template,
                    {"menu": page},
                    return_url=False,
                    options={"type": "png", "full_page": True, "viewport_width": page["settings"]["width"], "viewport_height": 800},
                ),
                timeout=self._number("render_timeout", 60, 10, 180),
            )
            source = Path(path)
            if not source.is_file() or source.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("截图服务未返回有效图片")
            with source.open("rb") as stream:
                signature = stream.read(8)
            if not (signature.startswith(b"\x89PNG\r\n\x1a\n") or signature.startswith(b"\xff\xd8\xff")):
                raise ValueError("截图服务返回的内容不是 PNG/JPEG 图片")
            await asyncio.to_thread(shutil.copyfile, source, target)
            # Keep cache size bounded, including across plugin reloads.
            files = sorted(cache_dir.glob("*.png"), key=lambda file: file.stat().st_mtime, reverse=True)
            for expired in files[64:]:
                if expired != target:
                    expired.unlink(missing_ok=True)
            return target

    @filter.command("帮助")
    async def help_menu(self, event: AstrMessageEvent, query: GreedyStr = ""):
        """查看插件功能菜单。"""
        event.stop_event()
        try:
            self._ensure_active()
            keyword, requested_page = parse_query(str(query))
            catalog = await read_catalog(self.context, event)
            config = self.context.get_config(event.unified_msg_origin)
            prefixes = config.get("wake_prefix", ["/"])
            prefix = str(prefixes[0]) if isinstance(prefixes, list) and prefixes else "/"
            pages = build_pages(catalog, self.store.snapshot(), is_admin=event.is_admin(), query=keyword, prefix=prefix)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        if not pages:
            yield event.plain_result("没有找到可显示的插件或指令。请尝试其他关键词，或在菜单编辑器检查隐藏设置。")
            return
        if requested_page is not None and requested_page > len(pages):
            yield event.plain_result(f"当前菜单共 {len(pages)} 页，请输入 1–{len(pages)} 之间的页码。")
            return
        limit = self._number("max_pages_per_request", 4, 1, 10)
        selected = [pages[requested_page - 1]] if requested_page else pages[:limit]
        for page in selected:
            try:
                path = await self._render_image(page)
            except Exception:
                logger.exception("帮助菜单截图失败，发送文字菜单")
                yield event.plain_result("菜单截图暂时不可用，先发送文字版：\n" + text_menu(page))
            else:
                yield event.image_result(str(path))
        if not requested_page and len(pages) > limit:
            followup = f"{prefix}帮助 {keyword + ' ' if keyword else ''}{limit + 1}"
            yield event.plain_result(f"共 {len(pages)} 页，本次发送前 {limit} 页。发送 {followup} 查看下一页。")

    async def page_state(self):
        try:
            self._ensure_active()
            return json_response({"state": self.store.snapshot(), "catalog": await read_catalog(self.context)})
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

    async def page_save(self):
        try:
            self._ensure_active()
            payload = await request.json(default=None)
            # No await between revision validation and the atomic write.
            return json_response({"state": self.store.save(payload)})
        except RevisionConflict as exc:
            return error_response(str(exc), status_code=409)
        except (ValueError, TypeError) as exc:
            return error_response(str(exc), status_code=400)
        except OSError:
            logger.exception("帮助菜单配置保存失败")
            return error_response("保存失败，请检查插件数据目录的写入权限", status_code=500)

    async def page_validate(self):
        try:
            self._ensure_active()
            return json_response({"state": validate_state(await request.json(default=None))})
        except (ValueError, TypeError) as exc:
            return error_response(str(exc), status_code=400)

    async def _preview_page(self, payload) -> dict:
        self._ensure_active()
        if not isinstance(payload, dict):
            raise ValueError("预览请求必须是 JSON 对象")
        state = validate_state(payload.get("state", self.store.snapshot()))
        query = payload.get("query", "")
        page_number = payload.get("page", 1)
        as_admin = payload.get("as_admin", False)
        if not isinstance(query, str) or len(query) > 100:
            raise ValueError("搜索词最多 100 字")
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            raise ValueError("页码必须是正整数")
        if not isinstance(as_admin, bool):
            raise ValueError("as_admin 必须是布尔值")
        pages = build_pages(await read_catalog(self.context), state, is_admin=as_admin, query=query)
        if not pages:
            return {"settings": state["settings"], "palette": THEMES[state["settings"]["theme"]], "plugins": [], "page": 1, "page_count": 1, "plugin_count": 0, "command_count": 0, "query": query, "prefix": state["settings"]["prefix"] or "/"}
        return pages[min(page_number, len(pages)) - 1]

    async def page_preview(self):
        try:
            page = await self._preview_page(await request.json(default=None))
            return json_response({"html": render_html(page), "page": page["page"], "pages": page["page_count"], "plugin_count": page["plugin_count"], "command_count": page["command_count"]})
        except (ValueError, TypeError) as exc:
            return error_response(str(exc), status_code=400)

    async def page_screenshot(self):
        try:
            page = await self._preview_page(await request.json(default=None))
            path = await self._render_image(page)
            content = await asyncio.to_thread(path.read_bytes)
            content_type = "image/png" if content.startswith(b"\x89PNG") else "image/jpeg"
            return json_response({"image": f"data:{content_type};base64,{base64.b64encode(content).decode()}", "page": page["page"], "pages": page["page_count"]})
        except (ValueError, TypeError) as exc:
            return error_response(str(exc), status_code=400)
        except Exception:
            logger.exception("帮助菜单预览截图失败")
            return error_response("截图失败，请检查 AstrBot 的 HTML 文转图服务；HTML 预览仍可使用", status_code=503)
