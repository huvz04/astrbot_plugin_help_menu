"""Shared HTML menu, visibility, search, and pagination for chat and WebUI."""

from __future__ import annotations

import copy
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

THEMES = {
    "sakura": {"background": "#f8f0f2", "surface": "#fffcfd", "ink": "#382e37", "muted": "#84747f", "line": "#eadfe5", "soft": "#f4e6ec"},
    "paper": {"background": "#f4f3ed", "surface": "#fffefa", "ink": "#32382f", "muted": "#777d72", "line": "#e2e4d9", "soft": "#eef0e6"},
    "dark": {"background": "#191d28", "surface": "#252a38", "ink": "#f2eef5", "muted": "#b6aec2", "line": "#3b4051", "soft": "#323347"},
}


def parse_query(query: str) -> tuple[str, int | None]:
    query = query.strip()
    match = re.fullmatch(r"(?:(.*?)\s+)?(\d+)", query)
    if match:
        page = int(match.group(2))
        if page < 1:
            raise ValueError("页码从 1 开始")
        return (match.group(1) or "").strip(), page
    return query, None


def visible_plugins(catalog: list[dict], state: dict, *, is_admin: bool = False, query: str = "", prefix: str = "/") -> list[dict]:
    settings = state["settings"]
    prefix = settings["prefix"] or prefix
    needle = query.casefold().strip()
    result = []
    for source in catalog:
        override = state["plugins"].get(source["id"], {})
        if override.get("hidden") or (source["system"] and not settings["show_system"]):
            continue
        plugin = copy.deepcopy(source)
        for name in ("name", "description"):
            plugin[name] = override.get(name) or plugin[name]
        plugin["category"] = override.get("category") or ("系统功能" if plugin["system"] else "插件功能")
        plugin["order"] = override.get("order", 1000)
        matches_plugin = needle in " ".join((plugin["id"], plugin["name"], plugin["description"], plugin["category"])).casefold()
        commands = []
        manual = [{**item, "name": item["usage"], "aliases": [], "manual": True, "is_group": False} for item in override.get("manual_commands", [])]
        for source_command in [*source["commands"], *manual]:
            change = source_command if source_command["manual"] else override.get("commands", {}).get(source_command["id"], {})
            if change.get("hidden") or (source_command["admin_only"] and not (is_admin and settings["show_admin"])):
                continue
            command = copy.deepcopy(source_command)
            command["usage"] = change.get("usage") or command["usage"]
            command["description"] = change.get("description") or command["description"]
            command["order"] = change.get("order", 1000)
            if needle and not matches_plugin and needle not in " ".join((command["usage"], command["description"], *command["aliases"])).casefold():
                continue
            # Manual usages may already include the configured wake prefix.
            command["display_usage"] = command["usage"] if prefix and command["usage"].startswith(prefix) else prefix + command["usage"]
            command["display_aliases"] = [prefix + alias for alias in command["aliases"]] if settings["show_aliases"] else []
            commands.append(command)
        plugin["commands"] = sorted(commands, key=lambda item: (item["order"], item["name"].casefold()))
        # Never turn an access-filtered plugin into an apparently commandless card.
        if not commands and (source.get("had_commands", bool(source["commands"])) or manual):
            continue
        if not commands and (not settings["include_empty"] or (needle and not matches_plugin)):
            continue
        result.append(plugin)
    return sorted(result, key=lambda item: (item["order"], item["name"].casefold()))


def build_pages(catalog: list[dict], state: dict, *, is_admin: bool = False, query: str = "", prefix: str = "/") -> list[dict]:
    plugins = visible_plugins(catalog, state, is_admin=is_admin, query=query, prefix=prefix)
    settings = copy.deepcopy(state["settings"])
    limit = settings["page_size"]
    batches: list[list[dict]] = []
    batch: list[dict] = []
    used = 0
    for plugin in plugins:
        entries = plugin["commands"] or [None]
        offset = 0
        while offset < len(entries):
            if used == limit:
                batches.append(batch)
                batch, used = [], 0
            chunk = entries[offset:offset + limit - used]
            card = {**plugin, "commands": [item for item in chunk if item is not None], "continued": offset > 0}
            batch.append(card)
            offset += len(chunk)
            used += len(chunk)
    if batch:
        batches.append(batch)
    if not batches:
        return []
    return [{
        "settings": settings,
        "palette": THEMES[settings["theme"]],
        "plugins": batch,
        "page": index + 1,
        "page_count": len(batches),
        "plugin_count": len(plugins),
        "command_count": sum(len(plugin["commands"]) for plugin in plugins),
        "query": query,
        "prefix": settings["prefix"] or prefix,
    } for index, batch in enumerate(batches)]


def render_html(page: dict) -> str:
    environment = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html"]))
    return environment.get_template("menu.html").render(menu=page)


def text_menu(page: dict) -> str:
    parts = [f"{page['settings']['title']} · {page['page']}/{page['page_count']}"]
    for plugin in page["plugins"]:
        parts.append(f"\n【{plugin['name']}】{plugin['description']}")
        for command in plugin["commands"]:
            parts.append(f"{command['display_usage']} — {command['description']}")
        if not plugin["commands"]:
            parts.append("此插件通过事件或后台任务自动运行。")
    return "\n".join(parts)
