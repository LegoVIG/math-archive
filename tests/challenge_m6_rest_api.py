"""Empirical Adversarial Stress Test Suite: Milestone 6 REST API Endpoints & WRITE_LOCK.

Conducted by Challenger 1 (Replacement) for Milestone 6:
1. Valid and invalid payloads for all 7 REST endpoints:
   - POST /api/save-sheet-index
   - POST /api/save-sheet
   - POST /api/delete-sheet
   - POST /api/save-sheet-task
   - POST /api/delete-sheet-task
   - POST /api/save-note
   - POST /api/delete-note
2. Boundary conditions and input validation:
   - Difficulty validation: 0, 9, negative, float, non-int strings, booleans (True/False), bounds (1, 8)
   - Missing mandatory fields: empty payloads, missing IDs, whitespace titles, invalid types
   - Non-existent sheet and note IDs (404 vs idempotent 200)
   - Path traversal in sheet/note/task identifiers
3. Dual-Sync integrity:
   - LaTeX backslash preservation (\\frac, \\sum, \\sqrt, etc.)
   - HTML script tag breakout prevention (</script>, </SCRIPT>)
4. Concurrency & WRITE_LOCK stress testing:
   - Multi-threaded concurrent mutations ensuring zero corruption, zero deadlocks, and valid JSON.
"""

import concurrent.futures
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# Add tests directory to sys.path
TESTS_DIR = Path(__file__).resolve().parent
MATH_ARCHIVE_DIR = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_helpers import (
    ServerInstance,
    extract_embedded_json,
    find_emoji_violations,
)


class IsolatedSandboxContext:
    """Isolated sandbox providing an ephemeral ServerInstance for mutating tests."""

    def __init__(self, port: int = None):
        self.temp_dir = tempfile.mkdtemp(prefix="adv_m6_sandbox_")
        self.path = Path(self.temp_dir)
        self.math_archive_path = self.path / "math-archive"
        self.port = port
        self.server = None

    def __enter__(self) -> ServerInstance:
        if MATH_ARCHIVE_DIR.exists():
            shutil.copytree(
                MATH_ARCHIVE_DIR,
                self.math_archive_path,
                ignore=shutil.ignore_patterns("tests", "*.tmp", "__pycache__"),
            )
        else:
            self.math_archive_path.mkdir(parents=True, exist_ok=True)

        self.server = ServerInstance(workdir=self.math_archive_path, port=self.port)
        if not self.server.start():
            self.__exit__(None, None, None)
            raise RuntimeError(f"Failed to start ephemeral server on port {self.server.port}")
        return self.server

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.server:
            try:
                self.server.stop()
            except Exception:
                pass
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass


def post_json(base_url: str, endpoint: str, payload: dict) -> tuple[int, dict]:
    """Execute POST request with JSON payload, returning HTTP status code and parsed body."""
    url = urllib.parse.urljoin(base_url, endpoint)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = resp.read().decode("utf-8")
            return resp.status, json.loads(data)
    except urllib.error.HTTPError as err:
        data = err.read().decode("utf-8")
        try:
            parsed = json.loads(data)
        except Exception:
            parsed = {"raw": data}
        return err.code, parsed


