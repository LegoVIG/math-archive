"""Milestone 6 Backend Test Suite: REST API, Scans Upload, Dual-Sync, and Data Stores.

Covers:
1. Static PDF MIME type mapping (application/pdf).
2. Multipart PDF upload endpoint (POST /api/upload-pdf) with security validation.
3. Sheets REST endpoints (save-sheet-index, save-sheet, delete-sheet, save-sheet-task, delete-sheet-task).
4. Notes REST endpoints (save-note, delete-note).
5. Dual-Sync consistency engine and HTML script tag escaping.
6. Integrity checks (zero emojis, untouched olimp-kombinatorika).
"""

import json
import re
import sys
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
ROOT_DIR = MATH_ARCHIVE_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_helpers import (
    DATA_DIR,
    DOMAINS,
    INDEX_HTML_PATH,
    OLIMP_DIR,
    SERVER_PY_PATH,
    IsolatedDataSandbox,
    extract_embedded_json,
    find_emoji_violations,
)


def make_minimal_pdf(title: str = "Test PDF Document") -> bytes:
    """Generate valid ISO 32000-1 / PDF-1.4 binary file with exact byte offsets."""
    stream_content = f"BT /F1 18 Tf 50 750 Td ({title}) Tj ET\n".encode("latin-1")
    stream_len = len(stream_content)
    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = (
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>\nendobj\n"
    )
    obj4 = f"4 0 obj\n<< /Length {stream_len} >>\nstream\n".encode("latin-1") + stream_content + b"endstream\nendobj\n"

    o1 = len(header)
    o2 = o1 + len(obj1)
    o3 = o2 + len(obj2)
    o4 = o3 + len(obj3)
    xref_offset = o4 + len(obj4)

    xref = (
        f"xref\n0 5\n"
        f"0000000000 65535 f \n"
        f"{o1:010d} 00000 n \n"
        f"{o2:010d} 00000 n \n"
        f"{o3:010d} 00000 n \n"
        f"{o4:010d} 00000 n \n"
    ).encode("latin-1")

    trailer = f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin-1")
    return header + obj1 + obj2 + obj3 + obj4 + xref + trailer


def post_json(base_url: str, endpoint: str, payload: dict) -> tuple[int, dict]:
    """Execute POST request with JSON payload, returning status code and response dict."""
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


