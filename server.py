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
        match = re.search(pattern, content, flags=re.DOTALL | re.IGNORECASE)
        if match:
            new_content = content[:match.start(1)] + escaped_json + content[match.end(1):]
        else:
            tag = f'\n  <script id="{script_id}" type="application/json">{escaped_json}</script>\n'
            body_close_idx = content.lower().rfind("</body>")
            if body_close_idx != -1:
                new_content = content[:body_close_idx] + tag + content[body_close_idx:]
            else:
                new_content = content + tag

        atomic_write(INDEX_HTML, new_content)
        INDEX_HTML_CACHE = new_content
        INDEX_HTML_MTIME = INDEX_HTML.stat().st_mtime
    except Exception as err:
        sys.stderr.write(f"[Dual-Sync Error] {err}\n")


def sync_sheets_dual_sync() -> None:
    """Bundle sheets index and sheet files into <script id="sheets-data"> in index.html."""
    if not ENABLE_DUAL_SYNC:
        return
    sheets_dir = DATA_DIR / "sheets"
    index_path = sheets_dir / "sheets-index.json"
    sheets_index = []
    if index_path.exists():
        try:
            sheets_index = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(sheets_index, list):
                sheets_index = []
        except Exception as err:
            sys.stderr.write(f"[Dual-Sync Sheets Index Warning] {err}\n")
            sheets_index = []

    sheets_map = {}
    if sheets_dir.exists():
        for sf in sorted(sheets_dir.glob("sheet_*.json")):
            try:
                s_data = json.loads(sf.read_text(encoding="utf-8"))
                s_id = str(s_data.get("id", sf.stem.replace("sheet_", "")))
                sheets_map[s_id] = s_data
                if s_id.isdigit():
                    sheets_map[f"sheet_{s_id}"] = s_data
                elif s_id.startswith("sheet_"):
                    sheets_map[s_id[6:]] = s_data
            except Exception as err:
                sys.stderr.write(f"[Dual-Sync Sheet File Warning] Failed to read {sf.name}: {err}\n")

    bundle = {
        "index": sheets_index,
        "sheets": sheets_map,
    }
    dual_sync_update_html("sheets-data", bundle)


def sync_notes_dual_sync() -> None:
    """Bundle notes index and LaTeX note files into <script id="notes-data"> in index.html."""
    if not ENABLE_DUAL_SYNC:
        return
    notes_dir = DATA_DIR / "notes"
    index_path = notes_dir / "notes-index.json"
    notes_index = []
    if index_path.exists():
        try:
            notes_index = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(notes_index, list):
                notes_index = []
        except Exception as err:
            sys.stderr.write(f"[Dual-Sync Notes Index Warning] {err}\n")
            notes_index = []

    notes_map = {}
    if notes_dir.exists():
        for nf in sorted(notes_dir.glob("note_*.json")):
            try:
                n_data = json.loads(nf.read_text(encoding="utf-8"))
                n_id = str(n_data.get("id", nf.stem.replace("note_", "")))
                notes_map[n_id] = n_data
                if n_id.isdigit():
                    notes_map[f"note_{n_id}"] = n_data
                elif n_id.startswith("note_"):
                    notes_map[n_id[5:]] = n_data
            except Exception as err:
                sys.stderr.write(f"[Dual-Sync Note File Warning] Failed to read {nf.name}: {err}\n")

    bundle = {
        "index": notes_index,
        "notes": notes_map,
    }
    dual_sync_update_html("notes-data", bundle)


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


