"""AstrBot entry tests with API doubles; does not claim a running AstrBot test."""

from __future__ import annotations

import asyncio
import base64
import importlib
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))


def api_modules(data_dir):
    names = ['astrbot', 'astrbot.api', 'astrbot.api.event', 'astrbot.api.star', 'astrbot.api.web', 'astrbot.core', 'astrbot.core.star', 'astrbot.core.star.filter', 'astrbot.core.star.filter.command', 'astrbot.core.utils', 'astrbot.core.utils.astrbot_path']
    modules = {name: ModuleType(name) for name in names}
    for module in modules.values():
        module.__path__ = []
    class Star:
        def __init__(self, context, config):
            self.context = context
    class Request:
        payload = None
        async def json(self, default=None):
            return self.payload
    modules['astrbot.api'].AstrBotConfig = dict
    modules['astrbot.api'].logger = logging.getLogger('help_menu_test')
    modules['astrbot.api.event'].AstrMessageEvent = object
    modules['astrbot.api.event'].filter = SimpleNamespace(command=lambda *args, **kwargs: lambda handler: handler)
    modules['astrbot.api.star'].Context = object
    modules['astrbot.api.star'].Star = Star
    modules['astrbot.api.web'].json_response = lambda value: value
    modules['astrbot.api.web'].error_response = lambda message, status_code=400: {"error": message, "status": status_code}
    modules['astrbot.api.web'].request = Request()
    modules['astrbot.core.star.filter.command'].GreedyStr = str
    modules['astrbot.core.utils.astrbot_path'].get_astrbot_plugin_data_path = lambda: data_dir
    return modules


