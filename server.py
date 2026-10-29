#!/usr/bin/env python3
"""Local Disk Persistence Server & REST API for Math Archive.

Zero external dependencies: uses Python standard library http.server.
Handles static file serving, REST API endpoints for data persistence,
atomic disk writes, and dual-sync into index.html embedded script tags.
"""

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
INDEX_HTML = BASE_DIR / "index.html"
DOMAINS = ["combinatorics", "algebra", "number-theory", "geometry", "analysis"]

# Concurrency lock for thread-safe file operations
WRITE_LOCK = threading.Lock()

# Global dual-sync enable toggle
ENABLE_DUAL_SYNC = True


def atomic_write(file_path: Path, content_str: str) -> None:
    """Write text content to file atomically with fsync and Windows lock retry."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(content_str)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(5):
        try:
            os.replace(temp_path, file_path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    os.replace(temp_path, file_path)


def atomic_write_json(file_path: Path, data: dict, indent: int = 2) -> None:
    """Write JSON data to file atomically."""
    serialized = json.dumps(data, ensure_ascii=False, indent=indent)
    atomic_write(file_path, serialized)


# In-memory cache for index.html dual-sync to eliminate Windows/OneDrive lock contention latency
INDEX_HTML_CACHE = None
INDEX_HTML_MTIME = 0.0


def dual_sync_update_html(script_id: str, data: dict) -> None:
    """Keep embedded <script type="application/json" id="..."> in index.html in sync."""
    global INDEX_HTML_CACHE, INDEX_HTML_MTIME
    if not ENABLE_DUAL_SYNC or not INDEX_HTML.exists():
        return
    try:
        raw_json = json.dumps(data, ensure_ascii=False)
        # Escape closing script tags per HTML5 specification (case-insensitive without modifying payload casing)
        escaped_json = re.sub(r'</(?=script)', r'<\\/', raw_json, flags=re.IGNORECASE)

        current_mtime = INDEX_HTML.stat().st_mtime
        if INDEX_HTML_CACHE is None or current_mtime > INDEX_HTML_MTIME:
            content = INDEX_HTML.read_text(encoding="utf-8")
        else:
            content = INDEX_HTML_CACHE

        pattern = rf'<script\s+[^>]*id=[\x22\x27]{re.escape(script_id)}[\x22\x27][^>]*>(.*?)</script>'
        match = re.search(pattern, content, flags=re.DOTALL)
        if match:
            new_content = content[:match.start(1)] + escaped_json + content[match.end(1):]
        else:
            tag = f'\n  <script type="application/json" id="{script_id}">{escaped_json}</script>\n'
            if "</body>" in content:
                new_content = content.replace("</body>", f"{tag}</body>")
            else:
                new_content = content + tag

        atomic_write(INDEX_HTML, new_content)
        INDEX_HTML_CACHE = new_content
        INDEX_HTML_MTIME = INDEX_HTML.stat().st_mtime
    except Exception as err:
        sys.stderr.write(f"[Dual-Sync Error] {err}\n")


def validate_domain(domain: str) -> bool:
    """Validate domain against whitelist and reject path traversal attempts."""
    if not domain or not isinstance(domain, str):
        return False
    if ".." in domain or "/" in domain or "\\" in domain:
        return False
    return domain in DOMAINS


def normalize_task(task: dict, fallback_id: str = "1.1.1") -> dict:
    """Normalize task data structure with default values to satisfy schema."""
    t = dict(task)
    t.setdefault("id", fallback_id)
    t.setdefault("src", "")
    t.setdefault("st", "")
    try:
        t["d"] = int(t.get("d", 1))
    except (ValueError, TypeError):
        t["d"] = 1
    t.setdefault("ggb", None)
    if "sol" not in t or not isinstance(t["sol"], dict):
        t["sol"] = {"ans": "", "idea": "", "plan": ""}
    else:
        t["sol"].setdefault("ans", "")
        t["sol"].setdefault("idea", "")
        t["sol"].setdefault("plan", "")
    return t


def ensure_domain_data_initialized():
    """Ensure data directory and domain starter schemas exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for dom in DOMAINS:
        dom_meta_path = DATA_DIR / dom / "meta.json"
        if not dom_meta_path.exists():
            if dom == "combinatorics" and INDEX_HTML.exists():
                try:
                    tools_dir = BASE_DIR / "tools"
                    sys.path.insert(0, str(tools_dir))
                    import extract_data
                    extract_data.extract_all(DATA_DIR, INDEX_HTML)
                    continue
                except Exception as err:
                    sys.stderr.write(f"[Init Warning] Could not extract data: {err}\n")
            # Fallback starter template
            empty_meta = {
                "total": 0,
                "parts": ["Методы", "Типы задач", "Разделы"],
                "ch": [],
                "edges": [],
                "macros": {},
                "preface": "",
                "credit": "",
                "pos": {}
            }
            atomic_write_json(dom_meta_path, empty_meta)


