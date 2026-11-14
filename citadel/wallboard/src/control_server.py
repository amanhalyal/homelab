#!/usr/bin/env python3
"""Authenticated tailnet API for Home Assistant; mutations stay in heimdall_actions."""
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import heimdall_actions as ha

STATE_LOCK = threading.RLock()
TOKEN = ""

def build_state():
    schedule = ha.load_schedule()
    rotation = ha.load_rotation()
    active_block = ha.active_schedule_block(schedule.get("blocks", [])) if schedule.get("enabled") else None
    degraded_components = []
    try:
        brightness = ha.get_brightness_pct()
    except Exception:
        brightness = None
    try:
        monitor_on = ha.monitor_status()
    except Exception:
        monitor_on = None
    try:
        tasks = ha.load_tasks()
    except Exception as exc:
        # Kanban is a remote dependency on Legion. A transient network or API
        # failure must not take down the whole state endpoint.
        print(f"DEGRADED: kanban state unavailable: {exc}", flush=True)
        tasks = {"todo": [], "doing": [], "on_hold": [], "done": []}
        degraded_components.append("kanban")
    return {
        "current_page": ha.get_current_page(),
        "brightness": brightness,
        "monitor_on": monitor_on,
        "rotation": rotation,
        "schedule": schedule,
        "schedule_active_block": active_block,
        "tasks": tasks,
        "task_ids": {col: [getattr(card, "public_id", None) for card in cards] for col, cards in tasks.items()},
        "nexus": ha.nexus_status(),
        "degraded_components": degraded_components,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "HeimdallControl/1.0"

    def _send_bytes(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload):
        self._send_bytes(status, "application/json", json.dumps(payload).encode())

    def _authenticated(self):
        return bool(TOKEN) and hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + TOKEN
        )

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            self._send_json(200, {"ok": True})
            return
        if not self._authenticated():
            self._send_json(401, {"error": "unauthorized"})
            return
        try:
            with STATE_LOCK:
                if path == "/api/state":
                    self._send_json(200, build_state())
                elif path == "/api/screenshot":
                    self._send_bytes(200, "image/png", Path(ha.capture_heimdall()).read_bytes())
                else:
                    self._send_json(404, {"error": "not found"})
        except Exception as exc:
            self._send_json(503, {"error": str(exc)})

    def do_POST(self):
        if not self._authenticated():
            self._send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError("JSON body must be 1-16384 bytes")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict): raise ValueError("JSON object required")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return
        with STATE_LOCK:
            self._dispatch(self.path.split("?")[0], body)

    def _dispatch(self, path, body):
        try:
            if path == "/api/display":
                ha.set_current_page(body["page"])

            elif path == "/api/brightness":
                pct = int(body["pct"])
                if not 10 <= pct <= 100: raise ValueError("Brightness must be 10-100")
                ha.set_brightness_pct(pct)

            elif path == "/api/monitor":
                if not isinstance(body.get("on"), bool):
                    raise ValueError("on must be a boolean")
                ha.monitor_set(body["on"])

            elif path == "/api/rotation":
                action = body.get("action")
                if action == "on":
                    interval = body.get("interval_seconds")
                    if interval is not None and (not isinstance(interval, int) or not 10 <= interval <= 86400):
                        raise ValueError("Rotation interval must be 10-86400 seconds")
                    ha.enable_rotation(interval)
                elif action == "interval":
                    interval = int(body["interval_seconds"])
                    if not 10 <= interval <= 86400: raise ValueError("Rotation interval must be 10-86400 seconds")
                    cfg = ha.load_rotation(); cfg["interval_seconds"] = interval; ha.save_rotation(cfg)
                elif action == "off":
                    ha.disable_rotation()
                elif action == "pages":
                    ha.set_rotation_pages(body.get("pages", []))
                else:
                    raise ValueError("unknown rotation action")

            elif path == "/api/schedule":
                action = body.get("action")
                if action == "on":
                    ha.enable_schedule()
                elif action == "off":
                    ha.disable_schedule()
                elif action == "set":
                    ha.set_schedule_block(body["start"], body["page"], body.get("brightness"))
                elif action == "remove":
                    ha.remove_schedule_block(body["start"])
                else:
                    raise ValueError("unknown schedule action")

            elif path == "/api/kanban":
                action = body.get("action")
                if body.get("card_id"):
                    ha.mutate_task(action, body["card_id"], body.get("column"))
                elif action == "add":
                    ha.add_task(body["text"])
                elif action == "move":
                    ha.move_task(body["col"], int(body["idx"]))
                elif action == "resume":
                    ha.resume_task(body["col"], int(body["idx"]))
                elif action == "remove":
                    ha.remove_task(body["col"], int(body["idx"]))
                else:
                    raise ValueError("unknown kanban action")

            elif path == "/api/pc":
                out = ha.run_pc_script(body.get("action"))
                self._send_json(200, {"ok": True, "output": out, "state": build_state()})
                return

            else:
                self._send_json(404, {"error": "not found"})
                return

        except (ValueError, KeyError) as e:
            self._send_json(400, {"error": str(e)})
            return
        except Exception as e:
            self._send_json(500, {"error": str(e)})
            return

        self._send_json(200, {"ok": True, "state": build_state()})

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


def main():
    global TOKEN
    TOKEN = Path(os.environ["HEIMDALL_TOKEN_FILE"]).read_text().strip()
    if len(TOKEN) < 32: raise RuntimeError("API token must contain at least 32 characters")
    host = os.environ["HEIMDALL_BIND_HOST"]  # the host's tailnet address, never 0.0.0.0
    port = int(os.environ.get("HEIMDALL_PORT", "8766"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Heimdall control API listening on {host}:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
