from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from catalog import collect_catalog
from menu import build_pages, parse_query, render_html, visible_plugins
from state import RevisionConflict, StateStore, default_state, validate_state


def sample():
    return json.loads((ROOT / "tools" / "sample_catalog.json").read_text(encoding="utf-8"))


class StateTests(unittest.TestCase):
    def test_save_survives_restart_and_conflicts(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            store = StateStore(path)
            value = store.snapshot()
            value["settings"]["title"] = "自定义菜单"
            saved = store.save(value)
            self.assertEqual(saved["revision"], 1)
            self.assertEqual(StateStore(path).snapshot(), saved)
            with self.assertRaises(RevisionConflict):
                store.save(value)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_invalid_values_are_rejected(self):
        for field, value in (("accent", "red; background:url(x)"), ("width", True), ("width", 100), ("columns", 4), ("show_admin", "false"), ("theme", "<script>")):
            with self.subTest(field=field, value=value):
                payload = default_state()
                payload["settings"][field] = value
                with self.assertRaises(ValueError):
                    validate_state(payload)
        for payload in (None, [], {"settings": []}, {"plugins": []}, {"version": 2}, {"revision": -1}):
            with self.assertRaises(ValueError):
                validate_state(payload)

    def test_duplicate_manual_ids_rejected(self):
        state = default_state()
        state["plugins"]["demo"] = {"manual_commands": [{"id": "a", "usage": "x"}, {"id": "a", "usage": "y"}]}
        with self.assertRaises(ValueError):
            validate_state(state)

    def test_unloaded_plugin_overrides_preserved(self):
        state = default_state()
        state["plugins"]["unloaded"] = {"description": "记住这个描述"}
        self.assertEqual(validate_state(state)["plugins"]["unloaded"]["description"], "记住这个描述")


class MenuTests(unittest.TestCase):
    def test_regular_and_admin_visibility(self):
        state = default_state()
        for admin in (False, True):
            names = [command["name"] for plugin in visible_plugins(sample(), state, is_admin=admin) for command in plugin["commands"]]
            self.assertNotIn("flight bind", names)
            self.assertNotIn("sid", names)
        state["settings"].update(show_admin=True, show_system=True)
        regular = [command["name"] for plugin in visible_plugins(sample(), state) for command in plugin["commands"]]
        admin = [command["name"] for plugin in visible_plugins(sample(), state, is_admin=True) for command in plugin["commands"]]
        self.assertNotIn("flight bind", regular)
        self.assertIn("flight bind", admin)
        self.assertIn("sid", admin)

    def test_overrides_manual_order_and_hiding(self):
        state = default_state()
        plugin_id = "astrbot_plugin_imas_birthday"
        state["plugins"][plugin_id] = {"name": "生日中心", "order": -1, "description": "新描述", "commands": {"demo.birthday:0": {"hidden": True}}, "manual_commands": [{"id": "custom", "usage": "/自定义", "description": "手动说明", "admin_only": False, "hidden": False, "order": -1}]}
        state = validate_state(state)
        visible = visible_plugins(sample(), state)
        self.assertEqual(visible[0]["name"], "生日中心")
        self.assertEqual(visible[0]["description"], "新描述")
        self.assertEqual(visible[0]["commands"][0]["display_usage"], "/自定义")
        self.assertNotIn("demo.birthday:0", [command["id"] for command in visible[0]["commands"]])
        state["plugins"][plugin_id]["hidden"] = True
        self.assertNotIn(plugin_id, [plugin["id"] for plugin in visible_plugins(sample(), state)])

    def test_search_name_alias_description_and_empty(self):
        for query in ("生日", "担当", "flight show", "空房", "帮助菜单"):
            self.assertTrue(build_pages(sample(), default_state(), query=query))
        self.assertEqual(build_pages(sample(), default_state(), query="不存在的功能"), [])
        state = default_state()
        state["settings"]["include_empty"] = False
        self.assertNotIn("日常提醒", [plugin["name"] for plugin in visible_plugins(sample(), state)])

    def test_pagination_does_not_lose_or_duplicate_commands(self):
        catalog = sample()[:1]
        base = catalog[0]["commands"][0]
        catalog[0]["commands"] = [{**base, "id": f"test.{index}", "name": f"指令{index}", "usage": f"指令{index}"} for index in range(37)]
        state = default_state()
        state["settings"]["page_size"] = 4
        pages = build_pages(catalog, state)
        identifiers = [item["id"] for page in pages for plugin in page["plugins"] for item in plugin["commands"]]
        self.assertEqual(len(pages), 10)
        self.assertEqual(len(identifiers), 37)
        self.assertEqual(len(set(identifiers)), 37)
        self.assertTrue(pages[1]["plugins"][0]["continued"])
        self.assertTrue(all(sum(max(1, len(plugin["commands"])) for plugin in page["plugins"]) <= 4 for page in pages))

    def test_parse_query(self):
        self.assertEqual(parse_query(""), ("", None))
        self.assertEqual(parse_query("3"), ("", 3))
        self.assertEqual(parse_query("生日 2"), ("生日", 2))
        self.assertEqual(parse_query("生日"), ("生日", None))
        with self.assertRaises(ValueError):
            parse_query("0")

    def test_html_injection_is_escaped_and_jinja_literals_are_data(self):
        state = default_state()
        attack = '<script>alert(1)</script> {{ 7 * 7 }} {% endraw %}'
        state["settings"]["title"] = attack
        html = render_html(build_pages(sample(), state)[0])
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertIn('{{ 7 * 7 }}', html)
        self.assertIn('{% endraw %}', html)

    def test_access_filtered_plugin_never_appears_as_empty(self):
        catalog = sample()[:1]
        catalog[0].update(commands=[], had_commands=True)
        self.assertEqual(build_pages(catalog, default_state()), [])


class CatalogTests(unittest.TestCase):
    def test_registered_groups_aliases_defaults_and_disabled_commands(self):
        async def handler(self, event, city: str, days: int = 2):
            """查询天气"""
        command = SimpleNamespace(command_name="show", custom_filter_list=[], get_complete_command_names=lambda: ["weather show", "天气 show", "weather 看", "天气 看"])
        group = SimpleNamespace(group_name="weather", custom_filter_list=[], get_complete_command_names=lambda: ["weather", "天气"])
        metadata = SimpleNamespace(handler_module_path="demo.main", handler_full_name="demo.main_weather", handler=handler, desc="", enabled=True, event_filters=[command])
        stars = [SimpleNamespace(name="demo", module_path="demo.main", display_name="示例插件", desc="插件描述", version="1", activated=True, reserved=False), SimpleNamespace(name="off", module_path="off.main", activated=False)]
        disabled = SimpleNamespace(**{**metadata.__dict__, "handler_full_name": "demo.disabled", "enabled": False})
        group_handler = SimpleNamespace(**{**metadata.__dict__, "handler_full_name": "demo.group", "event_filters": [group]})
        catalog = collect_catalog(stars, [metadata, disabled, group_handler])
        self.assertEqual(len(catalog), 1)
        self.assertEqual(len(catalog[0]["commands"]), 2)
        leaf = next(item for item in catalog[0]["commands"] if not item["is_group"])
        self.assertEqual(leaf["usage"], "weather show <city> [days=2]")
        self.assertEqual(leaf["description"], "查询天气")
        self.assertIn("天气 看", leaf["aliases"])


if __name__ == "__main__":
    unittest.main()