def ensure_sheets_and_notes_initialized():
    """Ensure data/sheets, data/notes, data/scans directories exist and prime index.html."""
    sheets_dir = DATA_DIR / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    sheets_index_path = sheets_dir / "sheets-index.json"
    if not sheets_index_path.exists():
        atomic_write_json(sheets_index_path, [])

    notes_dir = DATA_DIR / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    notes_index_path = notes_dir / "notes-index.json"
    if not notes_index_path.exists():
        atomic_write_json(notes_index_path, [])

    scans_dir = DATA_DIR / "scans"
    scans_dir.mkdir(parents=True, exist_ok=True)

    sync_sheets_dual_sync()
    sync_notes_dual_sync()


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
        ".pdf": "application/pdf",
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

        # Intercept multipart PDF upload prior to JSON decoding
        if path == "/api/upload-pdf":
            with WRITE_LOCK:
                return self.handle_upload_pdf()

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
            # Sheets Endpoints
            "/api/save-sheet-index": self.handle_save_sheet_index,
            "/api/save-sheet": self.handle_save_sheet,
            "/api/delete-sheet": self.handle_delete_sheet,
            "/api/save-sheet-task": self.handle_save_sheet_task,
            "/api/delete-sheet-task": self.handle_delete_sheet_task,
            # Notes Endpoints
            "/api/save-note": self.handle_save_note,
            "/api/delete-note": self.handle_delete_note,
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

    # =========================================================================
    # Upload and Scans Endpoints
    # =========================================================================

    def handle_upload_pdf(self):
        """POST /api/upload-pdf: Receives multipart/form-data PDF and saves to data/scans/."""
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            return self.send_json(400, {"status": "error", "message": "Expected multipart/form-data Content-Type"})

        try:
            length_header = self.headers.get("Content-Length")
            if not length_header:
                return self.send_json(400, {"status": "error", "message": "Missing Content-Length header"})
            length = int(length_header)
            if length <= 0:
                return self.send_json(400, {"status": "error", "message": "Empty upload body"})

            # Max size limit: 50MB
            max_size = 50 * 1024 * 1024
            if length > max_size:
                return self.send_json(413, {"status": "error", "message": "Payload too large (maximum 50MB)"})

            raw_body = self.rfile.read(length)
            if len(raw_body) < length:
                return self.send_json(400, {"status": "error", "message": "Incomplete request body"})
        except Exception as err:
            return self.send_json(400, {"status": "error", "message": f"Error reading upload body: {err}"})

        try:
            import email
            from email.policy import default

            mime_header = f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
            msg = email.message_from_bytes(mime_header + raw_body, policy=default)

            if not msg.is_multipart():
                return self.send_json(400, {"status": "error", "message": "Expected multipart form payload"})

            found_filename = None
            found_file = None
            for part in msg.iter_parts():
                fn = part.get_filename()
                if fn:
                    found_filename = fn
                    found_file = part.get_payload(decode=True)
                    break

            if found_filename is None or found_file is None:
                return self.send_json(400, {"status": "error", "message": "No file uploaded in form-data"})

            if len(found_file) == 0:
                return self.send_json(400, {"status": "error", "message": "Empty file uploaded"})

            # 1. Path traversal elimination
            raw_base = found_filename.replace('\\', '/').split('/')[-1]
            raw_base = re.sub(r'^[a-zA-Z]:', '', raw_base)
            p = Path(raw_base)

            # 2. Extension validation
            if p.suffix.lower() != ".pdf":
                return self.send_json(400, {"status": "error", "message": "Only PDF files (.pdf) are permitted"})

            # 3. Magic bytes validation
            if not found_file.startswith(b"%PDF-"):
                return self.send_json(400, {"status": "error", "message": "File is not a valid PDF (missing %PDF- header)"})

            # 4. Filename sanitization (alphanumeric, underscore, hyphen, dot)
            clean_stem = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', p.stem)
            clean_stem = re.sub(r'_+', '_', clean_stem)
            clean_stem = re.sub(r'\.+', '.', clean_stem).strip('._')

            # Windows reserved device names
            reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
            if not clean_stem or clean_stem.upper() in reserved:
                clean_stem = f"scan_{int(time.time())}"

            final_filename = f"{clean_stem}.pdf"

            scans_dir = DATA_DIR / "scans"
            scans_dir.mkdir(parents=True, exist_ok=True)
            target_path = (scans_dir / final_filename).resolve()

            # Defense-in-depth: confirm target is strictly within scans_dir
            if not str(target_path).startswith(str(scans_dir.resolve())):
                return self.send_json(400, {"status": "error", "message": "Illegal file path"})

            # Atomic binary write with Windows lock retry
            temp_path = target_path.with_suffix(".tmp")
            with open(temp_path, "wb") as f:
                f.write(found_file)
                f.flush()
                os.fsync(f.fileno())

            for attempt in range(5):
                try:
                    os.replace(temp_path, target_path)
                    break
                except PermissionError:
                    time.sleep(0.05 * (attempt + 1))
            else:
                os.replace(temp_path, target_path)

            return self.send_json(200, {
                "status": "ok",
                "message": "PDF uploaded successfully",
                "filename": final_filename,
                "file_path": f"/data/scans/{final_filename}",
                "size": len(found_file)
            })
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    # =========================================================================
    # Sheets Endpoints
    # =========================================================================

    def handle_save_sheet_index(self, body: dict):
        """POST /api/save-sheet-index: Save or update the sheets index registry."""
        sheets_dir = DATA_DIR / "sheets"
        sheets_dir.mkdir(parents=True, exist_ok=True)
        index_path = sheets_dir / "sheets-index.json"

        if "index" in body:
            new_index = body.get("index")
            if not isinstance(new_index, list):
                return self.send_json(400, {"status": "error", "message": "Field 'index' must be a list"})
            for item in new_index:
                if not isinstance(item, dict):
                    return self.send_json(400, {"status": "error", "message": "Each sheet item must be an object"})
                s_id = str(item.get("id", "")).strip()
                title = str(item.get("title", "")).strip()
                if not s_id or not title:
                    return self.send_json(400, {"status": "error", "message": "Each sheet item must have 'id' and 'title'"})
                if ".." in s_id or "/" in s_id or "\\" in s_id:
                    return self.send_json(400, {"status": "error", "message": "Invalid sheet 'id'"})
            atomic_write_json(index_path, new_index)
        elif "sheet" in body:
            sheet_meta = body.get("sheet")
            if not isinstance(sheet_meta, dict):
                return self.send_json(400, {"status": "error", "message": "Field 'sheet' must be an object"})
            s_id = str(sheet_meta.get("id", "")).strip()
            if not s_id or ".." in s_id or "/" in s_id or "\\" in s_id:
                return self.send_json(400, {"status": "error", "message": "Invalid or missing sheet 'id'"})
            current_index = []
            if index_path.exists():
                try:
                    current_index = json.loads(index_path.read_text(encoding="utf-8"))
                except Exception:
                    current_index = []
            replaced = False
            for idx, item in enumerate(current_index):
                if str(item.get("id")) == s_id:
                    current_index[idx].update(sheet_meta)
                    replaced = True
                    break
            if not replaced:
                current_index.append(sheet_meta)
            atomic_write_json(index_path, current_index)
        else:
            return self.send_json(400, {"status": "error", "message": "Payload must contain 'index' or 'sheet'"})

        sync_sheets_dual_sync()
        return self.send_json(200, {"status": "ok", "message": "Sheets index saved"})

    def handle_save_sheet(self, body: dict):
        """POST /api/save-sheet: Creates or updates full sheet file and updates sheets-index.json."""
        sheet_data = body.get("sheet")
        if not isinstance(sheet_data, dict):
            return self.send_json(400, {"status": "error", "message": "Field 'sheet' must be an object"})

        sheet_id = str(sheet_data.get("id", "")).strip()
        if not sheet_id or ".." in sheet_id or "/" in sheet_id or "\\" in sheet_id:
            return self.send_json(400, {"status": "error", "message": "Invalid or missing sheet 'id'"})

        title = str(sheet_data.get("title", "")).strip()
        if not title:
            return self.send_json(400, {"status": "error", "message": "Sheet 'title' cannot be empty"})

        category = str(sheet_data.get("category", "кружок")).strip()
        valid_cats = ["кружок", "олимпиада", "матбой", "авторская"]
        if category not in valid_cats:
            category = "кружок"

        desc = str(sheet_data.get("desc") or sheet_data.get("description", ""))

        tasks = sheet_data.get("tasks", [])
        if not isinstance(tasks, list):
            tasks = []

        norm_tasks = []
        for idx, t in enumerate(tasks):
            if isinstance(t, dict):
                norm_task = normalize_task(t, fallback_id=f"s{sheet_id}.{idx+1}")
                diff = norm_task.get("d", 1)
                try:
                    diff_int = int(diff)
                except (ValueError, TypeError):
                    diff_int = 1
                norm_task["d"] = max(1, min(8, diff_int))
                norm_tasks.append(norm_task)

        now_date = time.strftime("%Y-%m-%d", time.localtime())
        clean_sheet = {
            "id": sheet_id,
            "title": title,
            "category": category,
            "desc": desc,
            "description": desc,
            "tasks": norm_tasks,
            "created_at": sheet_data.get("created_at", now_date),
            "updated_at": now_date,
        }

        sheets_dir = DATA_DIR / "sheets"
        sheets_dir.mkdir(parents=True, exist_ok=True)

        sheet_file = sheets_dir / f"sheet_{sheet_id}.json"
        atomic_write_json(sheet_file, clean_sheet)

        # Update sheets-index.json
        index_path = sheets_dir / "sheets-index.json"
        current_index = []
        if index_path.exists():
            try:
                current_index = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                current_index = []

        summary_entry = {
            "id": sheet_id,
            "title": title,
            "category": category,
            "desc": desc,
            "description": desc,
            "tasks_count": len(norm_tasks),
            "updated_at": now_date,
        }
        replaced = False
        for idx, item in enumerate(current_index):
            if str(item.get("id")) == sheet_id:
                if "created_at" in item:
                    summary_entry["created_at"] = item["created_at"]
                current_index[idx] = summary_entry
                replaced = True
                break
        if not replaced:
            summary_entry["created_at"] = clean_sheet["created_at"]
            current_index.append(summary_entry)

        atomic_write_json(index_path, current_index)
        sync_sheets_dual_sync()
        return self.send_json(200, {
            "status": "ok",
            "message": "Sheet saved",
            "id": sheet_id,
            "tasks_count": len(norm_tasks)
        })

    def handle_delete_sheet(self, body: dict):
        """POST /api/delete-sheet: Deletes a sheet file and removes from sheets-index.json."""
        sheet_id = str(body.get("id", "")).strip()
        if not sheet_id or ".." in sheet_id or "/" in sheet_id or "\\" in sheet_id:
            return self.send_json(400, {"status": "error", "message": "Invalid or missing 'id'"})

        sheets_dir = DATA_DIR / "sheets"
        sheet_file = sheets_dir / f"sheet_{sheet_id}.json"
        if sheet_file.exists():
            try:
                sheet_file.unlink()
            except Exception as err:
                sys.stderr.write(f"[Delete Sheet Error] {err}\n")

        index_path = sheets_dir / "sheets-index.json"
        if index_path.exists():
            try:
                current_index = json.loads(index_path.read_text(encoding="utf-8"))
                current_index = [s for s in current_index if str(s.get("id")) != sheet_id]
                atomic_write_json(index_path, current_index)
            except Exception as err:
                return self.send_json(500, {"status": "error", "message": str(err)})

        sync_sheets_dual_sync()
        return self.send_json(200, {"status": "ok", "message": "Sheet deleted", "id": sheet_id})

    def handle_save_sheet_task(self, body: dict):
        """POST /api/save-sheet-task: Adds or updates a single task in a sheet."""
        sheet_id = str(body.get("sheet_id", "")).strip()
        if not sheet_id or ".." in sheet_id or "/" in sheet_id or "\\" in sheet_id:
            return self.send_json(400, {"status": "error", "message": "Invalid or missing 'sheet_id'"})

        task_data = body.get("task")
        if not isinstance(task_data, dict):
            return self.send_json(400, {"status": "error", "message": "Field 'task' must be an object"})

        # Validate difficulty
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

        sheets_dir = DATA_DIR / "sheets"
        sheet_file = sheets_dir / f"sheet_{sheet_id}.json"
        if not sheet_file.exists():
            return self.send_json(404, {"status": "not_found", "message": f"Sheet {sheet_id} not found"})

        try:
            sheet = json.loads(sheet_file.read_text(encoding="utf-8"))
            sheet.setdefault("tasks", [])

            norm_task = normalize_task(task_data, fallback_id=f"s{sheet_id}.{len(sheet['tasks'])+1}")
            norm_task["d"] = diff

            replaced = False
            for idx, p in enumerate(sheet["tasks"]):
                if str(p.get("id")) == str(norm_task["id"]):
                    sheet["tasks"][idx] = norm_task
                    replaced = True
                    break
            if not replaced:
                sheet["tasks"].append(norm_task)

            sheet["updated_at"] = time.strftime("%Y-%m-%d", time.localtime())
            atomic_write_json(sheet_file, sheet)

            # Update index tasks_count
            index_path = sheets_dir / "sheets-index.json"
            if index_path.exists():
                try:
                    idx_data = json.loads(index_path.read_text(encoding="utf-8"))
                    for s in idx_data:
                        if str(s.get("id")) == sheet_id:
                            s["tasks_count"] = len(sheet["tasks"])
                            s["updated_at"] = sheet["updated_at"]
                            break
                    atomic_write_json(index_path, idx_data)
                except Exception:
                    pass

            sync_sheets_dual_sync()
            return self.send_json(200, {
                "status": "ok",
                "message": "Task saved",
                "sheet_id": sheet_id,
                "task_id": norm_task["id"],
                "tasks_count": len(sheet["tasks"])
            })
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    def handle_delete_sheet_task(self, body: dict):
        """POST /api/delete-sheet-task: Deletes a task from a sheet."""
        sheet_id = str(body.get("sheet_id", "")).strip()
        task_id = str(body.get("task_id", "")).strip()
        if not sheet_id or not task_id:
            return self.send_json(400, {"status": "error", "message": "Fields 'sheet_id' and 'task_id' cannot be empty"})

        sheets_dir = DATA_DIR / "sheets"
        sheet_file = sheets_dir / f"sheet_{sheet_id}.json"
        if not sheet_file.exists():
            return self.send_json(404, {"status": "not_found", "message": f"Sheet {sheet_id} not found"})

        try:
            sheet = json.loads(sheet_file.read_text(encoding="utf-8"))
            sheet.setdefault("tasks", [])

            orig_len = len(sheet["tasks"])
            sheet["tasks"] = [t for t in sheet["tasks"] if str(t.get("id")) != task_id]
            if len(sheet["tasks"]) == orig_len:
                return self.send_json(200, {"status": "not_found", "message": f"Task {task_id} not found in sheet {sheet_id}"})

            sheet["updated_at"] = time.strftime("%Y-%m-%d", time.localtime())
            atomic_write_json(sheet_file, sheet)

            index_path = sheets_dir / "sheets-index.json"
            if index_path.exists():
                try:
                    idx_data = json.loads(index_path.read_text(encoding="utf-8"))
                    for s in idx_data:
                        if str(s.get("id")) == sheet_id:
                            s["tasks_count"] = len(sheet["tasks"])
                            s["updated_at"] = sheet["updated_at"]
                            break
                    atomic_write_json(index_path, idx_data)
                except Exception:
                    pass

            sync_sheets_dual_sync()
            return self.send_json(200, {
                "status": "ok",
                "message": "Task deleted",
                "sheet_id": sheet_id,
                "task_id": task_id,
                "tasks_count": len(sheet["tasks"])
            })
        except Exception as err:
            return self.send_json(500, {"status": "error", "message": str(err)})

    # =========================================================================
    # Notes Endpoints
    # =========================================================================

    def handle_save_note(self, body: dict):
        """POST /api/save-note: Creates or updates a note and updates notes-index.json."""
        note_data = body.get("note")
        if not isinstance(note_data, dict):
            return self.send_json(400, {"status": "error", "message": "Field 'note' must be an object"})

        note_id = str(note_data.get("id", "")).strip()
        if not note_id or ".." in note_id or "/" in note_id or "\\" in note_id:
            return self.send_json(400, {"status": "error", "message": "Invalid or missing note 'id'"})

        domain = note_data.get("domain")
        if not validate_domain(domain):
            return self.send_json(400, {"status": "error", "message": f"Invalid domain: {domain}"})

        note_type = str(note_data.get("type", "latex")).lower()
        if note_type not in ["latex", "pdf"]:
            return self.send_json(400, {"status": "error", "message": "Field 'type' must be 'latex' or 'pdf'"})

        title = str(note_data.get("title", "")).strip()
        if not title:
            return self.send_json(400, {"status": "error", "message": "Note 'title' cannot be empty"})

        desc = str(note_data.get("desc") or note_data.get("description", ""))
        date_str = str(note_data.get("date", time.strftime("%Y-%m-%d", time.localtime())))
        now_date = time.strftime("%Y-%m-%d", time.localtime())

        notes_dir = DATA_DIR / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)

        if note_type == "latex":
            content = str(note_data.get("content", ""))
            full_note = {
                "id": note_id,
                "domain": domain,
                "type": "latex",
                "title": title,
                "desc": desc,
                "description": desc,
                "content": content,
                "date": date_str,
                "created_at": note_data.get("created_at", now_date),
                "updated_at": now_date,
            }
            note_file = notes_dir / f"note_{note_id}.json"
            atomic_write_json(note_file, full_note)

        index_path = notes_dir / "notes-index.json"
        current_index = []
        if index_path.exists():
            try:
                current_index = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                current_index = []

        summary_entry = {
            "id": note_id,
            "domain": domain,
            "type": note_type,
            "title": title,
            "desc": desc,
            "description": desc,
            "date": date_str,
            "updated_at": now_date,
        }
        if note_type == "pdf":
            file_path = str(note_data.get("file_path", ""))
            filename = str(note_data.get("filename") or note_data.get("file") or (Path(file_path).name if file_path else ""))
            summary_entry["file"] = filename
            summary_entry["filename"] = filename
            summary_entry["file_path"] = file_path or f"data/scans/{filename}"

        replaced = False
        for idx, item in enumerate(current_index):
            if str(item.get("id")) == note_id:
                if "created_at" in item:
                    summary_entry["created_at"] = item["created_at"]
                current_index[idx] = summary_entry
                replaced = True
                break
        if not replaced:
            summary_entry["created_at"] = note_data.get("created_at", now_date)
            current_index.append(summary_entry)

        atomic_write_json(index_path, current_index)
        sync_notes_dual_sync()
        return self.send_json(200, {
            "status": "ok",
            "message": "Note saved",
            "id": note_id,
            "type": note_type,
            "domain": domain
        })

    def handle_delete_note(self, body: dict):
        """POST /api/delete-note: Deletes a note and its file, updating notes-index.json."""
        note_id = str(body.get("id", "")).strip()
        if not note_id or ".." in note_id or "/" in note_id or "\\" in note_id:
            return self.send_json(400, {"status": "error", "message": "Invalid or missing 'id'"})

        notes_dir = DATA_DIR / "notes"
        note_file = notes_dir / f"note_{note_id}.json"
        if note_file.exists():
            try:
                note_file.unlink()
            except Exception as err:
                sys.stderr.write(f"[Delete Note Error] {err}\n")

        index_path = notes_dir / "notes-index.json"
        if index_path.exists():
            try:
                current_index = json.loads(index_path.read_text(encoding="utf-8"))
                current_index = [n for n in current_index if str(n.get("id")) != note_id]
                atomic_write_json(index_path, current_index)
            except Exception as err:
                return self.send_json(500, {"status": "error", "message": str(err)})

        sync_notes_dual_sync()
        return self.send_json(200, {"status": "ok", "message": "Note deleted", "id": note_id})


def create_server(bind_host: str = "0.0.0.0", port: int = 8000) -> ThreadingHTTPServer:
    """Create configured ThreadingHTTPServer instance."""
    ensure_domain_data_initialized()
    ensure_sheets_and_notes_initialized()
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
    ensure_sheets_and_notes_initialized()

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
