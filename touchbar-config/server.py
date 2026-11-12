#!/usr/bin/env python3
"""touchbar-config: local GUI for ~/.config/big-dfr/config.toml.

Serves a single page on 127.0.0.1 (random port, secret token in the URL) and
opens it in the browser. The page edits the config as JSON; saving writes it
back as TOML (with a timestamped backup) and the daemon live-reloads it.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tbtoml  # noqa: E402

CONF_DIR = tbtoml.CONFIG.parent
META = CONF_DIR / "touchbar-config.json"  # GUI-only data (layer names)
ICON_DIRS = [CONF_DIR, Path("/etc/big-dfr"), Path("/usr/share/big-dfr")]
CACHE = Path("/var/cache/big-dfr")
BACKUPS = CONF_DIR / "backups"
TOKEN = secrets.token_urlsafe(16)
NAME_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,80}$")
last_seen = time.time()


def header_of(text):
    return "\n".join(l[2:] for l in text.splitlines() if l.startswith("# ")) or None


def list_icons():
    seen = {}
    for d in ICON_DIRS:
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.suffix in (".svg", ".png") and p.stem not in seen:
                    seen[p.stem] = str(p.parent)
    return sorted(seen)


def find_icon(name):
    if not NAME_RE.match(name):
        return None
    for d in ICON_DIRS:
        for ext in (".svg", ".png"):
            p = d / (name + ext)
            if p.is_file():
                return p
    return None


def capture_frame():
    """Ask the daemon to dump frames, force a redraw by touching the config."""
    req = CACHE / "dump-request"
    frame = CACHE / "frame.png"
    try:
        before = frame.stat().st_mtime if frame.exists() else 0
        req.touch()
        os.utime(tbtoml.CONFIG)
        deadline = time.time() + 4
        while time.time() < deadline:
            time.sleep(0.25)
            if frame.exists() and frame.stat().st_mtime > before:
                time.sleep(1.2)  # let widgets settle; later frames overwrite
                break
    finally:
        try:
            req.unlink()
        except FileNotFoundError:
            pass
    return frame.read_bytes() if frame.exists() else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        global last_seen
        host = self.headers.get("Host", "")
        if not host.startswith(("127.0.0.1:", "localhost:")):
            return None
        parts = urlparse(self.path).path.split("/", 2)
        if len(parts) < 2 or not secrets.compare_digest(parts[1], TOKEN):
            return None
        last_seen = time.time()
        return "/" + (parts[2] if len(parts) > 2 else "")

    def do_GET(self):
        path = self.authed()
        if path is None:
            return self.send(403, "forbidden", "text/plain")
        if path == "/":
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/config":
            text = tbtoml.CONFIG.read_text()
            meta = json.loads(META.read_text()) if META.exists() else {}
            return self.send(200, {"config": tomllib.loads(text), "meta": meta,
                                   "header": header_of(text)})
        if path == "/api/icons":
            return self.send(200, list_icons())
        if path.startswith("/icon/"):
            p = find_icon(unquote(path[6:]))
            if not p:
                return self.send(404, "no icon", "text/plain")
            ctype = "image/svg+xml" if p.suffix == ".svg" else "image/png"
            return self.send(200, p.read_bytes(), ctype)
        if path == "/api/frame":
            png = capture_frame()
            if png is None:
                return self.send(503, "no frame", "text/plain")
            return self.send(200, png, "image/png")
        if path == "/api/sleep":  # test hook for ?shot screenshots
            time.sleep(4)
            return self.send(200, b"GIF89a\x01\x00\x01\x00\x00\x00\x00;", "image/gif")
        if path == "/api/ping":
            return self.send(200, {"ok": True})
        return self.send(404, "not found", "text/plain")

    def do_POST(self):
        path = self.authed()
        if path is None:
            return self.send(403, "forbidden", "text/plain")
        n = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(n) or b"{}")
        if path == "/api/config":
            try:
                text = tbtoml.dump(data["config"], header=data.get("header"))
            except Exception as e:  # noqa: BLE001 - report any write problem
                return self.send(400, {"error": str(e)})
            BACKUPS.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(tbtoml.CONFIG, BACKUPS / f"config-{stamp}.toml")
            olds = sorted(BACKUPS.glob("config-*.toml"))
            for old in olds[:-30]:
                old.unlink()
            tmp = tbtoml.CONFIG.with_suffix(".toml.tmp")
            tmp.write_text(text)
            tmp.replace(tbtoml.CONFIG)
            META.write_text(json.dumps(data.get("meta", {}), indent=2))
            return self.send(200, {"ok": True, "backup": f"backups/config-{stamp}.toml"})
        if path == "/api/quit":
            self.send(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        return self.send(404, "not found", "text/plain")


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{srv.server_address[1]}/{TOKEN}/"
    print(f"touchbar-config: {url}", flush=True)

    def idle_watch():  # stop when the page has been gone for 10 minutes
        while True:
            time.sleep(30)
            if time.time() - last_seen > 600:
                srv.shutdown()
                return

    threading.Thread(target=idle_watch, daemon=True).start()
    if "--no-browser" not in sys.argv:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    srv.serve_forever()


if __name__ == "__main__":
    main()