def post_raw(base_url: str, endpoint: str, raw_body: bytes, content_type: str = "application/json") -> tuple[int, dict]:
    """Execute POST request with raw byte body, returning HTTP status code and parsed body."""
    url = urllib.parse.urljoin(base_url, endpoint)
    req = urllib.request.Request(
        url,
        data=raw_body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = resp.read().decode("utf-8")
            return resp.status, json.loads(data)
    except urllib.error.HTTPError as err:
        data = err.read().decode("utf-8")
        try:
            parsed = json.loads(data)
        except Exception:
            parsed = {"raw": data}
        return err.code, parsed


# =============================================================================
# 1. Sheet Index Adversarial Tests
# =============================================================================

class TestSheetIndexAdversarial(unittest.TestCase):
    """Stress tests for POST /api/save-sheet-index."""

    def setUp(self):
        self.sandbox = IsolatedSandboxContext()
        self.server = self.sandbox.__enter__()
        self.base_url = self.server.base_url
        self.workdir = self.sandbox.math_archive_path

    def tearDown(self):
        self.sandbox.__exit__(None, None, None)

    def test_save_sheet_index_valid_list(self):
        """Valid index array replaces sheets-index.json and triggers Dual-Sync."""
        new_index = [
            {"id": "adv_s1", "title": "Index Test 1", "category": "кружок", "desc": "Desc 1"},
            {"id": "adv_s2", "title": "Index Test 2", "category": "олимпиада", "desc": "Desc 2"},
        ]
        status, resp = post_json(self.base_url, "/api/save-sheet-index", {"index": new_index})
        self.assertEqual(status, 200)
        self.assertEqual(resp.get("status"), "ok")

        # Verify on disk
        idx_file = self.workdir / "data" / "sheets" / "sheets-index.json"
        saved = json.loads(idx_file.read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["id"], "adv_s1")

        # Verify Dual-Sync in HTML
        html = (self.workdir / "index.html").read_text(encoding="utf-8")
        embedded = extract_embedded_json(html, "sheets-data")
        self.assertIsNotNone(embedded)
        self.assertEqual(len(embedded.get("index", [])), 2)

    def test_save_sheet_index_valid_single_sheet_merge(self):
        """Valid 'sheet' key updates or appends metadata in sheets-index.json."""
        status, resp = post_json(self.base_url, "/api/save-sheet-index", {
            "sheet": {"id": "sheet_1", "title": "Updated Title via Sheet Index", "category": "авторская"}
        })
        self.assertEqual(status, 200)
        idx_file = self.workdir / "data" / "sheets" / "sheets-index.json"
        saved = json.loads(idx_file.read_text(encoding="utf-8"))
        matched = [s for s in saved if str(s.get("id")) == "sheet_1"]
        self.assertTrue(len(matched) > 0)
        self.assertEqual(matched[0]["title"], "Updated Title via Sheet Index")

    def test_save_sheet_index_invalid_payloads(self):
        """Malformed or incomplete index payloads rejected with HTTP 400."""
        # Empty body
        status, resp = post_json(self.base_url, "/api/save-sheet-index", {})
        self.assertEqual(status, 400)

        # Index is not list
        status, resp = post_json(self.base_url, "/api/save-sheet-index", {"index": "not-a-list"})
        self.assertEqual(status, 400)

        # Index contains non-dict item
        status, resp = post_json(self.base_url, "/api/save-sheet-index", {"index": ["string_item"]})
        self.assertEqual(status, 400)

        # Index item missing id
        status, resp = post_json(self.base_url, "/api/save-sheet-index", {"index": [{"title": "No ID"}]})
        self.assertEqual(status, 400)

        # Index item missing title
        status, resp = post_json(self.base_url, "/api/save-sheet-index", {"index": [{"id": "s1", "title": "  "}]})
        self.assertEqual(status, 400)

        # Index item path traversal id
        status, resp = post_json(self.base_url, "/api/save-sheet-index", {"index": [{"id": "../evil", "title": "Evil"}]})
        self.assertEqual(status, 400)

        # Sheet is not dict
        status, resp = post_json(self.base_url, "/api/save-sheet-index", {"sheet": "not-a-dict"})
        self.assertEqual(status, 400)

        # Sheet missing id or invalid
        status, resp = post_json(self.base_url, "/api/save-sheet-index", {"sheet": {"id": ""}})
        self.assertEqual(status, 400)


# =============================================================================
# 2. Sheet CRUD & Boundaries
# =============================================================================

class TestSheetCRUDAdversarial(unittest.TestCase):
    """Stress tests for POST /api/save-sheet and POST /api/delete-sheet."""

    def setUp(self):
        self.sandbox = IsolatedSandboxContext()
        self.server = self.sandbox.__enter__()
        self.base_url = self.server.base_url
        self.workdir = self.sandbox.math_archive_path

    def tearDown(self):
        self.sandbox.__exit__(None, None, None)

    def test_save_sheet_valid_and_category_normalization(self):
        """Valid sheet creation with standard categories and fallback for unknown category."""
        # Standard category
        sheet_data = {
            "id": "sheet_adv_cat",
            "title": "Adversarial Category Test",
            "category": "матбой",
            "desc": "Testing matboy",
            "tasks": [
                {"id": "t1", "st": "Statement 1", "d": 4}
            ]
        }
        status, resp = post_json(self.base_url, "/api/save-sheet", {"sheet": sheet_data})
        self.assertEqual(status, 200)
        self.assertEqual(resp.get("id"), "sheet_adv_cat")
        self.assertEqual(resp.get("tasks_count"), 1)

        # Verify disk file
        sheet_file = self.workdir / "data" / "sheets" / "sheet_sheet_adv_cat.json"
        self.assertTrue(sheet_file.exists())
        saved = json.loads(sheet_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["category"], "матбой")

        # Unknown category falls back cleanly to 'кружок'
        sheet_data_unknown_cat = {
            "id": "sheet_unknown_cat",
            "title": "Unknown Category",
            "category": "some_random_category",
            "tasks": []
        }
        status, resp = post_json(self.base_url, "/api/save-sheet", {"sheet": sheet_data_unknown_cat})
        self.assertEqual(status, 200)
        sheet_file2 = self.workdir / "data" / "sheets" / "sheet_sheet_unknown_cat.json"
        saved2 = json.loads(sheet_file2.read_text(encoding="utf-8"))
        self.assertEqual(saved2["category"], "кружок")

    def test_save_sheet_difficulty_clamping_in_batch(self):
        """In batch save-sheet, invalid task difficulties clamp between 1 and 8 without crash."""
        sheet_data = {
            "id": "sheet_diff_clamp",
            "title": "Clamping Test",
            "category": "олимпиада",
            "tasks": [
                {"id": "t_low", "st": "Low diff", "d": -10},
                {"id": "t_high", "st": "High diff", "d": 99},
                {"id": "t_invalid", "st": "Invalid str diff", "d": "extreme"},
                {"id": "t_valid", "st": "Valid diff", "d": 5},
            ]
        }
        status, resp = post_json(self.base_url, "/api/save-sheet", {"sheet": sheet_data})
        self.assertEqual(status, 200)

        sheet_file = self.workdir / "data" / "sheets" / "sheet_sheet_diff_clamp.json"
        saved = json.loads(sheet_file.read_text(encoding="utf-8"))
        tasks = {t["id"]: t["d"] for t in saved["tasks"]}
        self.assertEqual(tasks["t_low"], 1)
        self.assertEqual(tasks["t_high"], 8)
        self.assertEqual(tasks["t_invalid"], 1)
        self.assertEqual(tasks["t_valid"], 5)

    def test_save_sheet_invalid_payloads_and_traversal(self):
        """Missing mandatory fields, empty titles, and path traversal in save-sheet return 400."""
        # Missing sheet
        status, resp = post_json(self.base_url, "/api/save-sheet", {})
        self.assertEqual(status, 400)

        # Missing id
        status, resp = post_json(self.base_url, "/api/save-sheet", {"sheet": {"title": "No ID"}})
        self.assertEqual(status, 400)

        # Empty title or whitespace title
        status, resp = post_json(self.base_url, "/api/save-sheet", {"sheet": {"id": "s_empty_t", "title": ""}})
        self.assertEqual(status, 400)
        status, resp = post_json(self.base_url, "/api/save-sheet", {"sheet": {"id": "s_ws_t", "title": "   \n\t "}})
        self.assertEqual(status, 400)

        # Path traversal in sheet id
        for evil_id in ["../evil_sheet", "..\\evil_sheet", "foo/bar", "foo\\bar"]:
            status, resp = post_json(self.base_url, "/api/save-sheet", {"sheet": {"id": evil_id, "title": "Evil"}})
            self.assertEqual(status, 400)

    def test_delete_sheet_lifecycle_and_idempotency(self):
        """Deleting a sheet removes file, index entry, updates Dual-Sync, and is idempotent."""
        sheet_id = "sheet_to_del"
        post_json(self.base_url, "/api/save-sheet", {
            "sheet": {"id": sheet_id, "title": "To Delete", "category": "кружок", "tasks": []}
        })
        sheet_file = self.workdir / "data" / "sheets" / f"sheet_{sheet_id}.json"
        self.assertTrue(sheet_file.exists())

        # Delete existing sheet
        status, resp = post_json(self.base_url, "/api/delete-sheet", {"id": sheet_id})
        self.assertEqual(status, 200)
        self.assertEqual(resp.get("status"), "ok")
        self.assertFalse(sheet_file.exists())

        # Verify sheets-index.json
        idx_file = self.workdir / "data" / "sheets" / "sheets-index.json"
        saved = json.loads(idx_file.read_text(encoding="utf-8"))
        self.assertFalse(any(str(s.get("id")) == sheet_id for s in saved))

        # Idempotent delete on non-existent sheet
        status, resp = post_json(self.base_url, "/api/delete-sheet", {"id": "ghost_sheet_9999"})
        self.assertEqual(status, 200)

        # Path traversal in delete-sheet rejected
        for evil_id in ["../traversal", "..\\traversal", "/etc/passwd"]:
            status, resp = post_json(self.base_url, "/api/delete-sheet", {"id": evil_id})
            self.assertEqual(status, 400)


# =============================================================================
# 3. Sheet Task CRUD & Strict Difficulty Boundaries
# =============================================================================

class TestSheetTaskDifficultyAndCRUD(unittest.TestCase):
    """Stress tests for POST /api/save-sheet-task and POST /api/delete-sheet-task."""

    def setUp(self):
        self.sandbox = IsolatedSandboxContext()
        self.server = self.sandbox.__enter__()
        self.base_url = self.server.base_url
        self.workdir = self.sandbox.math_archive_path

        # Create base test sheet
        self.test_sheet_id = "sheet_task_test"
        post_json(self.base_url, "/api/save-sheet", {
            "sheet": {
                "id": self.test_sheet_id,
                "title": "Task Test Sheet",
                "category": "олимпиада",
                "tasks": []
            }
        })

    def tearDown(self):
        self.sandbox.__exit__(None, None, None)

    def test_save_sheet_task_valid_difficulty_boundaries(self):
        """Difficulty d strictly accepts 1 (min), 8 (max), 4 (mid), and integer-valued float (3.0)."""
        valid_diffs = [1, 8, 4, 3.0]
        for idx, d in enumerate(valid_diffs):
            payload = {
                "sheet_id": self.test_sheet_id,
                "task": {
                    "id": f"t_valid_{idx}",
                    "st": f"Statement for diff {d}",
                    "d": d
                }
            }
            status, resp = post_json(self.base_url, "/api/save-sheet-task", payload)
            self.assertEqual(status, 200, f"Expected 200 for difficulty {d}, got {status}: {resp}")
            self.assertEqual(resp.get("status"), "ok")

        # Verify tasks saved with correct int difficulty
        sheet_file = self.workdir / "data" / "sheets" / f"sheet_{self.test_sheet_id}.json"
        sheet = json.loads(sheet_file.read_text(encoding="utf-8"))
        self.assertEqual(len(sheet["tasks"]), len(valid_diffs))
        for t in sheet["tasks"]:
            self.assertIsInstance(t["d"], int)
            self.assertTrue(1 <= t["d"] <= 8)

    def test_save_sheet_task_invalid_difficulty_rejections(self):
        """Difficulty d strictly rejects 0, 9, negative, float, non-int string, boolean, list, None."""
        invalid_diffs = [
            0,          # Below min boundary 1
            9,          # Above max boundary 8
            100,        # Far above
            -1,         # Negative
            -50,        # Far negative
            3.5,        # Non-integer float
            7.99,       # Non-integer float
            "hard",     # Non-int string
            "abc",      # Non-int string
            "",         # Empty string
            "3.14",     # Float string
            True,       # Boolean True (subclass of int in Python)
            False,      # Boolean False
            None,       # NoneType
            [3],        # List
            {"val": 4}, # Dict
        ]
        for d in invalid_diffs:
            payload = {
                "sheet_id": self.test_sheet_id,
                "task": {
                    "id": "t_invalid_diff",
                    "st": "Should be rejected",
                    "d": d
                }
            }
            status, resp = post_json(self.base_url, "/api/save-sheet-task", payload)
            self.assertEqual(status, 400, f"Expected 400 rejection for difficulty {d!r}, got {status}: {resp}")

    def test_save_sheet_task_string_int_coercion(self):
        """Stringified integer difficulty '5' is gracefully coerced to integer 5 on disk."""
        payload = {
            "sheet_id": self.test_sheet_id,
            "task": {
                "id": "t_coerced_diff",
                "st": "Coerced diff task",
                "d": "5"
            }
        }
        status, resp = post_json(self.base_url, "/api/save-sheet-task", payload)
        self.assertEqual(status, 200)

        sheet_file = self.workdir / "data" / "sheets" / f"sheet_{self.test_sheet_id}.json"
        sheet = json.loads(sheet_file.read_text(encoding="utf-8"))
        matched = [t for t in sheet["tasks"] if t["id"] == "t_coerced_diff"]
        self.assertEqual(len(matched), 1)
        self.assertIsInstance(matched[0]["d"], int)
        self.assertEqual(matched[0]["d"], 5)

    def test_save_sheet_task_nonexistent_sheet_returns_404(self):
        """Saving task into non-existent sheet returns HTTP 404."""
        payload = {
            "sheet_id": "ghost_sheet_not_found",
            "task": {"id": "t1", "st": "Statement", "d": 3}
        }
        status, resp = post_json(self.base_url, "/api/save-sheet-task", payload)
        self.assertEqual(status, 404)
        self.assertEqual(resp.get("status"), "not_found")

    def test_save_sheet_task_missing_fields_and_traversal(self):
        """Missing sheet_id, missing task, or path traversal returns 400."""
        # Empty
        status, resp = post_json(self.base_url, "/api/save-sheet-task", {})
        self.assertEqual(status, 400)

        # Missing task
        status, resp = post_json(self.base_url, "/api/save-sheet-task", {"sheet_id": self.test_sheet_id})
        self.assertEqual(status, 400)

        # Task not object
        status, resp = post_json(self.base_url, "/api/save-sheet-task", {"sheet_id": self.test_sheet_id, "task": "invalid"})
        self.assertEqual(status, 400)

        # Traversal in sheet_id
        status, resp = post_json(self.base_url, "/api/save-sheet-task", {"sheet_id": "../evil", "task": {"d": 3}})
        self.assertEqual(status, 400)

    def test_save_sheet_task_update_in_place(self):
        """Saving task with existing ID updates it in place without duplicating."""
        task_id = "task_to_update"
        post_json(self.base_url, "/api/save-sheet-task", {
            "sheet_id": self.test_sheet_id,
            "task": {"id": task_id, "st": "Initial statement", "d": 2}
        })
        # Update same task
        status, resp = post_json(self.base_url, "/api/save-sheet-task", {
            "sheet_id": self.test_sheet_id,
            "task": {"id": task_id, "st": "Updated statement", "d": 6}
        })
        self.assertEqual(status, 200)

        sheet_file = self.workdir / "data" / "sheets" / f"sheet_{self.test_sheet_id}.json"
        sheet = json.loads(sheet_file.read_text(encoding="utf-8"))
        self.assertEqual(len(sheet["tasks"]), 1)
        self.assertEqual(sheet["tasks"][0]["st"], "Updated statement")
        self.assertEqual(sheet["tasks"][0]["d"], 6)

    def test_delete_sheet_task_lifecycle(self):
        """Deleting task removes it, decrements index tasks_count, handles 404 and not_found."""
        task_id = "task_del_me"
        post_json(self.base_url, "/api/save-sheet-task", {
            "sheet_id": self.test_sheet_id,
            "task": {"id": task_id, "st": "Delete me", "d": 3}
        })
        # Delete existing task
        status, resp = post_json(self.base_url, "/api/delete-sheet-task", {
            "sheet_id": self.test_sheet_id,
            "task_id": task_id
        })
        self.assertEqual(status, 200)
        self.assertEqual(resp.get("status"), "ok")
        self.assertEqual(resp.get("tasks_count"), 0)

        # Delete non-existent task from existing sheet -> 200 with status=not_found
        status, resp = post_json(self.base_url, "/api/delete-sheet-task", {
            "sheet_id": self.test_sheet_id,
            "task_id": "ghost_task_id"
        })
        self.assertEqual(status, 200)
        self.assertEqual(resp.get("status"), "not_found")

        # Delete from non-existent sheet -> 404
        status, resp = post_json(self.base_url, "/api/delete-sheet-task", {
            "sheet_id": "ghost_sheet_not_found",
            "task_id": task_id
        })
        self.assertEqual(status, 404)

        # Missing sheet_id or task_id -> 400
        status, resp = post_json(self.base_url, "/api/delete-sheet-task", {"sheet_id": self.test_sheet_id})
        self.assertEqual(status, 400)
        status, resp = post_json(self.base_url, "/api/delete-sheet-task", {"task_id": task_id})
        self.assertEqual(status, 400)


# =============================================================================
# 4. Notes CRUD & Domain Boundaries
# =============================================================================

class TestNotesCRUDAdversarial(unittest.TestCase):
    """Stress tests for POST /api/save-note and POST /api/delete-note."""

    def setUp(self):
        self.sandbox = IsolatedSandboxContext()
        self.server = self.sandbox.__enter__()
        self.base_url = self.server.base_url
        self.workdir = self.sandbox.math_archive_path

    def tearDown(self):
        self.sandbox.__exit__(None, None, None)

    def test_save_note_all_valid_domains(self):
        """All 5 domains (combinatorics, algebra, number-theory, geometry, analysis) accepted."""
        domains = ["combinatorics", "algebra", "number-theory", "geometry", "analysis"]
        for d in domains:
            note_id = f"note_dom_{d}"
            payload = {
                "note": {
                    "id": note_id,
                    "domain": d,
                    "type": "latex",
                    "title": f"Note for {d}",
                    "content": f"# Header for {d}\nMath content here"
                }
            }
            status, resp = post_json(self.base_url, "/api/save-note", payload)
            self.assertEqual(status, 200, f"Expected 200 for domain {d}, got {status}: {resp}")
            note_file = self.workdir / "data" / "notes" / f"note_{note_id}.json"
            self.assertTrue(note_file.exists())

    def test_save_note_pdf_type_structure(self):
        """Note of type 'pdf' updates notes-index.json without creating note_{id}.json."""
        note_id = "note_scan_pdf_test"
        payload = {
            "note": {
                "id": note_id,
                "domain": "geometry",
                "type": "pdf",
                "title": "Ceva Theorem PDF Scan",
                "file": "ceva_menelaus.pdf",
                "file_path": "/data/scans/ceva_menelaus.pdf"
            }
        }
        status, resp = post_json(self.base_url, "/api/save-note", payload)
        self.assertEqual(status, 200)

        # PDF note does not write note_{id}.json
        note_file = self.workdir / "data" / "notes" / f"note_{note_id}.json"
        self.assertFalse(note_file.exists())

        # Index entry must contain file info
        idx_file = self.workdir / "data" / "notes" / "notes-index.json"
        saved = json.loads(idx_file.read_text(encoding="utf-8"))
        matched = [n for n in saved if str(n.get("id")) == note_id]
        self.assertTrue(len(matched) > 0)
        self.assertEqual(matched[0]["file"], "ceva_menelaus.pdf")
        self.assertEqual(matched[0]["type"], "pdf")

    def test_save_note_invalid_inputs_and_traversal(self):
        """Invalid domain, invalid type, empty title, and path traversal return 400."""
        # Invalid domain
        status, resp = post_json(self.base_url, "/api/save-note", {
            "note": {"id": "n1", "domain": "astrology", "type": "latex", "title": "Test"}
        })
        self.assertEqual(status, 400)

        # Domain path traversal
        status, resp = post_json(self.base_url, "/api/save-note", {
            "note": {"id": "n1", "domain": "../geometry", "type": "latex", "title": "Test"}
        })
        self.assertEqual(status, 400)

        # Invalid type
        status, resp = post_json(self.base_url, "/api/save-note", {
            "note": {"id": "n1", "domain": "geometry", "type": "word_docx", "title": "Test"}
        })
        self.assertEqual(status, 400)

        # Empty or whitespace title
        status, resp = post_json(self.base_url, "/api/save-note", {
            "note": {"id": "n1", "domain": "algebra", "type": "latex", "title": "  "}
        })
        self.assertEqual(status, 400)

        # Missing id
        status, resp = post_json(self.base_url, "/api/save-note", {
            "note": {"domain": "algebra", "type": "latex", "title": "Title"}
        })
        self.assertEqual(status, 400)

        # Traversal in note id
        for evil_id in ["../evil_note", "..\\evil_note", "sub/note"]:
            status, resp = post_json(self.base_url, "/api/save-note", {
                "note": {"id": evil_id, "domain": "analysis", "type": "latex", "title": "Evil"}
            })
            self.assertEqual(status, 400)

    def test_delete_note_lifecycle(self):
        """Deleting LaTeX and PDF notes cleans disk, updates index, and is idempotent."""
        # Create LaTeX note
        post_json(self.base_url, "/api/save-note", {
            "note": {"id": "note_to_del", "domain": "combinatorics", "type": "latex", "title": "Del Me"}
        })
        note_file = self.workdir / "data" / "notes" / "note_note_to_del.json"
        self.assertTrue(note_file.exists())

        # Delete it
        status, resp = post_json(self.base_url, "/api/delete-note", {"id": "note_to_del"})
        self.assertEqual(status, 200)
        self.assertFalse(note_file.exists())

        # Non-existent delete is idempotent
        status, resp = post_json(self.base_url, "/api/delete-note", {"id": "ghost_note_12345"})
        self.assertEqual(status, 200)

        # Traversal in delete-note rejected
        status, resp = post_json(self.base_url, "/api/delete-note", {"id": "../evil"})
        self.assertEqual(status, 400)


# =============================================================================
# 5. Dual-Sync Inlining & LaTeX Fidelity
# =============================================================================

class TestDualSyncFidelityAndEscaping(unittest.TestCase):
    """Stress tests for LaTeX formula preservation and HTML5 script escaping in Dual-Sync."""

    def setUp(self):
        self.sandbox = IsolatedSandboxContext()
        self.server = self.sandbox.__enter__()
        self.base_url = self.server.base_url
        self.workdir = self.sandbox.math_archive_path

    def tearDown(self):
        self.sandbox.__exit__(None, None, None)

    def test_latex_syntax_fidelity(self):
        """LaTeX mathematical formulas survive save, disk write, and Dual-Sync without backslash loss."""
        latex_content = (
            "Formula: $\\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$\n"
            "Sum: $\\sum_{k=1}^{\\infty} \\frac{1}{k^2} = \\frac{\\pi^2}{6}$\n"
            "Matrix: $\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}$\n"
            "Symbols: $\\forall x \\in \\mathbb{R}, \\exists y > 0: x + y = 0$\n"
            "Regex backreferences: \\1 \\2 \\b \\n \\t \\r"
        )
        post_json(self.base_url, "/api/save-note", {
            "note": {
                "id": "note_latex_stress",
                "domain": "algebra",
                "type": "latex",
                "title": "LaTeX Stress Formula",
                "content": latex_content
            }
        })

        # Verify disk file
        disk_file = self.workdir / "data" / "notes" / "note_note_latex_stress.json"
        disk_data = json.loads(disk_file.read_text(encoding="utf-8"))
        self.assertEqual(disk_data["content"], latex_content)

        # Verify Dual-Sync embedded script in index.html
        html = (self.workdir / "index.html").read_text(encoding="utf-8")
        embedded = extract_embedded_json(html, "notes-data")
        self.assertIsNotNone(embedded)
        inlined_note = embedded.get("notes", {}).get("note_latex_stress")
        self.assertIsNotNone(inlined_note)
        self.assertEqual(inlined_note["content"], latex_content)

    def test_script_tag_breakout_escaping(self):
        """Closing </script> in task statement or note content is escaped to <\\/script> without XSS."""
        evil_payload = "Attack: </script><script>alert('xss')</script> and </SCRIPT> and </Script >"
        post_json(self.base_url, "/api/save-note", {
            "note": {
                "id": "note_xss_escape",
                "domain": "analysis",
                "type": "latex",
                "title": "XSS Test",
                "content": evil_payload
            }
        })

        html = (self.workdir / "index.html").read_text(encoding="utf-8")
        # Ensure raw unescaped </script> does NOT exist inside the JSON script tag
        pattern = r'<script\s+[^>]*id=[\x22\x27]notes-data[\x22\x27][^>]*>(.*?)</script>'
        match = re.search(pattern, html, flags=re.DOTALL | re.IGNORECASE)
        self.assertIsNotNone(match)
        inner_content = match.group(1)
        self.assertNotIn("</script>", inner_content.lower())
        self.assertIn(r"<\/script>", inner_content.lower())

        # But when JSON-parsed, the payload is preserved exactly
        parsed = json.loads(inner_content)
        self.assertEqual(parsed["notes"]["note_xss_escape"]["content"], evil_payload)


# =============================================================================
# 6. Concurrency and WRITE_LOCK Stress
# =============================================================================

class TestConcurrencyAndWriteLock(unittest.TestCase):
    """Stress tests validating thread-safety and data consistency under concurrent load."""

    def setUp(self):
        self.sandbox = IsolatedSandboxContext()
        self.server = self.sandbox.__enter__()
        self.base_url = self.server.base_url
        self.workdir = self.sandbox.math_archive_path

        # Setup base sheets for concurrency targets
        for i in range(1, 4):
            post_json(self.base_url, "/api/save-sheet", {
                "sheet": {"id": f"conc_sheet_{i}", "title": f"Conc Sheet {i}", "category": "кружок", "tasks": []}
            })

    def tearDown(self):
        self.sandbox.__exit__(None, None, None)

    def test_high_concurrency_mixed_operations(self):
        """20 concurrent threads perform 80 mixed CRUD operations under WRITE_LOCK with zero crashes."""
        errors = []
        status_codes = []

        def worker_task(worker_id: int):
            try:
                op = worker_id % 5
                if op == 0:
                    # Save sheet task
                    target_sheet = f"conc_sheet_{(worker_id % 3) + 1}"
                    status, resp = post_json(self.base_url, "/api/save-sheet-task", {
                        "sheet_id": target_sheet,
                        "task": {
                            "id": f"t_conc_{worker_id}",
                            "st": f"Statement from worker {worker_id}",
                            "d": (worker_id % 8) + 1
                        }
                    })
                    return status, resp
                elif op == 1:
                    # Save a new note
                    status, resp = post_json(self.base_url, "/api/save-note", {
                        "note": {
                            "id": f"conc_note_{worker_id}",
                            "domain": "algebra",
                            "type": "latex",
                            "title": f"Conc Note {worker_id}",
                            "content": f"Content from worker {worker_id}"
                        }
                    })
                    return status, resp
                elif op == 2:
                    # Save sheet
                    status, resp = post_json(self.base_url, "/api/save-sheet", {
                        "sheet": {
                            "id": f"worker_sheet_{worker_id}",
                            "title": f"Worker Sheet {worker_id}",
                            "category": "матбой",
                            "tasks": [{"id": f"st_{worker_id}", "st": "Worker task", "d": 3}]
                        }
                    })
                    return status, resp
                elif op == 3:
                    # Delete task
                    target_sheet = f"conc_sheet_{(worker_id % 3) + 1}"
                    status, resp = post_json(self.base_url, "/api/delete-sheet-task", {
                        "sheet_id": target_sheet,
                        "task_id": f"t_conc_{worker_id - 5}"
                    })
                    return status, resp
                else:
                    # Update sheet index
                    status, resp = post_json(self.base_url, "/api/save-sheet-index", {
                        "sheet": {"id": f"conc_sheet_1", "title": f"Updated by worker {worker_id}"}
                    })
                    return status, resp
            except Exception as ex:
                return 500, {"error": str(ex)}

        num_workers = 20
        num_tasks = 80
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_task, i) for i in range(num_tasks)]
            for f in concurrent.futures.as_completed(futures):
                status, resp = f.result()
                status_codes.append(status)
                if status >= 500:
                    errors.append((status, resp))

        # Check for zero internal server errors
        self.assertEqual(len(errors), 0, f"Encountered 500 errors under concurrency: {errors}")
        for code in status_codes:
            self.assertIn(code, [200, 404], f"Unexpected status code: {code}")

        # Check disk JSON files for corruption (all must parse cleanly)
        sheets_dir = self.workdir / "data" / "sheets"
        for jf in sheets_dir.glob("*.json"):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                self.assertIsInstance(data, (list, dict), f"{jf.name} did not parse to list or dict")
            except Exception as err:
                self.fail(f"Corrupted JSON in {jf.name} after concurrency: {err}")

        notes_dir = self.workdir / "data" / "notes"
        for jf in notes_dir.glob("*.json"):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                self.assertIsInstance(data, (list, dict), f"{jf.name} did not parse to list or dict")
            except Exception as err:
                self.fail(f"Corrupted JSON in {jf.name} after concurrency: {err}")

        # Check index.html embedded script tags parse cleanly
        html = (self.workdir / "index.html").read_text(encoding="utf-8")
        sheets_data = extract_embedded_json(html, "sheets-data")
        self.assertIsNotNone(sheets_data, "sheets-data corrupted in index.html")
        self.assertNotIn("__parse_error__", sheets_data)

        notes_data = extract_embedded_json(html, "notes-data")
        self.assertIsNotNone(notes_data, "notes-data corrupted in index.html")
        self.assertNotIn("__parse_error__", notes_data)


