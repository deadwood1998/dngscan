# SPDX-License-Identifier: GPL-3.0-or-later
"""Localhost HTTP server for the dngscan web GUI."""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import dngscan as dg
from dngscan.debug_util import maybe_print_exc

from .page import render_page
from .service import list_dir, prepare_preview, raw9_support, run_export_isolated, run_preview


def reveal_path(params: dict) -> dict:
    path = Path(str(params.get("path", ""))).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{path}")
    result = subprocess.run(["open", "-R", str(path)], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or "open -R failed")
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = render_page(str(_default_dir()))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/list":
            q = parse_qs(parsed.query)
            self._json(list_dir(q.get("dir", [""])[0]))
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in ("/export", "/preview", "/prepare", "/raw9-support", "/reveal"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            params = json.loads(self.rfile.read(length) or b"{}")
            if path == "/preview":
                result = run_preview(params)
            elif path == "/export":
                result = run_export_isolated(params)
            elif path == "/prepare":
                result = prepare_preview(params)
            elif path == "/raw9-support":
                result = raw9_support(params)
            else:
                result = reveal_path(params)
            self._json(result)
        except Exception as exc:  # surface any pipeline error to the UI
            maybe_print_exc()
            self._json({"ok": False, "error": str(exc)}, code=200)

    def log_message(self, fmt: str, *args: object) -> None:  # keep the console quiet
        return


def _default_dir() -> Path:
    pics = Path.home() / "Pictures"
    return pics if pics.is_dir() else Path.home()


def main() -> int:
    if dg.IMPORT_ERRORS:
        print("警告：dngscan 依赖未就绪，导出会失败。请先安装 rawpy/numpy/matplotlib/pillow：")
        print("  " + "\n  ".join(dg.IMPORT_ERRORS))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    url = f"http://127.0.0.1:{port}/"
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"dngscan GUI: {url}  (Ctrl+C 退出)")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    return 0
