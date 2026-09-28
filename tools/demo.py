"""Local editor preview with explicitly labelled sample data, no AstrBot needed."""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from menu import THEMES, build_pages, render_html  # noqa: E402
from state import RevisionConflict, StateStore, validate_state  # noqa: E402

CATALOG = json.loads((ROOT / "tools" / "sample_catalog.json").read_text(encoding="utf-8"))
STORE = StateStore(ROOT / "previews" / "demo-state.json")
STORE_LOCK = Lock()


class Handler(BaseHTTPRequestHandler):
    def respond(self, body, content_type="application/json; charset=utf-8", status=200):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/demo/api/state":
            self.respond({"state": STORE.snapshot(), "catalog": CATALOG})
            return
        assets = {"/demo/": ("index.html", "text/html"), "/demo/app.js": ("app.js", "text/javascript"), "/demo/style.css": ("style.css", "text/css")}
        if path not in assets:
            self.respond({"message": "Not found"}, status=404)
            return
        filename, media = assets[path]
        self.respond((ROOT / "pages" / "editor" / filename).read_bytes(), media + "; charset=utf-8")

    def do_POST(self):
        try:
            if self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
                self.respond({"message": "Invalid origin"}, status=403)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2 * 1024 * 1024:
                raise ValueError("请求体必须是非空且不超过 2MB 的 JSON 内容")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求必须是 JSON 对象")
            path = urlsplit(self.path).path
            if path == "/demo/api/settings":
                with STORE_LOCK:
                    result = {"state": STORE.save(payload)}
            elif path == "/demo/api/validate":
                result = {"state": validate_state(payload)}
            elif path == "/demo/api/preview":
                state = validate_state(payload.get("state", STORE.snapshot()))
                query = payload.get("query", "")
                page = payload.get("page", 1)
                admin = payload.get("as_admin", False)
                if not isinstance(query, str) or len(query) > 100 or isinstance(page, bool) or not isinstance(page, int) or page < 1 or not isinstance(admin, bool):
                    raise ValueError("无效的搜索、页码或管理员视角")
                pages = build_pages(CATALOG, state, query=query, is_admin=admin)
                view = pages[min(page, len(pages)) - 1] if pages else {"settings": state["settings"], "palette": THEMES[state["settings"]["theme"]], "plugins": [], "page": 1, "page_count": 1, "plugin_count": 0, "command_count": 0, "query": query, "prefix": state["settings"]["prefix"] or "/"}
                result = {"html": render_html(view), "page": view["page"], "pages": view["page_count"], "plugin_count": view["plugin_count"], "command_count": view["command_count"]}
            else:
                self.respond({"message": "Not found"}, status=404)
                return
            self.respond(result)
        except RevisionConflict as exc:
            self.respond({"message": str(exc)}, status=409)
        except (ValueError, TypeError) as exc:
            self.respond({"message": str(exc)}, status=400)
        except OSError:
            self.respond({"message": "本地演示配置写入失败"}, status=500)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    print(f"本地菜单编辑器（示例数据）：http://127.0.0.1:{args.port}/demo/", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        pass