# =============================================================================
# 7. Protocol and Malformed Payload Fuzzing
# =============================================================================

class TestProtocolAndMalformedFuzzing(unittest.TestCase):
    """Stress tests for malformed JSON, empty bodies, non-dict payloads, and HTTP routing."""

    def setUp(self):
        self.sandbox = IsolatedSandboxContext()
        self.server = self.sandbox.__enter__()
        self.base_url = self.server.base_url

    def tearDown(self):
        self.sandbox.__exit__(None, None, None)

    def test_malformed_json_syntax_rejected(self):
        """Malformed JSON strings return HTTP 400."""
        endpoints = [
            "/api/save-sheet-index",
            "/api/save-sheet",
            "/api/delete-sheet",
            "/api/save-sheet-task",
            "/api/delete-sheet-task",
            "/api/save-note",
            "/api/delete-note",
        ]
        malformed_bytes = b'{"unclosed_brace": true, '
        for ep in endpoints:
            status, resp = post_raw(self.base_url, ep, malformed_bytes)
            self.assertEqual(status, 400, f"Expected 400 for malformed JSON on {ep}, got {status}")
            self.assertEqual(resp.get("status"), "error")

    def test_non_dict_json_payloads_rejected(self):
        """JSON payloads that are lists, strings, numbers, or booleans return HTTP 400."""
        endpoints = [
            "/api/save-sheet",
            "/api/delete-sheet",
            "/api/save-sheet-task",
            "/api/delete-sheet-task",
            "/api/save-note",
            "/api/delete-note",
        ]
        non_dict_payloads = [
            b'[1, 2, 3]',
            b'"just a string"',
            b'12345',
            b'true',
            b'null',
        ]
        for ep in endpoints:
            for p in non_dict_payloads:
                status, resp = post_raw(self.base_url, ep, p)
                self.assertEqual(status, 400, f"Expected 400 for non-dict on {ep}, got {status}")

    def test_empty_post_body_rejected(self):
        """Empty POST body (0 bytes) returns HTTP 400."""
        endpoints = [
            "/api/save-sheet-index",
            "/api/save-sheet",
            "/api/delete-sheet",
            "/api/save-sheet-task",
            "/api/delete-sheet-task",
            "/api/save-note",
            "/api/delete-note",
        ]
        for ep in endpoints:
            status, resp = post_raw(self.base_url, ep, b'')
            self.assertEqual(status, 400, f"Expected 400 for empty body on {ep}, got {status}")

    def test_unknown_api_endpoints_return_404(self):
        """Non-existent API endpoints return HTTP 404."""
        unknown = ["/api/nonexistent", "/api/sheets/hack", "/api/save-ghost"]
        for ep in unknown:
            status, resp = post_json(self.base_url, ep, {"test": 1})
            self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
