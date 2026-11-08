"""Web del informe: página, API JSON para la página y acciones."""
import json
import logging
import urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .logbuf import BUFFER
from .verify import VERDICTS

log = logging.getLogger("doctor.web")

PAGE = (Path(__file__).with_name("page.html")).read_text(encoding="utf-8")
ICON = (Path(__file__).with_name("favicon.svg")).read_text(encoding="utf-8")


def make_handler(cfg, store, worker):
    page = (PAGE.replace("__VERDICTS__", json.dumps(VERDICTS, ensure_ascii=False))
                .replace("__BAZARR__", json.dumps(cfg.bazarr_url)))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

        def _send(self, code, body, ctype):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/api/results":
                body = {"state": worker.public_state(), "items": store.all()}
                self._send(200, json.dumps(body, ensure_ascii=False), "application/json; charset=utf-8")
            elif path == "/api/log":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                try:
                    after = int(q.get("after", ["0"])[0])
                except ValueError:
                    after = 0
                self._send(200, json.dumps({"lines": BUFFER.since(after)}, ensure_ascii=False),
                           "application/json; charset=utf-8")
            elif path == "/api/settings":
                self._send(200, json.dumps(cfg.editable(), ensure_ascii=False), "application/json; charset=utf-8")
            elif path in ("/favicon.svg", "/favicon.ico"):
                self._send(200, ICON, "image/svg+xml")
            elif path == "/health":
                self._send(200, "ok", "text/plain")
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            if url.path == "/api/scan":
                summary = worker.scan_now()
                return self._send(200, json.dumps({"ok": True, "message": summary}, ensure_ascii=False),
                                  "application/json; charset=utf-8")
            elif url.path == "/api/run":
                key = urllib.parse.parse_qs(url.query).get("key", [None])[0]
                if key:
                    if not worker.request_one(key):
                        return self._send(404, "no existe", "text/plain")
                else:
                    worker.request_all()
            elif url.path == "/api/stop":
                worker.stop()
            elif url.path == "/api/retry-errors":
                for item in store.all():
                    if item["verdict"] == "error":
                        worker.request_one(item["key"])
            elif url.path == "/api/action":
                q = urllib.parse.parse_qs(url.query)
                ok, msg = worker.perform(q.get("key", [""])[0], q.get("do", [""])[0])
                return self._send(200 if ok else 409, json.dumps({"ok": ok, "message": msg}, ensure_ascii=False),
                                  "application/json; charset=utf-8")
            elif url.path == "/api/settings":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    values = json.loads(self.rfile.read(length) or b"{}")
                    clean = cfg.apply(values)
                except Exception as e:  # noqa: BLE001
                    return self._send(400, json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                store.save_settings(clean)
                log.info("Ajustes guardados: %s", ", ".join(f"{k}={v}" for k, v in clean.items()))
                worker.request_scan()
                return self._send(200, json.dumps({"ok": True, "settings": cfg.editable()}, ensure_ascii=False),
                                  "application/json; charset=utf-8")
            else:
                return self._send(404, "not found", "text/plain")
            self._send(204, "", "text/plain")

    return Handler


def serve(cfg, store, worker):
    server = ThreadingHTTPServer(("0.0.0.0", cfg.port), make_handler(cfg, store, worker))
    log.info("Web del informe en el puerto %d", cfg.port)
    server.serve_forever()