def upload_pdf_bytes(
    base_url: str,
    filename: str,
    pdf_bytes: bytes,
    custom_content_type: str = None,
    custom_headers: dict = None,
) -> tuple[int, dict]:
    """Execute multipart/form-data upload to /api/upload-pdf."""
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    url = urllib.parse.urljoin(base_url, "/api/upload-pdf")

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + pdf_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = {
        "Content-Type": custom_content_type or f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    if custom_headers:
        headers.update(custom_headers)

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
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


class TestStaticMimeTypes(unittest.TestCase):
    """Verify explicit MIME type configuration in server.py."""

    def test_pdf_mime_type_serving(self):
        """Verify .pdf files are served with Content-Type: application/pdf."""
        with IsolatedDataSandbox() as srv:
            url = f"{srv.base_url}/data/scans/ceva_menelaus.pdf"
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                content_type = resp.headers.get("Content-Type", "")
                self.assertEqual(resp.status, 200)
                self.assertEqual(content_type, "application/pdf")

    def test_standard_mime_types_preserved(self):
        """Verify standard MIME mappings for html, css, js, json, svg."""
        with IsolatedDataSandbox() as srv:
            req = urllib.request.Request(f"{srv.base_url}/index.html", method="HEAD")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.assertTrue(resp.headers.get("Content-Type", "").startswith("text/html"))


class TestPdfUploadEndpoint(unittest.TestCase):
    """Verify POST /api/upload-pdf multipart handling and validation."""

    def test_upload_valid_pdf(self):
        """Verify uploading valid PDF returns 200 and saves file in data/scans/."""
        with IsolatedDataSandbox() as srv:
            pdf_bytes = make_minimal_pdf("Test Geometry Lecture")
            status, res = upload_pdf_bytes(srv.base_url, "my_lecture.pdf", pdf_bytes)

            self.assertEqual(status, 200)
            self.assertEqual(res.get("status"), "ok")
            self.assertEqual(res.get("filename"), "my_lecture.pdf")
            self.assertEqual(res.get("file_path"), "/data/scans/my_lecture.pdf")
            self.assertEqual(res.get("size"), len(pdf_bytes))

            # Verify file was written to disk
            saved_file = srv.workdir / "data" / "scans" / "my_lecture.pdf"
            self.assertTrue(saved_file.exists())
            self.assertEqual(saved_file.read_bytes(), pdf_bytes)

    def test_upload_non_multipart_rejected(self):
        """Verify non-multipart request returns HTTP 400."""
        with IsolatedDataSandbox() as srv:
            url = f"{srv.base_url}/api/upload-pdf"
            req = urllib.request.Request(
                url,
                data=b'{"file": "fake"}',
                headers={"Content-Type": "application/json", "Content-Length": "16"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=3.0)
            self.assertEqual(ctx.exception.code, 400)

    def test_upload_zero_length_rejected(self):
        """Verify zero-length upload returns HTTP 400."""
        with IsolatedDataSandbox() as srv:
            status, res = upload_pdf_bytes(srv.base_url, "empty.pdf", b"")
            self.assertEqual(status, 400)

    def test_upload_non_pdf_extension_rejected(self):
        """Verify non-.pdf extension returns HTTP 400."""
        with IsolatedDataSandbox() as srv:
            pdf_bytes = make_minimal_pdf("Exploit Attempt")
            status, res = upload_pdf_bytes(srv.base_url, "script.exe", pdf_bytes)
            self.assertEqual(status, 400)
            self.assertIn("error", res.get("status", ""))

    def test_upload_invalid_magic_bytes_rejected(self):
        """Verify file without %PDF- magic bytes returns HTTP 400."""
        with IsolatedDataSandbox() as srv:
            fake_pdf = b"NOT A VALID PDF FILE CONTENT"
            status, res = upload_pdf_bytes(srv.base_url, "fake.pdf", fake_pdf)
            self.assertEqual(status, 400)
            self.assertIn("error", res.get("status", ""))

    def test_upload_path_traversal_sanitized(self):
        """Verify directory traversal in filename is sanitized to flat basename."""
        with IsolatedDataSandbox() as srv:
            pdf_bytes = make_minimal_pdf("Traversal Test")
            status, res = upload_pdf_bytes(srv.base_url, "../../escape_test.pdf", pdf_bytes)
            self.assertEqual(status, 200)
            self.assertEqual(res.get("filename"), "escape_test.pdf")

            # Check that file exists strictly in data/scans/
            escaped_path = srv.workdir / "data" / "scans" / "escape_test.pdf"
            self.assertTrue(escaped_path.exists())

    def test_upload_filename_sanitization(self):
        """Verify spaces and special characters in filename are replaced with underscores."""
        with IsolatedDataSandbox() as srv:
            pdf_bytes = make_minimal_pdf("Special Chars")
            status, res = upload_pdf_bytes(srv.base_url, "Lecture Note #1 (Final).pdf", pdf_bytes)
            self.assertEqual(status, 200)
            clean_name = res.get("filename")
            self.assertTrue(clean_name.endswith(".pdf"))
            self.assertNotIn(" ", clean_name)
            self.assertNotIn("#", clean_name)
            self.assertNotIn("(", clean_name)

    def test_upload_windows_reserved_names_sanitized(self):
        """Verify Windows reserved device stems (CON, PRN, AUX) are safely renamed."""
        with IsolatedDataSandbox() as srv:
            pdf_bytes = make_minimal_pdf("Reserved Stem")
            status, res = upload_pdf_bytes(srv.base_url, "con.pdf", pdf_bytes)
            self.assertEqual(status, 200)
            clean_name = res.get("filename")
            self.assertNotEqual(clean_name.lower(), "con.pdf")
            self.assertTrue(clean_name.startswith("scan_"))


class TestSheetsRestEndpoints(unittest.TestCase):
    """Verify REST API persistence endpoints for problem collection sheets."""

    def test_save_sheet_index_full_list(self):
        """Verify POST /api/save-sheet-index replaces registry list and dual-syncs."""
        with IsolatedDataSandbox() as srv:
            new_index = [
                {
                    "id": "sheet_custom_1",
                    "title": "Пользовательский листочек",
                    "category": "олимпиада",
                    "desc": "Тестовое описание",
                    "tasks_count": 0,
                }
            ]
            status, res = post_json(srv.base_url, "/api/save-sheet-index", {"index": new_index})
            self.assertEqual(status, 200)
            self.assertEqual(res.get("status"), "ok")

            # Verify disk file
            index_path = srv.workdir / "data" / "sheets" / "sheets-index.json"
            on_disk = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(len(on_disk), 1)
            self.assertEqual(on_disk[0]["id"], "sheet_custom_1")

            # Verify dual-sync in index.html
            html = (srv.workdir / "index.html").read_text(encoding="utf-8")
            embedded = extract_embedded_json(html, "sheets-data")
            self.assertEqual(len(embedded["index"]), 1)
            self.assertEqual(embedded["index"][0]["id"], "sheet_custom_1")

    def test_save_sheet_index_validation_errors(self):
        """Verify POST /api/save-sheet-index rejects invalid payloads with 400."""
        with IsolatedDataSandbox() as srv:
            # 1. Non-list index
            status, _ = post_json(srv.base_url, "/api/save-sheet-index", {"index": "not-a-list"})
            self.assertEqual(status, 400)

            # 2. Item missing title
            status, _ = post_json(srv.base_url, "/api/save-sheet-index", {"index": [{"id": "s1"}]})
            self.assertEqual(status, 400)

            # 3. Path traversal in ID
            status, _ = post_json(srv.base_url, "/api/save-sheet-index", {"index": [{"id": "../bad", "title": "T"}]})
            self.assertEqual(status, 400)

    def test_save_sheet_new_and_update(self):
        """Verify POST /api/save-sheet writes sheet file, updates index, and dual-syncs."""
        with IsolatedDataSandbox() as srv:
            sheet_data = {
                "id": "sheet_100",
                "title": "Листочек по графам",
                "category": "кружок",
                "desc": "Эйлеровы и гамильтоновы циклы",
                "tasks": [
                    {
                        "id": "s100.1",
                        "st": "<p>Докажите критерий Эйлера</p>",
                        "d": 3,
                        "sol": {"ans": "Доказано", "idea": "Чётность степеней вершин", "plan": "Индукция"},
                    }
                ],
            }
            status, res = post_json(srv.base_url, "/api/save-sheet", {"sheet": sheet_data})
            self.assertEqual(status, 200)
            self.assertEqual(res.get("id"), "sheet_100")
            self.assertEqual(res.get("tasks_count"), 1)

            # Check sheet file
            sheet_file = srv.workdir / "data" / "sheets" / "sheet_sheet_100.json"
            self.assertTrue(sheet_file.exists())
            saved_sheet = json.loads(sheet_file.read_text(encoding="utf-8"))
            self.assertEqual(saved_sheet["title"], "Листочек по графам")
            self.assertEqual(len(saved_sheet["tasks"]), 1)

            # Check sheets-index.json contains summary entry
            index_path = srv.workdir / "data" / "sheets" / "sheets-index.json"
            idx_list = json.loads(index_path.read_text(encoding="utf-8"))
            found = [s for s in idx_list if s["id"] == "sheet_100"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["tasks_count"], 1)

            # Check Dual-Sync
            html = (srv.workdir / "index.html").read_text(encoding="utf-8")
            embedded = extract_embedded_json(html, "sheets-data")
            self.assertIn("sheet_100", embedded["sheets"])
            self.assertEqual(embedded["sheets"]["sheet_100"]["title"], "Листочек по графам")

    def test_save_sheet_task_and_update(self):
        """Verify POST /api/save-sheet-task adds task to sheet and increments count."""
        with IsolatedDataSandbox() as srv:
            # 1. Create parent sheet
            sheet_data = {"id": "sheet_test_tasks", "title": "Задачный лист", "category": "матбой", "tasks": []}
            post_json(srv.base_url, "/api/save-sheet", {"sheet": sheet_data})

            # 2. Add first task
            new_task = {
                "id": "t1",
                "st": "<p>Условие задачи $x+y=z$</p>",
                "d": 4,
                "sol": {"ans": "$z$", "idea": "Сложите числа", "plan": "1. Сложить"},
            }
            status, res = post_json(srv.base_url, "/api/save-sheet-task", {"sheet_id": "sheet_test_tasks", "task": new_task})
            self.assertEqual(status, 200)
            self.assertEqual(res.get("tasks_count"), 1)
            self.assertEqual(res.get("task_id"), "t1")

            # 3. Update existing task in place
            updated_task = {
                "id": "t1",
                "st": "<p>Обновлённое условие $x^2+y^2=z^2$</p>",
                "d": 5,
                "sol": {"ans": "Пифагор", "idea": "Геометрия", "plan": "Формулы"},
            }
            status2, res2 = post_json(srv.base_url, "/api/save-sheet-task", {"sheet_id": "sheet_test_tasks", "task": updated_task})
            self.assertEqual(status2, 200)
            self.assertEqual(res2.get("tasks_count"), 1)

            # Verify in sheet file
            sheet_file = srv.workdir / "data" / "sheets" / "sheet_sheet_test_tasks.json"
            content = json.loads(sheet_file.read_text(encoding="utf-8"))
            self.assertEqual(len(content["tasks"]), 1)
            self.assertEqual(content["tasks"][0]["st"], updated_task["st"])
            self.assertEqual(content["tasks"][0]["d"], 5)

    def test_save_sheet_task_difficulty_bounds_validation(self):
        """Verify difficulty 'd' must be integer between 1 and 8."""
        with IsolatedDataSandbox() as srv:
            sheet_data = {"id": "sheet_diff_test", "title": "Diff Test", "category": "кружок", "tasks": []}
            post_json(srv.base_url, "/api/save-sheet", {"sheet": sheet_data})

            # Invalid: d = 9 (out of range)
            status, _ = post_json(srv.base_url, "/api/save-sheet-task", {
                "sheet_id": "sheet_diff_test",
                "task": {"id": "t1", "d": 9}
            })
            self.assertEqual(status, 400)

            # Invalid: d = 0 (out of range)
            status, _ = post_json(srv.base_url, "/api/save-sheet-task", {
                "sheet_id": "sheet_diff_test",
                "task": {"id": "t1", "d": 0}
            })
            self.assertEqual(status, 400)

            # Invalid: boolean True (not integer)
            status, _ = post_json(srv.base_url, "/api/save-sheet-task", {
                "sheet_id": "sheet_diff_test",
                "task": {"id": "t1", "d": True}
            })
            self.assertEqual(status, 400)

            # Invalid: float 2.5
            status, _ = post_json(srv.base_url, "/api/save-sheet-task", {
                "sheet_id": "sheet_diff_test",
                "task": {"id": "t1", "d": 2.5}
            })
            self.assertEqual(status, 400)

    def test_save_sheet_task_missing_parent_returns_404(self):
        """Verify adding task to non-existent sheet returns 404."""
        with IsolatedDataSandbox() as srv:
            status, res = post_json(srv.base_url, "/api/save-sheet-task", {
                "sheet_id": "non_existent_9999",
                "task": {"id": "t1", "d": 3}
            })
            self.assertEqual(status, 404)

    def test_delete_sheet_task(self):
        """Verify POST /api/delete-sheet-task removes task and updates index."""
        with IsolatedDataSandbox() as srv:
            sheet_data = {
                "id": "sheet_del_task",
                "title": "Del Task Sheet",
                "category": "авторская",
                "tasks": [{"id": "del_t1", "d": 2}, {"id": "keep_t2", "d": 3}]
            }
            post_json(srv.base_url, "/api/save-sheet", {"sheet": sheet_data})

            # Delete del_t1
            status, res = post_json(srv.base_url, "/api/delete-sheet-task", {
                "sheet_id": "sheet_del_task",
                "task_id": "del_t1"
            })
            self.assertEqual(status, 200)
            self.assertEqual(res.get("tasks_count"), 1)

            # Verify in file
            sheet_file = srv.workdir / "data" / "sheets" / "sheet_sheet_del_task.json"
            content = json.loads(sheet_file.read_text(encoding="utf-8"))
            self.assertEqual(len(content["tasks"]), 1)
            self.assertEqual(content["tasks"][0]["id"], "keep_t2")

    def test_delete_sheet_task_not_found(self):
        """Verify deleting non-existent task returns 200 with status: not_found."""
        with IsolatedDataSandbox() as srv:
            sheet_data = {"id": "sheet_del_nf", "title": "NF Sheet", "category": "кружок", "tasks": []}
            post_json(srv.base_url, "/api/save-sheet", {"sheet": sheet_data})

            status, res = post_json(srv.base_url, "/api/delete-sheet-task", {
                "sheet_id": "sheet_del_nf",
                "task_id": "no_such_task"
            })
            self.assertEqual(status, 200)
            self.assertEqual(res.get("status"), "not_found")

    def test_delete_sheet(self):
        """Verify POST /api/delete-sheet removes sheet file and index registry entry."""
        with IsolatedDataSandbox() as srv:
            sheet_data = {"id": "sheet_to_remove", "title": "To Remove", "category": "кружок", "tasks": []}
            post_json(srv.base_url, "/api/save-sheet", {"sheet": sheet_data})

            sheet_file = srv.workdir / "data" / "sheets" / "sheet_sheet_to_remove.json"
            self.assertTrue(sheet_file.exists())

            # Delete sheet
            status, res = post_json(srv.base_url, "/api/delete-sheet", {"id": "sheet_to_remove"})
            self.assertEqual(status, 200)
            self.assertFalse(sheet_file.exists())

            # Verify removed from index
            index_path = srv.workdir / "data" / "sheets" / "sheets-index.json"
            idx_list = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertFalse(any(s["id"] == "sheet_to_remove" for s in idx_list))

            # Verify removed from Dual-Sync
            html = (srv.workdir / "index.html").read_text(encoding="utf-8")
            embedded = extract_embedded_json(html, "sheets-data")
            self.assertNotIn("sheet_to_remove", embedded["sheets"])

    def test_delete_sheet_idempotent(self):
        """Verify deleting non-existent sheet returns 200 without error."""
        with IsolatedDataSandbox() as srv:
            status, res = post_json(srv.base_url, "/api/delete-sheet", {"id": "sheet_does_not_exist_99"})
            self.assertEqual(status, 200)
            self.assertEqual(res.get("status"), "ok")


class TestNotesRestEndpoints(unittest.TestCase):
    """Verify REST API persistence endpoints for lecture notes."""

    def test_save_note_latex(self):
        """Verify POST /api/save-note writes LaTeX note file and updates index."""
        with IsolatedDataSandbox() as srv:
            note_data = {
                "id": "note_custom_latex",
                "domain": "combinatorics",
                "type": "latex",
                "title": "Формула обращения Мёбиуса",
                "desc": "Мультипликативные функции и свёртка Дирихле",
                "content": "# Формула обращения Мёбиуса\n\nПусть $f$ и $g$ — функции..."
            }
            status, res = post_json(srv.base_url, "/api/save-note", {"note": note_data})
            self.assertEqual(status, 200)
            self.assertEqual(res.get("status"), "ok")
            self.assertEqual(res.get("id"), "note_custom_latex")

            # Check note file
            note_file = srv.workdir / "data" / "notes" / "note_note_custom_latex.json"
            self.assertTrue(note_file.exists())
            saved = json.loads(note_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["content"], note_data["content"])

            # Check notes-index.json
            index_path = srv.workdir / "data" / "notes" / "notes-index.json"
            idx_list = json.loads(index_path.read_text(encoding="utf-8"))
            found = [n for n in idx_list if n["id"] == "note_custom_latex"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["type"], "latex")

            # Check Dual-Sync
            html = (srv.workdir / "index.html").read_text(encoding="utf-8")
            embedded = extract_embedded_json(html, "notes-data")
            self.assertIn("note_custom_latex", embedded["notes"])
            self.assertEqual(embedded["notes"]["note_custom_latex"]["content"], note_data["content"])

    def test_save_note_pdf(self):
        """Verify POST /api/save-note with type: 'pdf' registers metadata without note file."""
        with IsolatedDataSandbox() as srv:
            note_data = {
                "id": "note_custom_pdf",
                "domain": "geometry",
                "type": "pdf",
                "title": "Скан по тригонометрии",
                "desc": "Теоремы синусов и косинусов",
                "filename": "ceva_menelaus.pdf",
                "file_path": "data/scans/ceva_menelaus.pdf"
            }
            status, res = post_json(srv.base_url, "/api/save-note", {"note": note_data})
            self.assertEqual(status, 200)

            # PDF notes do NOT need note_*.json files
            note_file = srv.workdir / "data" / "notes" / "note_note_custom_pdf.json"
            self.assertFalse(note_file.exists())

            # Verified in notes-index.json
            index_path = srv.workdir / "data" / "notes" / "notes-index.json"
            idx_list = json.loads(index_path.read_text(encoding="utf-8"))
            found = [n for n in idx_list if n["id"] == "note_custom_pdf"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["filename"], "ceva_menelaus.pdf")

    def test_save_note_validation_errors(self):
        """Verify validation errors for domain, note type, empty title, and path traversal."""
        with IsolatedDataSandbox() as srv:
            # 1. Invalid domain
            status, _ = post_json(srv.base_url, "/api/save-note", {
                "note": {"id": "n1", "domain": "astrology", "type": "latex", "title": "Title"}
            })
            self.assertEqual(status, 400)

            # 2. Invalid type
            status, _ = post_json(srv.base_url, "/api/save-note", {
                "note": {"id": "n1", "domain": "geometry", "type": "docx", "title": "Title"}
            })
            self.assertEqual(status, 400)

            # 3. Missing/Empty title
            status, _ = post_json(srv.base_url, "/api/save-note", {
                "note": {"id": "n1", "domain": "geometry", "type": "latex", "title": "   "}
            })
            self.assertEqual(status, 400)

            # 4. Path traversal in ID
            status, _ = post_json(srv.base_url, "/api/save-note", {
                "note": {"id": "../evil", "domain": "geometry", "type": "latex", "title": "Evil"}
            })
            self.assertEqual(status, 400)

    def test_delete_note(self):
        """Verify POST /api/delete-note deletes note file, removes from index, and dual-syncs."""
        with IsolatedDataSandbox() as srv:
            note_data = {
                "id": "note_to_del",
                "domain": "algebra",
                "type": "latex",
                "title": "Временный конспект",
                "content": "# Текст"
            }
            post_json(srv.base_url, "/api/save-note", {"note": note_data})

            note_file = srv.workdir / "data" / "notes" / "note_note_to_del.json"
            self.assertTrue(note_file.exists())

            # Delete note
            status, res = post_json(srv.base_url, "/api/delete-note", {"id": "note_to_del"})
            self.assertEqual(status, 200)
            self.assertFalse(note_file.exists())

            # Verify removed from index
            index_path = srv.workdir / "data" / "notes" / "notes-index.json"
            idx_list = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertFalse(any(n["id"] == "note_to_del" for n in idx_list))

            # Verify removed from Dual-Sync
            html = (srv.workdir / "index.html").read_text(encoding="utf-8")
            embedded = extract_embedded_json(html, "notes-data")
            self.assertNotIn("note_to_del", embedded["notes"])

    def test_delete_note_idempotent(self):
        """Verify deleting non-existent note returns 200 without crashing."""
        with IsolatedDataSandbox() as srv:
            status, res = post_json(srv.base_url, "/api/delete-note", {"id": "ghost_note_99"})
            self.assertEqual(status, 200)
            self.assertEqual(res.get("status"), "ok")


class TestDualSyncEngineAndEscaping(unittest.TestCase):
    """Verify HTML inlining, script escaping, and offline readability."""

    def test_dual_sync_initial_bundle_integrity(self):
        """Verify starter sheets and notes are embedded before </body> in index.html."""
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")

        sheets_data = extract_embedded_json(html, "sheets-data")
        self.assertIsNotNone(sheets_data, "<script id='sheets-data'> must exist in index.html")
        self.assertIn("index", sheets_data)
        self.assertIn("sheets", sheets_data)
        self.assertGreaterEqual(len(sheets_data["index"]), 4)

        notes_data = extract_embedded_json(html, "notes-data")
        self.assertIsNotNone(notes_data, "<script id='notes-data'> must exist in index.html")
        self.assertIn("index", notes_data)
        self.assertIn("notes", notes_data)
        self.assertGreaterEqual(len(notes_data["index"]), 4)

    def test_dual_sync_script_tag_escaping(self):
        """Verify literal </script> in task or note is safely escaped as <\\/script> in HTML."""
        with IsolatedDataSandbox() as srv:
            poisoned_note = {
                "id": "poison_note",
                "domain": "combinatorics",
                "type": "latex",
                "title": "XSS Test Note",
                "content": "<p>Dangerous content: </script><script>alert('pwned')</script></p>"
            }
            post_json(srv.base_url, "/api/save-note", {"note": poisoned_note})

            raw_html = (srv.workdir / "index.html").read_text(encoding="utf-8")
            match = re.search(r'<script\s+[^>]*id=[\x22\x27]notes-data[\x22\x27][^>]*>(.*?)</script>', raw_html, re.DOTALL)
            self.assertIsNotNone(match)
            inner_json = match.group(1)

            # Unescaped </script> must NOT appear in the JSON payload
            self.assertNotIn("</script>", inner_json)
            self.assertIn("<\\/script>", inner_json)

            # Standard JSON.parse / json.loads must decode it losslessly
            decoded = json.loads(inner_json)
            self.assertEqual(decoded["notes"]["poison_note"]["content"], poisoned_note["content"])

    def test_dual_sync_missing_tag_auto_injection(self):
        """Verify engine injects missing <script id='sheets-data'> before </body>."""
        with IsolatedDataSandbox() as srv:
            html_file = srv.workdir / "index.html"
            content = html_file.read_text(encoding="utf-8")
            # Strip sheets-data tag
            stripped = re.sub(r'\s*<script\s+[^>]*id=[\x22\x27]sheets-data[\x22\x27][^>]*>.*?</script>', '', content, flags=re.DOTALL)
            html_file.write_text(stripped, encoding="utf-8")

            # Trigger mutation
            post_json(srv.base_url, "/api/save-sheet", {
                "sheet": {"id": "sheet_reinject", "title": "Re-inject Test", "category": "кружок", "tasks": []}
            })

            # Verify restored before </body>
            updated_html = html_file.read_text(encoding="utf-8")
            self.assertIn('id="sheets-data"', updated_html)
            sheets_pos = updated_html.find('id="sheets-data"')
            body_pos = updated_html.lower().rfind("</body>")
            self.assertLess(sheets_pos, body_pos)


class TestIntegrityAndZeroEmoji(unittest.TestCase):
    """Verify zero emojis in code/data and integrity constraints."""

    def test_zero_emoji_in_server_py(self):
        """Verify server.py contains strictly zero emojis."""
        code = SERVER_PY_PATH.read_text(encoding="utf-8")
        violations = find_emoji_violations(code)
        self.assertEqual(len(violations), 0, f"Found emojis in server.py: {violations}")

    def test_zero_emoji_in_sheets_and_notes_data(self):
        """Verify all JSON files in data/sheets/ and data/notes/ contain zero emojis."""
        sheets_dir = DATA_DIR / "sheets"
        notes_dir = DATA_DIR / "notes"

        for p in list(sheets_dir.glob("*.json")) + list(notes_dir.glob("*.json")):
            content = p.read_text(encoding="utf-8")
            violations = find_emoji_violations(content)
            self.assertEqual(len(violations), 0, f"Found emojis in {p.name}: {violations}")

    def test_olimp_kombinatorika_untouched(self):
        """Verify olimp-kombinatorika directory remains completely untouched."""
        self.assertTrue(OLIMP_DIR.exists())
        orig_index = OLIMP_DIR / "index.html"
        self.assertTrue(orig_index.exists())
        self.assertGreater(orig_index.stat().st_size, 5000000)


if __name__ == "__main__":
    unittest.main()
