"""Validated, atomic editor state; independent of AstrBot."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path

DEFAULT_SETTINGS = {
    "title": "花见 · 帮助菜单",
    "subtitle": "从这里，找到你需要的功能。",
    "footer": "发送指令即可使用 · 菜单内容随插件更新",
    "theme": "sakura",
    "accent": "#af5676",
    "width": 1080,
    "columns": 2,
    "page_size": 16,
    "prefix": "",
    "show_system": False,
    "show_admin": False,
    "include_empty": True,
    "show_aliases": True,
}


def default_state() -> dict:
    return {"version": 1, "revision": 0, "settings": copy.deepcopy(DEFAULT_SETTINGS), "plugins": {}}


def _text(value, label: str, limit: int = 1200) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(f"{label} 必须是最多 {limit} 字的文本")
    return value.strip()


def _boolean(value, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} 必须是布尔值")
    return value


def _integer(value, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} 必须是 {minimum}–{maximum} 范围内的整数")
    return value


def _command_override(value, manual: bool = False) -> dict:
    if not isinstance(value, dict):
        raise ValueError("指令设置必须是对象")
    result = {
        "description": _text(value.get("description", ""), "指令描述"),
        "usage": _text(value.get("usage", ""), "指令用法", 300),
        "hidden": _boolean(value.get("hidden", False), "隐藏指令"),
        "order": _integer(value.get("order", 1000), "指令排序", -10000, 10000),
    }
    if manual:
        result["id"] = _text(value.get("id", ""), "手动指令 ID", 100)
        result["admin_only"] = _boolean(value.get("admin_only", False), "管理员指令")
        if not result["id"] or not result["usage"]:
            raise ValueError("手动指令必须填写 ID 和用法")
    return result


def validate_state(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("菜单设置必须是 JSON 对象")
    if payload.get("version", 1) != 1:
        raise ValueError("不支持的菜单配置版本")
    result = default_state()
    result["revision"] = _integer(payload.get("revision", 0), "配置版本", 0, 2**53 - 1)
    settings = payload.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("settings 必须是对象")
    for name in ("title", "subtitle", "footer", "prefix", "accent", "theme"):
        limits = {"title": 100, "prefix": 16, "accent": 7, "theme": 20}
        result["settings"][name] = _text(settings.get(name, DEFAULT_SETTINGS[name]), name, limits.get(name, 400))
    if result["settings"]["theme"] not in ("sakura", "paper", "dark"):
        raise ValueError("主题必须是 sakura、paper 或 dark")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", result["settings"]["accent"]):
        raise ValueError("主题色必须是 #RRGGBB 格式")
    for name, bounds in {"width": (720, 1600), "columns": (1, 3), "page_size": (4, 40)}.items():
        result["settings"][name] = _integer(settings.get(name, DEFAULT_SETTINGS[name]), name, *bounds)
    for name in ("show_system", "show_admin", "include_empty", "show_aliases"):
        result["settings"][name] = _boolean(settings.get(name, DEFAULT_SETTINGS[name]), name)
    plugins = payload.get("plugins", {})
    if not isinstance(plugins, dict) or len(plugins) > 1000:
        raise ValueError("plugins 必须是最多 1000 项的对象")
    for plugin_id, value in plugins.items():
        plugin_id = _text(plugin_id, "插件 ID", 300)
        if not plugin_id or not isinstance(value, dict):
            raise ValueError("插件 ID 或插件设置无效")
        commands = value.get("commands", {})
        manual = value.get("manual_commands", [])
        if not isinstance(commands, dict) or len(commands) > 1000:
            raise ValueError("commands 必须是最多 1000 项的对象")
        if not isinstance(manual, list) or len(manual) > 100:
            raise ValueError("每个插件最多添加 100 条手动指令")
        cleaned_manual = [_command_override(item, manual=True) for item in manual]
        if len({item["id"] for item in cleaned_manual}) != len(cleaned_manual):
            raise ValueError("手动指令 ID 不能重复")
        result["plugins"][plugin_id] = {
            "name": _text(value.get("name", ""), "插件显示名称", 100),
            "description": _text(value.get("description", ""), "插件描述"),
            "category": _text(value.get("category", ""), "插件分类", 50),
            "hidden": _boolean(value.get("hidden", False), "隐藏插件"),
            "order": _integer(value.get("order", 1000), "插件排序", -10000, 10000),
            "commands": {_text(key, "指令 ID", 500): _command_override(item) for key, item in commands.items()},
            "manual_commands": cleaned_manual,
        }
    return result


class RevisionConflict(ValueError):
    pass


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.value = default_state()
        if path.exists():
            # A corrupt file is left intact; the caller reports the error.
            self.value = validate_state(json.loads(path.read_text(encoding="utf-8")))

    def snapshot(self) -> dict:
        return copy.deepcopy(self.value)

    def save(self, payload: dict) -> dict:
        value = validate_state(payload)
        if value["revision"] != self.value["revision"]:
            raise RevisionConflict("配置已在其他页面更新，请重新读取后再保存")
        value["revision"] += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".menu-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.value = value
        return self.snapshot()