class Event:
    unified_msg_origin = "test:group:1"
    stopped = False
    def is_admin(self): return False
    def stop_event(self): self.stopped = True
    def plain_result(self, value): return {"text": value}
    def image_result(self, value): return {"image": value}


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.modules = api_modules(self.temporary.name)
        self.patch_modules = patch.dict(sys.modules, self.modules)
        self.patch_modules.start()
        self.main = importlib.import_module('astrbot_plugin_help_menu.main')
        self.routes = []
        context = SimpleNamespace(register_web_api=lambda *args: self.routes.append(args), get_config=lambda umo: {"wake_prefix": ["!"]})
        self.plugin = self.main.HelpMenuPlugin(context, {})
        import json
        sample = json.loads((ROOT / 'tools' / 'sample_catalog.json').read_text(encoding='utf-8'))
        self.catalog_patch = patch.object(self.main, 'read_catalog', AsyncMock(return_value=sample))
        self.catalog_patch.start()

    async def asyncTearDown(self):
        self.catalog_patch.stop()
        self.patch_modules.stop()
        sys.modules.pop('astrbot_plugin_help_menu.main', None)
        self.temporary.cleanup()

    async def test_chat_renders_images_with_session_prefix(self):
        path = Path(self.temporary.name) / 'image.png'
        path.write_bytes(b'\x89PNG\r\n\x1a\n' + b'fake')
        renderer = AsyncMock(return_value=path)
        self.plugin.html_render = renderer
        event = Event()
        messages = [value async for value in self.plugin.help_menu(event)]
        self.assertTrue(event.stopped)
        self.assertIn('image', messages[0])
        self.assertEqual(renderer.call_args.args[1]['menu']['prefix'], '!')
        self.assertFalse(renderer.call_args.kwargs['return_url'])
        self.assertEqual(renderer.call_args.kwargs['options']['type'], 'png')
        self.assertEqual(renderer.call_args.kwargs['options']['viewport_width'], 1080)
        messages = [value async for value in self.plugin.help_menu(event)]
        self.assertEqual(renderer.await_count, 1, 'identical menu should use cached screenshot')

    async def test_render_failure_falls_back_to_text(self):
        self.plugin.html_render = AsyncMock(side_effect=RuntimeError('renderer unavailable'))
        with self.assertLogs('help_menu_test', level='ERROR'):
            messages = [value async for value in self.plugin.help_menu(Event())]
        self.assertIn('文字版', messages[0]['text'])
        self.assertIn('!帮助', messages[0]['text'])

    async def test_page_limits_and_explicit_page(self):
        self.plugin.store.value['settings']['page_size'] = 4
        self.plugin.config['max_pages_per_request'] = 1
        self.plugin._render_image = AsyncMock(return_value=Path('test.png'))
        messages = [value async for value in self.plugin.help_menu(Event())]
        self.assertEqual(len(messages), 2)
        self.assertIn('!帮助 2', messages[1]['text'])
        explicit = [value async for value in self.plugin.help_menu(Event(), '2')]
        self.assertEqual(len(explicit), 1)
        invalid = [value async for value in self.plugin.help_menu(Event(), '99')]
        self.assertIn('页码', invalid[0]['text'])

    async def test_preview_save_validation_and_revision_conflict(self):
        request = self.modules['astrbot.api.web'].request
        snapshot = await self.plugin.page_state()
        request.payload = {"state": snapshot['state'], "page": 1}
        preview = await self.plugin.page_preview()
        self.assertIn('<!doctype html>', preview['html'])
        request.payload = snapshot['state']
        saved = await self.plugin.page_save()
        self.assertEqual(saved['state']['revision'], 1)
        conflict = await self.plugin.page_save()
        self.assertEqual(conflict['status'], 409)
        request.payload = [1, 2]
        self.assertEqual((await self.plugin.page_preview())['status'], 400)
        self.assertEqual((await self.plugin.page_save())['status'], 400)
        self.assertEqual((await self.plugin.page_validate())['status'], 400)

    async def test_disabled_plugin_api_cannot_edit(self):
        await self.plugin.terminate()
        self.assertEqual((await self.plugin.page_state())['status'], 400)
        self.modules['astrbot.api.web'].request.payload = self.plugin.store.snapshot()
        self.assertEqual((await self.plugin.page_save())['status'], 400)
        self.assertEqual(len(self.routes), 5)

    async def test_screenshot_api_returns_png_data_uri(self):
        path = Path(self.temporary.name) / 'screenshot.png'
        content = b'\x89PNG\r\n\x1a\n' + b'fake'
        path.write_bytes(content)
        self.plugin._render_image = AsyncMock(return_value=path)
        self.modules['astrbot.api.web'].request.payload = {'state': self.plugin.store.snapshot(), 'page': 1}
        result = await self.plugin.page_screenshot()
        self.assertEqual(base64.b64decode(result['image'].split(',', 1)[1]), content)
        self.assertEqual(result['page'], 1)

    async def test_discovery_filters_session_permission_and_platform_without_running_commands(self):
        from astrbot_plugin_help_menu.catalog import read_catalog
        names = ['astrbot.core.star.filter.event_message_type', 'astrbot.core.star.filter.permission', 'astrbot.core.star.filter.platform_adapter_type', 'astrbot.core.star.session_plugin_manager', 'astrbot.core.star.star_handler']
        modules = {name: ModuleType(name) for name in names}
        class Permission:
            permission_type = SimpleNamespace(name='ADMIN')
            def filter(self, event, config): return event.is_admin()
        class Platform:
            def filter(self, event, config): return False
        class MessageType:
            def filter(self, event, config): return True
        modules[names[0]].EventMessageTypeFilter = MessageType
        modules[names[1]].PermissionTypeFilter = Permission
        modules[names[2]].PlatformAdapterTypeFilter = Platform
        session_check = AsyncMock(side_effect=lambda umo, name: name != 'session_off')
        modules[names[3]].SessionPluginManager = SimpleNamespace(is_plugin_enabled_for_session=session_check)
        class Command:
            custom_filter_list = []
            command_name = 'test'
            def get_complete_command_names(self): return ['test']
            def filter(self, event, config): raise AssertionError('command filter must never run during discovery')
        async def callback(self, event): pass
        def star(name):
            return SimpleNamespace(name=name, module_path=name + '.main', activated=name != 'global_off', reserved=False, desc=name, display_name=name)
        def handler(name, filters):
            return SimpleNamespace(handler_module_path=name+'.main', handler_full_name=name+'.main_callback', handler=callback, desc='说明', enabled=True, event_filters=[Command(), *filters])
        stars = [star(name) for name in ('allowed', 'restricted', 'wrong_platform', 'config_off', 'session_off', 'global_off')]
        modules[names[4]].star_handlers_registry = [handler('allowed', [MessageType()]), handler('restricted', [Permission()]), handler('wrong_platform', [Platform()]), handler('config_off', []), handler('session_off', []), handler('global_off', [])]
        context = SimpleNamespace(get_all_stars=lambda: stars, get_config=lambda umo: {'plugin_set': ['allowed','restricted','wrong_platform','session_off','global_off']})
        with patch.dict(sys.modules, modules):
            catalog = await read_catalog(context, Event())
        by_id = {plugin['id']: plugin for plugin in catalog}
        self.assertEqual(set(by_id), {'allowed','restricted','wrong_platform'})
        self.assertEqual(len(by_id['allowed']['commands']), 1)
        self.assertFalse(by_id['restricted']['commands'])
        self.assertTrue(by_id['restricted']['had_commands'])
        from astrbot_plugin_help_menu.menu import build_pages
        pages = build_pages(catalog, self.plugin.store.snapshot())
        self.assertEqual([plugin['id'] for page in pages for plugin in page['plugins']], ['allowed'])


if __name__ == '__main__':
    unittest.main()
