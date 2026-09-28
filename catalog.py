"""Read registered commands without running command handlers or matching filters."""

from __future__ import annotations

import inspect


def _usage(handler, command: str, is_group: bool) -> str:
    if is_group:
        return command
    try:
        parameters = inspect.signature(handler).parameters.values()
    except (TypeError, ValueError):
        return command
    parts = [command]
    for parameter in parameters:
        if parameter.name in ("self", "event", "cls"):
            continue
        if parameter.kind in (parameter.VAR_KEYWORD, parameter.VAR_POSITIONAL):
            parts.append(f"[{parameter.name}…]")
        elif parameter.default is parameter.empty:
            parts.append(f"<{parameter.name}>")
        else:
            default = str(parameter.default)
            parts.append(f"[{parameter.name}{'=' + default if default else ''}]")
    return " ".join(parts)


def collect_catalog(stars, handlers) -> list[dict]:
    """Structural metadata only, so collection can also run in the local demo."""
    by_module: dict[str, list] = {}
    for handler in handlers:
        if getattr(handler, "enabled", True):
            by_module.setdefault(handler.handler_module_path, []).append(handler)
    result = []
    for star in stars:
        if not getattr(star, "activated", True) or not star.name:
            continue
        commands = []
        for handler in by_module.get(star.module_path, []):
            for index, command_filter in enumerate(handler.event_filters):
                if not hasattr(command_filter, "get_complete_command_names"):
                    continue
                is_group = hasattr(command_filter, "group_name")
                if not is_group and not hasattr(command_filter, "command_name"):
                    continue
                names = list(dict.fromkeys(command_filter.get_complete_command_names()))
                if not names:
                    continue
                # The first registered name is the canonical full command;
                # remaining entries include parent-group and command aliases.
                filters = [*handler.event_filters, *getattr(command_filter, "custom_filter_list", [])]
                restricted = any(
                    getattr(getattr(item, "permission_type", None), "name", "MEMBER") != "MEMBER"
                    for item in filters if hasattr(item, "permission_type")
                )
                commands.append({
                    "id": f"{handler.handler_full_name}:{index}",
                    "name": names[0],
                    "usage": _usage(handler.handler, names[0], is_group),
                    "aliases": sorted(names[1:]),
                    "description": (handler.desc or inspect.getdoc(handler.handler) or "暂无描述").strip()[:1200],
                    "admin_only": restricted,
                    "is_group": is_group,
                    "manual": False,
                })
        commands.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        result.append({
            "id": star.name,
            "name": getattr(star, "display_name", None) or star.name,
            "description": (getattr(star, "desc", None) or getattr(star, "short_desc", None) or "暂无描述")[:1200],
            "version": str(getattr(star, "version", None) or ""),
            "author": str(getattr(star, "author", None) or ""),
            "system": bool(getattr(star, "reserved", False)),
            "commands": commands,
        })
    return sorted(result, key=lambda item: (item["system"], item["name"].casefold()))


async def read_catalog(context, event=None) -> list[dict]:
    from astrbot.core.star.filter.event_message_type import EventMessageTypeFilter
    from astrbot.core.star.filter.permission import PermissionTypeFilter
    from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterTypeFilter
    from astrbot.core.star.session_plugin_manager import SessionPluginManager
    from astrbot.core.star.star_handler import star_handlers_registry

    stars = list(context.get_all_stars())
    handlers = list(star_handlers_registry)
    if event is None:
        return collect_catalog(stars, handlers)
    config = context.get_config(event.unified_msg_origin)
    allowed = getattr(event, "plugins_name", None)
    if allowed is None:
        allowed = config.get("plugin_set", ["*"])
    visible_stars = []
    for star in stars:
        if not getattr(star, "activated", True):
            continue
        if not star.reserved:
            if allowed is not None and allowed != ["*"] and star.name not in allowed:
                continue
            if not await SessionPluginManager.is_plugin_enabled_for_session(event.unified_msg_origin, star.name):
                continue
        visible_stars.append(star)
    safe_filters = (PermissionTypeFilter, PlatformAdapterTypeFilter, EventMessageTypeFilter)
    visible_handlers = []
    for handler in handlers:
        if config.get("disable_builtin_commands", False) and handler.handler_module_path == "astrbot.builtin_stars.builtin_commands.main":
            continue
        filters = list(handler.event_filters)
        for item in handler.event_filters:
            filters.extend(getattr(item, "custom_filter_list", []))
        # Only these built-in checks are read-only. Command/regex/custom filters
        # may change the incoming event, so never execute them during discovery.
        try:
            if all(item.filter(event, config) for item in filters if isinstance(item, safe_filters)):
                visible_handlers.append(handler)
        except Exception:
            # A failed visibility check must not expose a restricted command.
            continue
    catalog = collect_catalog(visible_stars, visible_handlers)
    raw_by_id = {item["id"]: item for item in collect_catalog(visible_stars, handlers)}
    for plugin in catalog:
        plugin["had_commands"] = bool(raw_by_id[plugin["id"]]["commands"])
    return catalog