class MathArchiveRequestHandler(SimpleHTTPRequestHandler):
    """HTTP Request Handler for Math Archive static assets and REST API endpoints."""

    # Explicit MIME types map for web assets
    extensions_map = SimpleHTTPRequestHandler.extensions_map.copy()
    extensions_map.update({
        "": "application/octet-stream",
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
    })

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def log_message(self, format, *args):
        """Suppress standard HTTP request logging for quiet operation."""
        pass

    def send_json(self, status_code: int, payload: dict) -> None:
        """Send JSON response with complete CORS headers."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        """Reject DELETE with 405 Method Not Allowed."""
        self.send_json(405, {"status": "error", "message": "Method Not Allowed"})

    def do_PUT(self):
        """Reject PUT with 405 Method Not Allowed."""
        self.send_json(405, {"status": "error", "message": "Method Not Allowed"})

    def do_GET(self):
        """Route GET requests: /api/* to REST handlers, else static file serving."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if not path.startswith("/api/"):
            # Serve static files from BASE_DIR
            return super().do_GET()

        # 1. Status endpoint
        if path == "/api/status":
            return self.send_json(200, {
                "status": "ok",
                "editable": True,
                "version": "1.0.0",
                "domains": DOMAINS,
            })

        # 2. Monolithic Export endpoint
        if path == "/api/export":
            return self.handle_export()

        # 3. Data retrieval endpoint
        if path == "/api/data":
            return self.handle_get_data(parsed.query)

        # Unknown /api/ path
        return self.send_json(404, {"status": "error", "message": f"API endpoint not found: {path}"})

    def do_POST(self):
        """Route POST requests for data persistence endpoints."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if not path.startswith("/api/"):
            return self.send_json(404, {"status": "error", "message": "Not found"})

        # Read request body
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length)
            body = json.loads(raw_body.decode("utf-8"))
            if not isinstance(body, dict):
                return self.send_json(400, {"status": "error", "message": "JSON body must be an object"})
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"status": "error", "message": "Malformed JSON payload"})

        endpoints = {
            "/api/save-coords": self.handle_save_coords,
            "/api/save-topic": self.handle_save_topic,
            "/api/delete-topic": self.handle_delete_topic,
            "/api/save-section": self.handle_save_section,
            "/api/delete-section": self.handle_delete_section,
            "/api/save-task": self.handle_save_task,
            "/api/delete-task": self.handle_delete_task,
            "/api/save-text": self.handle_save_text,
        }

        handler = endpoints.get(path)
        if handler:
            with WRITE_LOCK:
                return handler(body)

        return self.send_json(404, {"status": "error", "message": f"Endpoint not found: {path}"})

    # =========================================================================
    # REST API Endpoint Handlers
    # =========================================================================

    def handle_get_data(self, query_string: str):
        """GET /api/data?domain={domain}&ch={n} or GET /api/data?domain={domain}."""
        params = urllib.parse.parse_qs(query_string)
        domains = params.get("domain", [])
        if not domains:
            return self.send_json(400, {"status": "error", "message": "Missing domain query parameter"})

        domain = domains[0]
        if not validate_domain(domain):
            return self.send_json(400, {"status": "error", "message": f"Invalid domain or path traversal: {domain}"})

        chs = params.get("ch", [])
        if chs:
            try:
                ch = int(chs[0])
            except ValueError:
                return self.send_json(400, {"status": "error", "message": "Invalid chapter parameter"})

            ch_path = DATA_DIR / domain / f"ch{ch}.json"
            if not ch_path.exists():
                return self.send_json(404, {"status": "not_found", "message": f"Chapter {ch} not found in {domain}"})

            try:
                data = json.loads(ch_path.read_text(encoding="utf-8"))
                return self.send_json(200, data)
            except Exception as err:
                return self.send_json(500, {"status": "error", "message": str(err)})

        # Return domain meta.json
        meta_path = DATA_DIR / domain / "meta.json"
        if not meta_path.exists():
            return self.send_json(404, {"status": "not_found", "message": f"Meta not found for {domain}"})

        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            return self.send_json(200, data)
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    def handle_save_coords(self, body: dict):
        """POST /api/save-coords: Updates topic vertex coordinates in meta.json."""
        domain = body.get("domain")
        if not validate_domain(domain):
            return self.send_json(400, {"status": "error", "message": "Invalid or missing domain"})

        pos_updates = body.get("pos")
        if not isinstance(pos_updates, dict):
            return self.send_json(400, {"status": "error", "message": "Field 'pos' must be an object"})

        meta_path = DATA_DIR / domain / "meta.json"
        if not meta_path.exists():
            return self.send_json(404, {"status": "not_found", "message": f"Meta not found for {domain}"})

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if "pos" not in meta or not isinstance(meta["pos"], dict):
                meta["pos"] = {}

            for topic_id, coords in pos_updates.items():
                if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                    # Clamp coordinates to canvas boundaries 0..1400 and 0..910
                    x = max(0, min(1400, int(coords[0])))
                    y = max(0, min(910, int(coords[1])))
                    meta["pos"][str(topic_id)] = [x, y]

            atomic_write_json(meta_path, meta)
            script_id = "meta" if domain == "combinatorics" else f"meta-{domain}"
            dual_sync_update_html(script_id, meta)

            return self.send_json(200, {"status": "ok", "message": "Coordinates saved"})
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    def handle_save_topic(self, body: dict):
        """POST /api/save-topic: Creates or updates a topic in meta.json."""
        domain = body.get("domain")
        if not validate_domain(domain):
            return self.send_json(400, {"status": "error", "message": "Invalid or missing domain"})

        try:
            ch_num = int(body.get("ch"))
        except (ValueError, TypeError):
            return self.send_json(400, {"status": "error", "message": "Field 'ch' must be an integer"})

        title = str(body.get("t", "")).strip()
        if not title:
            return self.send_json(400, {"status": "error", "message": "Topic title 't' cannot be empty"})

        short_title = str(body.get("s", title)).strip()
        try:
            part_num = int(body.get("p", 1))
        except (ValueError, TypeError):
            part_num = 1

        pos = body.get("pos", [200, 200])
        if not (isinstance(pos, (list, tuple)) and len(pos) >= 2):
            pos = [200, 200]
        pos = [max(0, min(1400, int(pos[0]))), max(0, min(910, int(pos[1])))]

        meta_path = DATA_DIR / domain / "meta.json"
        if not meta_path.exists():
            return self.send_json(404, {"status": "not_found", "message": f"Meta not found for {domain}"})

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta.setdefault("ch", [])
            meta.setdefault("pos", {})

            # Update existing or append new topic
            existing = None
            for topic in meta["ch"]:
                if topic.get("n") == ch_num:
                    existing = topic
                    break

            if existing:
                existing["t"] = title
                existing["s"] = short_title
                existing["p"] = part_num
            else:
                meta["ch"].append({
                    "n": ch_num,
                    "t": title,
                    "s": short_title,
                    "p": part_num,
                    "secs": []
                })
                meta["ch"].sort(key=lambda x: x.get("n", 0))

            meta["pos"][str(ch_num)] = pos

            edges = body.get("edges")
            if isinstance(edges, list):
                meta["edges"] = edges

            # Ensure chapter JSON file exists
            ch_path = DATA_DIR / domain / f"ch{ch_num}.json"
            if not ch_path.exists():
                atomic_write_json(ch_path, {"n": ch_num, "intro": [], "secs": []})

            atomic_write_json(meta_path, meta)
            script_id = "meta" if domain == "combinatorics" else f"meta-{domain}"
            dual_sync_update_html(script_id, meta)

            return self.send_json(200, {"status": "ok", "message": "Topic saved", "ch": ch_num})
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    def handle_delete_topic(self, body: dict):
        """POST /api/delete-topic: Deletes a topic and its associated chapter JSON file."""
        domain = body.get("domain")
        if not validate_domain(domain):
            return self.send_json(400, {"status": "error", "message": "Invalid or missing domain"})

        try:
            ch_num = int(body.get("ch"))
        except (ValueError, TypeError):
            return self.send_json(400, {"status": "error", "message": "Field 'ch' must be an integer"})

        meta_path = DATA_DIR / domain / "meta.json"
        if not meta_path.exists():
            return self.send_json(404, {"status": "not_found", "message": f"Meta not found for {domain}"})

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta.setdefault("ch", [])
            meta.setdefault("pos", {})
            meta.setdefault("edges", [])

            # Check if topic exists
            target_idx = None
            for idx, topic in enumerate(meta["ch"]):
                if topic.get("n") == ch_num:
                    target_idx = idx
                    break

            if target_idx is None:
                return self.send_json(200, {"status": "not_found", "message": f"Topic {ch_num} not found"})

            del meta["ch"][target_idx]
            meta["pos"].pop(str(ch_num), None)
            meta["edges"] = [e for e in meta["edges"] if ch_num not in e]

            # Delete modular chapter file
            ch_path = DATA_DIR / domain / f"ch{ch_num}.json"
            if ch_path.exists():
                try:
                    ch_path.unlink()
                except Exception:
                    pass

            # Recalculate total tasks
            total = 0
            for topic in meta["ch"]:
                for s in topic.get("secs", []):
                    total += len(s.get("ids", []))
            meta["total"] = total

            atomic_write_json(meta_path, meta)
            script_id = "meta" if domain == "combinatorics" else f"meta-{domain}"
            dual_sync_update_html(script_id, meta)

            return self.send_json(200, {"status": "ok", "message": "Topic deleted"})
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    def handle_save_section(self, body: dict):
        """POST /api/save-section: Creates or updates a section in chapter JSON and meta.json."""
        domain = body.get("domain")
        if not validate_domain(domain):
            return self.send_json(400, {"status": "error", "message": "Invalid or missing domain"})

        try:
            ch_num = int(body.get("ch"))
            sec_num = int(body.get("sec"))
        except (ValueError, TypeError):
            return self.send_json(400, {"status": "error", "message": "Fields 'ch' and 'sec' must be integers"})

        title = str(body.get("t", f"Section {sec_num}"))
        th = body.get("th", [])
        if not isinstance(th, list):
            th = []

        ch_path = DATA_DIR / domain / f"ch{ch_num}.json"
        if not ch_path.exists():
            atomic_write_json(ch_path, {"n": ch_num, "intro": [], "secs": []})

        try:
            ch_data = json.loads(ch_path.read_text(encoding="utf-8"))
            ch_data.setdefault("secs", [])

            # Update or append section
            existing = None
            for s in ch_data["secs"]:
                if s.get("n") == sec_num:
                    existing = s
                    break

            if existing:
                existing["t"] = title
                existing["th"] = th
            else:
                ch_data["secs"].append({
                    "n": sec_num,
                    "t": title,
                    "th": th,
                    "g": []
                })
                ch_data["secs"].sort(key=lambda x: x.get("n", 0))

            atomic_write_json(ch_path, ch_data)

            # Update meta.json topic secs summary
            meta_path = DATA_DIR / domain / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                for topic in meta.get("ch", []):
                    if topic.get("n") == ch_num:
                        topic.setdefault("secs", [])
                        sec_meta = None
                        for sm in topic["secs"]:
                            if sm.get("n") == sec_num:
                                sec_meta = sm
                                break
                        if sec_meta:
                            sec_meta["t"] = title
                        else:
                            topic["secs"].append({
                                "n": sec_num,
                                "t": title,
                                "ids": [],
                                "d": []
                            })
                            topic["secs"].sort(key=lambda x: x.get("n", 0))
                        break
                atomic_write_json(meta_path, meta)
                script_id = "meta" if domain == "combinatorics" else f"meta-{domain}"
                dual_sync_update_html(script_id, meta)

            if domain == "combinatorics":
                dual_sync_update_html(f"ch{ch_num}", ch_data)

            return self.send_json(200, {"status": "ok", "message": "Section saved"})
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    def handle_delete_section(self, body: dict):
        """POST /api/delete-section: Deletes a section and re-indexes remaining sections."""
        domain = body.get("domain")
        if not validate_domain(domain):
            return self.send_json(400, {"status": "error", "message": "Invalid or missing domain"})

        try:
            ch_num = int(body.get("ch"))
            sec_num = int(body.get("sec"))
        except (ValueError, TypeError):
            return self.send_json(400, {"status": "error", "message": "Fields 'ch' and 'sec' must be integers"})

        ch_path = DATA_DIR / domain / f"ch{ch_num}.json"
        if not ch_path.exists():
            return self.send_json(200, {"status": "not_found", "message": f"Chapter {ch_num} not found"})

        try:
            ch_data = json.loads(ch_path.read_text(encoding="utf-8"))
            ch_data.setdefault("secs", [])

            target_idx = None
            for idx, s in enumerate(ch_data["secs"]):
                if s.get("n") == sec_num:
                    target_idx = idx
                    break

            if target_idx is None:
                return self.send_json(200, {"status": "not_found", "message": f"Section {sec_num} not found"})

            del ch_data["secs"][target_idx]

            # Re-index remaining sections
            for idx, s in enumerate(ch_data["secs"]):
                s["n"] = idx + 1

            atomic_write_json(ch_path, ch_data)

            # Update meta.json
            meta_path = DATA_DIR / domain / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                for topic in meta.get("ch", []):
                    if topic.get("n") == ch_num:
                        topic.setdefault("secs", [])
                        topic["secs"] = [s for s in topic["secs"] if s.get("n") != sec_num]
                        for idx, s in enumerate(topic["secs"]):
                            s["n"] = idx + 1
                        break

                # Recalculate total tasks across all remaining sections
                total_tasks = 0
                for topic in meta.get("ch", []):
                    for sm in topic.get("secs", []):
                        total_tasks += len(sm.get("ids", []))
                meta["total"] = total_tasks

                atomic_write_json(meta_path, meta)
                script_id = "meta" if domain == "combinatorics" else f"meta-{domain}"
                dual_sync_update_html(script_id, meta)

            if domain == "combinatorics":
                dual_sync_update_html(f"ch{ch_num}", ch_data)

            return self.send_json(200, {"status": "ok", "message": "Section deleted"})
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    def handle_save_task(self, body: dict):
        """POST /api/save-task: Creates or updates a problem in chapter JSON and meta.json."""
        domain = body.get("domain")
        if not validate_domain(domain):
            return self.send_json(400, {"status": "error", "message": "Invalid or missing domain"})

        try:
            ch_num = int(body.get("ch"))
            sec_num = int(body.get("sec"))
        except (ValueError, TypeError):
            return self.send_json(400, {"status": "error", "message": "Fields 'ch' and 'sec' must be integers"})

        task_data = body.get("task")
        if not isinstance(task_data, dict):
            return self.send_json(400, {"status": "error", "message": "Field 'task' must be an object"})

        # Validate difficulty range 1..8
        raw_diff = task_data.get("d", 1)
        if isinstance(raw_diff, bool) or (isinstance(raw_diff, float) and not raw_diff.is_integer()):
            return self.send_json(400, {"status": "error", "message": "Difficulty 'd' must be an integer between 1 and 8"})
        try:
            diff = int(raw_diff)
            if not (1 <= diff <= 8):
                return self.send_json(400, {"status": "error", "message": "Difficulty 'd' must be between 1 and 8"})
        except (ValueError, TypeError):
            return self.send_json(400, {"status": "error", "message": "Difficulty 'd' must be an integer between 1 and 8"})
        task_data["d"] = diff

        ch_path = DATA_DIR / domain / f"ch{ch_num}.json"
        if not ch_path.exists():
            atomic_write_json(ch_path, {"n": ch_num, "intro": [], "secs": []})

        try:
            ch_data = json.loads(ch_path.read_text(encoding="utf-8"))
            ch_data.setdefault("secs", [])

            # Locate target section
            target_sec = None
            for s in ch_data["secs"]:
                if s.get("n") == sec_num:
                    target_sec = s
                    break

            if target_sec is None:
                # Auto-create section if missing
                target_sec = {"n": sec_num, "t": f"Section {sec_num}", "th": [], "g": []}
                ch_data["secs"].append(target_sec)
                ch_data["secs"].sort(key=lambda x: x.get("n", 0))

            target_sec.setdefault("g", [])
            if not target_sec["g"]:
                target_sec["g"].append({"t": "Задачи", "p": []})

            norm_task = normalize_task(task_data, fallback_id=f"{ch_num}.{sec_num}.1")

            # Look for existing task with same id across all groups
            replaced = False
            for grp in target_sec["g"]:
                grp.setdefault("p", [])
                for idx, p in enumerate(grp["p"]):
                    if p.get("id") == norm_task["id"]:
                        grp["p"][idx] = norm_task
                        replaced = True
                        break
                if replaced:
                    break

            if not replaced:
                target_sec["g"][0]["p"].append(norm_task)

            atomic_write_json(ch_path, ch_data)

            # Update meta.json section task metadata and recalculate total
            meta_path = DATA_DIR / domain / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                for topic in meta.get("ch", []):
                    if topic.get("n") == ch_num:
                        topic.setdefault("secs", [])
                        sec_meta = None
                        for sm in topic["secs"]:
                            if sm.get("n") == sec_num:
                                sec_meta = sm
                                break
                        if not sec_meta:
                            sec_meta = {"n": sec_num, "t": target_sec.get("t", f"Section {sec_num}"), "ids": [], "d": []}
                            topic["secs"].append(sec_meta)
                            topic["secs"].sort(key=lambda x: x.get("n", 0))

                        # Refresh ids and d for this section
                        all_ids = []
                        all_ds = []
                        for grp in target_sec.get("g", []):
                            for p in grp.get("p", []):
                                all_ids.append(p.get("id", ""))
                                all_ds.append(p.get("d", 1))
                        sec_meta["ids"] = all_ids
                        sec_meta["d"] = all_ds
                        break

                # Recalculate total across all topics
                total_tasks = 0
                for topic in meta.get("ch", []):
                    for sm in topic.get("secs", []):
                        total_tasks += len(sm.get("ids", []))
                meta["total"] = total_tasks

                atomic_write_json(meta_path, meta)
                script_id = "meta" if domain == "combinatorics" else f"meta-{domain}"
                dual_sync_update_html(script_id, meta)

            if domain == "combinatorics":
                dual_sync_update_html(f"ch{ch_num}", ch_data)

            return self.send_json(200, {"status": "ok", "message": "Task saved"})
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    def handle_delete_task(self, body: dict):
        """POST /api/delete-task: Deletes a problem from chapter JSON and meta.json."""
        domain = body.get("domain")
        if not validate_domain(domain):
            return self.send_json(400, {"status": "error", "message": "Invalid or missing domain"})

        try:
            ch_num = int(body.get("ch"))
            sec_num = int(body.get("sec"))
        except (ValueError, TypeError):
            return self.send_json(400, {"status": "error", "message": "Fields 'ch' and 'sec' must be integers"})

        task_id = str(body.get("id", "")).strip()
        if not task_id:
            return self.send_json(400, {"status": "error", "message": "Field 'id' cannot be empty"})

        ch_path = DATA_DIR / domain / f"ch{ch_num}.json"
        if not ch_path.exists():
            return self.send_json(200, {"status": "not_found", "message": f"Chapter {ch_num} not found"})

        try:
            ch_data = json.loads(ch_path.read_text(encoding="utf-8"))
            ch_data.setdefault("secs", [])

            deleted = False
            for s in ch_data["secs"]:
                if s.get("n") == sec_num:
                    for grp in s.get("g", []):
                        orig_len = len(grp.get("p", []))
                        grp["p"] = [p for p in grp.get("p", []) if p.get("id") != task_id]
                        if len(grp["p"]) < orig_len:
                            deleted = True
                    break

            if not deleted:
                return self.send_json(200, {"status": "not_found", "message": f"Task {task_id} not found"})

            atomic_write_json(ch_path, ch_data)

            # Update meta.json
            meta_path = DATA_DIR / domain / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                for topic in meta.get("ch", []):
                    if topic.get("n") == ch_num:
                        for sm in topic.get("secs", []):
                            if sm.get("n") == sec_num and task_id in sm.get("ids", []):
                                idx = sm["ids"].index(task_id)
                                del sm["ids"][idx]
                                if "d" in sm and len(sm["d"]) > idx:
                                    del sm["d"][idx]
                        break

                total_tasks = 0
                for topic in meta.get("ch", []):
                    for sm in topic.get("secs", []):
                        total_tasks += len(sm.get("ids", []))
                meta["total"] = total_tasks

                atomic_write_json(meta_path, meta)
                script_id = "meta" if domain == "combinatorics" else f"meta-{domain}"
                dual_sync_update_html(script_id, meta)

            if domain == "combinatorics":
                dual_sync_update_html(f"ch{ch_num}", ch_data)

            return self.send_json(200, {"status": "ok", "message": "Task deleted"})
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    def handle_save_text(self, body: dict):
        """POST /api/save-text: Updates theory cards, intro paragraphs, or metadata texts."""
        domain = body.get("domain")
        if not validate_domain(domain):
            return self.send_json(400, {"status": "error", "message": "Invalid or missing domain"})

        text_type = str(body.get("type", "theory")).lower()
        field = str(body.get("field", "html"))
        content = str(body.get("content", ""))

        try:
            ch_num = int(body.get("ch", 1))
            sec_num = int(body.get("sec", 1))
        except (ValueError, TypeError):
            ch_num = 1
            sec_num = 1

        try:
            if text_type == "meta":
                meta_path = DATA_DIR / domain / "meta.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    meta[field] = content
                    atomic_write_json(meta_path, meta)
                    script_id = "meta" if domain == "combinatorics" else f"meta-{domain}"
                    dual_sync_update_html(script_id, meta)
                return self.send_json(200, {"status": "ok", "message": "Text saved"})

            ch_path = DATA_DIR / domain / f"ch{ch_num}.json"
            if not ch_path.exists():
                atomic_write_json(ch_path, {"n": ch_num, "intro": [], "secs": []})

            ch_data = json.loads(ch_path.read_text(encoding="utf-8"))

            if text_type == "intro":
                ch_data.setdefault("intro", [])
                if not ch_data["intro"]:
                    ch_data["intro"].append({"title": "Введение", "html": ""})
                ch_data["intro"][0][field] = content
            elif text_type == "theory":
                ch_data.setdefault("secs", [])
                target_sec = None
                for s in ch_data["secs"]:
                    if s.get("n") == sec_num:
                        target_sec = s
                        break
                if not target_sec:
                    target_sec = {"n": sec_num, "t": f"Section {sec_num}", "th": [], "g": []}
                    ch_data["secs"].append(target_sec)
                target_sec.setdefault("th", [])
                if not target_sec["th"]:
                    target_sec["th"].append({"title": "Теория", "html": ""})
                target_sec["th"][0][field] = content
            else:
                # Generic fallback
                ch_data[field] = content

            atomic_write_json(ch_path, ch_data)
            if domain == "combinatorics":
                dual_sync_update_html(f"ch{ch_num}", ch_data)

            return self.send_json(200, {"status": "ok", "message": "Text saved"})
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    def handle_export(self):
        """GET /api/export: Exports complete math archive in structured JSON."""
        try:
            archive = {
                "version": "1.0.0",
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "domains": {}
            }

            for dom in DOMAINS:
                dom_dir = DATA_DIR / dom
                dom_data = {"meta": None, "chapters": {}}
                if dom_dir.exists():
                    meta_path = dom_dir / "meta.json"
                    if meta_path.exists():
                        try:
                            dom_data["meta"] = json.loads(meta_path.read_text(encoding="utf-8"))
                        except Exception:
                            pass
                    for ch_file in dom_dir.glob("ch*.json"):
                        try:
                            ch_data = json.loads(ch_file.read_text(encoding="utf-8"))
                            dom_data["chapters"][ch_file.stem] = ch_data
                        except Exception:
                            pass
                archive["domains"][dom] = dom_data

            return self.send_json(200, archive)
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})


def create_server(bind_host: str = "0.0.0.0", port: int = 8000) -> ThreadingHTTPServer:
    """Create configured ThreadingHTTPServer instance."""
    server = ThreadingHTTPServer((bind_host, port), MathArchiveRequestHandler)
    server.daemon_threads = True
    return server


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Math Archive Local Disk Persistence Server")
    parser.add_argument("--port", "-p", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--bind", "-b", type=str, default="0.0.0.0", help="Address to bind to (default: 0.0.0.0)")
    parser.add_argument("--no-dual-sync", action="store_true", help="Disable dual-sync into index.html")
    return parser.parse_args()


def main():
    args = parse_args()

    global ENABLE_DUAL_SYNC
    if args.no_dual_sync:
        ENABLE_DUAL_SYNC = False

    ensure_domain_data_initialized()

    server = create_server(args.bind, args.port)

    def handle_sigterm(signum, frame):
        server.server_close()
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, handle_sigterm)
        signal.signal(signal.SIGTERM, handle_sigterm)
    except Exception:
        pass

    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
