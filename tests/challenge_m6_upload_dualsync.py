"""Empirical Adversarial Stress Test Suite: Milestone 6 PDF Upload & Dual-Sync Engine.

Conducted by Challenger 2 for Milestone 6:
1. Multipart PDF upload:
   - Invalid extensions (.exe, .txt, .png, empty extension, double extension, trailing dots, dotfiles)
   - Invalid magic headers (ELF binary, PE/MZ binary, PNG, ZIP, plain text, leading whitespace, empty file)
   - Path traversal attempts (POSIX ../, Windows ..\\, absolute drive C:\\, root /, Windows device stems CON, NUL, AUX, PRN, COM1-9, LPT1-9)
   - Oversized payload rejection (>50MB, HTTP 413, invalid Content-Length headers)
2. Dual-Sync Engine:
   - LaTeX mathematical expressions (\\frac, \\sum, \\sqrt, \\int, matrices, backreferences \\1, \\2, \\b, etc.)
   - HTML5 script tag escaping (</script>, </SCRIPT>, </Script>, </script >, XSS injection attempts)
   - HTML structural preservation (zero corruption of doctype, body, or existing milestone assets)
3. Stress & Concurrency:
   - Concurrent uploads and concurrent Dual-Sync saves under WRITE_LOCK
   - Cyrillic / Unicode filename sanitization
   - Header formatting and case variants
"""

import concurrent.futures
import http.client
import json
import re
import shutil
import socket
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
    find_free_port,
)


class SafeSandboxEnvironment:
    """Isolated temporary directory mirroring math-archive ignoring ephemeral files."""

    def __init__(self):
        self.temp_dir = tempfile.mkdtemp(prefix="math_archive_sandbox_")
        self.path = Path(self.temp_dir)
        self.math_archive_path = self.path / "math-archive"
        self.data_path = self.math_archive_path / "data"

    def __enter__(self):
        if MATH_ARCHIVE_DIR.exists():
            shutil.copytree(
                MATH_ARCHIVE_DIR,
                self.math_archive_path,
                ignore=shutil.ignore_patterns("tests", "*.tmp", "__pycache__"),
            )
        else:
            self.math_archive_path.mkdir(parents=True, exist_ok=True)
            self.data_path.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass


class SafeIsolatedSandbox:
    """Isolated test sandbox providing an ephemeral ServerInstance."""

    def __init__(self, port: int = None):
        self.sandbox = SafeSandboxEnvironment()
        self.port = port
        self.server = None

    def __enter__(self) -> ServerInstance:
        self.sandbox.__enter__()
        self.server = ServerInstance(workdir=self.sandbox.math_archive_path, port=self.port)
        if not self.server.start():
            self.sandbox.__exit__(None, None, None)
            raise RuntimeError(f"Failed to start server on port {self.server.port}")
        return self.server

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.server:
            try:
                self.server.stop()
            except Exception:
                pass
        self.sandbox.__exit__(exc_type, exc_val, exc_tb)


def make_valid_pdf_bytes(title: str = "Empirical Challenge PDF") -> bytes:
    """Generate valid ISO 32000-1 binary PDF byte stream."""
    stream_content = f"BT /F1 14 Tf 50 750 Td ({title}) Tj ET\n".encode("latin-1")
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


def upload_multipart(
    base_url: str,
    filename: str,
    file_bytes: bytes,
    content_type: str = "application/pdf",
    custom_content_type_header: str = None,
    custom_headers: dict = None,
) -> tuple[int, dict]:
    """Upload a file to /api/upload-pdf using standard multipart/form-data encoding."""
    boundary = "----ChallengeBoundary" + uuid.uuid4().hex
    url = urllib.parse.urljoin(base_url, "/api/upload-pdf")

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = {
        "Content-Type": custom_content_type_header or f"multipart/form-data; boundary={boundary}",
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


def post_json_api(base_url: str, endpoint: str, payload: dict) -> tuple[int, dict]:
    """Execute POST JSON request to server."""
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
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        data = err.read().decode("utf-8")
        try:
            parsed = json.loads(data)
        except Exception:
            parsed = {"raw": data}
        return err.code, parsed


class TestAdversarialPdfUpload(unittest.TestCase):
    """Adversarial stress tests for POST /api/upload-pdf."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox_ctx = SafeIsolatedSandbox()
        cls.srv = cls.sandbox_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.sandbox_ctx.__exit__(None, None, None)

    def test_invalid_extensions_rejected(self):
        """Verify non-PDF extensions and edge-case filenames are strictly rejected with HTTP 400."""
        valid_pdf = make_valid_pdf_bytes("Test Extension Rejection")
        invalid_names = [
            "malware.exe",
            "notes.txt",
            "diagram.png",
            "index.html",
            "app.js",
            "exploit.py",
            "script.sh",
            "run.bat",
            "data.bin",
            "no_extension",
            "trailing_dot.",
            ".pdf",          # Dotfile with empty stem: Path('.pdf').suffix is ''
            "exploit.pdf.exe",
            "hidden.pdf.txt",
        ]
        for bad_name in invalid_names:
            status, res = upload_multipart(self.srv.base_url, bad_name, valid_pdf)
            self.assertEqual(
                status, 400,
                f"Expected HTTP 400 for invalid extension '{bad_name}', got {status}: {res}"
            )
            self.assertEqual(res.get("status"), "error")
            self.assertIn("Only PDF files (.pdf) are permitted", res.get("message", ""))

        # Verify no files were created in data/scans/
        scans_dir = self.srv.workdir / "data" / "scans"
        existing_files = [f.name for f in scans_dir.glob("*") if f.name.endswith(".exe") or f.name.endswith(".txt")]
        self.assertEqual(existing_files, [])

    def test_invalid_magic_headers_rejected(self):
        """Verify files lacking ISO 32000-1 %PDF- magic bytes are rejected with HTTP 400."""
        invalid_payloads = [
            ("elf_binary.pdf", b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
            ("pe_binary.pdf", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"),
            ("png_image.pdf", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"),
            ("zip_archive.pdf", b"PK\x03\x04\x14\x00\x00\x00\x08\x00"),
            ("plain_text.pdf", b"This is plain ASCII text pretending to be a PDF file."),
            ("leading_whitespace.pdf", b"   %PDF-1.4\nvalid pdf body after spaces"),
            ("leading_newline.pdf", b"\n%PDF-1.4\nvalid pdf body after newline"),
            ("truncated_magic.pdf", b"%PD"),
        ]
        for fn, payload in invalid_payloads:
            status, res = upload_multipart(self.srv.base_url, fn, payload)
            self.assertEqual(
                status, 400,
                f"Expected HTTP 400 for invalid magic header in '{fn}', got {status}: {res}"
            )
            self.assertEqual(res.get("status"), "error")
            self.assertIn("missing %PDF- header", res.get("message", ""))

        # Test empty file (0 bytes)
        status, res = upload_multipart(self.srv.base_url, "empty.pdf", b"")
        self.assertEqual(status, 400)
        self.assertIn("Empty file uploaded", res.get("message", ""))

    def test_path_traversal_attempts(self):
        """Verify path traversal sequences are neutralized and files remain confined to data/scans/."""
        valid_pdf = make_valid_pdf_bytes("Traversal Immunity Test")
        scans_dir = self.srv.workdir / "data" / "scans"

        traversal_vectors = [
            ("../../etc/passwd", 400),  # No .pdf ext -> rejected
            ("..\\..\\Windows\\system32\\calc.exe", 400),  # No .pdf ext -> rejected
            ("C:\\autoexec.bat", 400),  # No .pdf ext -> rejected
            ("../../etc/passwd.pdf", 200),  # Valid ext -> sanitized to passwd.pdf
            ("..\\..\\Windows\\system32\\calc.pdf", 200),  # Valid ext -> sanitized to calc.pdf
            ("C:\\autoexec.pdf", 200),  # Windows drive -> sanitized to autoexec.pdf
            ("C:/Windows/win_ini.pdf", 200),  # Windows forward-slash drive -> win_ini.pdf
            ("/etc/shadow.pdf", 200),  # Unix root path -> sanitized to shadow.pdf
            ("../../../../deep_escape.pdf", 200),  # Deep traversal -> deep_escape.pdf
            ("D:\\data\\projects\\archive\\lecture.pdf", 200),  # Windows absolute -> lecture.pdf
        ]

        for path_vector, expected_status in traversal_vectors:
            status, res = upload_multipart(self.srv.base_url, path_vector, valid_pdf)
            self.assertEqual(
                status, expected_status,
                f"Unexpected status for vector '{path_vector}': {status}, response: {res}"
            )
            if expected_status == 200:
                self.assertEqual(res.get("status"), "ok")
                saved_filename = res.get("filename")
                self.assertTrue(saved_filename.endswith(".pdf"))
                self.assertNotIn("/", saved_filename)
                self.assertNotIn("\\", saved_filename)
                self.assertNotIn("..", saved_filename)
                # Verify target file exists strictly within scans_dir
                target_file = scans_dir / saved_filename
                self.assertTrue(target_file.exists())
                # Ensure file didn't escape scans_dir
                self.assertEqual(target_file.resolve().parent, scans_dir.resolve())

        # Verify no rogue files escaped to parent or root directories
        self.assertFalse((self.srv.workdir / "passwd.pdf").exists())
        self.assertFalse((self.srv.workdir / "calc.pdf").exists())
        self.assertFalse((self.srv.workdir / "autoexec.pdf").exists())
        self.assertFalse((self.srv.workdir / "shadow.pdf").exists())
        self.assertFalse((self.srv.workdir / "deep_escape.pdf").exists())

    def test_windows_reserved_device_names(self):
        """Verify Windows reserved device stems (CON, PRN, AUX, NUL, COM1-9, LPT1-9) are safely renamed."""
        valid_pdf = make_valid_pdf_bytes("Windows Device Reserved Test")
        scans_dir = self.srv.workdir / "data" / "scans"
        device_names = [
            "CON.pdf", "con.pdf",
            "PRN.pdf", "prn.pdf",
            "AUX.pdf", "aux.pdf",
            "NUL.pdf", "nul.pdf",
            "COM1.pdf", "com1.pdf", "COM9.pdf",
            "LPT1.pdf", "lpt1.pdf", "LPT9.pdf",
        ]

        for dev_name in device_names:
            status, res = upload_multipart(self.srv.base_url, dev_name, valid_pdf)
            self.assertEqual(status, 200, f"Failed upload for device name '{dev_name}': {res}")
            saved_filename = res.get("filename")
            # Filename must NOT equal the reserved device stem + .pdf
            stem_upper = Path(dev_name).stem.upper()
            self.assertNotEqual(
                saved_filename.upper(), f"{stem_upper}.PDF",
                f"Device stem {stem_upper} was not renamed: got {saved_filename}"
            )
            self.assertTrue(saved_filename.startswith("scan_"))
            self.assertTrue(saved_filename.endswith(".pdf"))

            # Verify file was written and can be read back cleanly
            target_file = scans_dir / saved_filename
            self.assertTrue(target_file.exists())
            self.assertEqual(target_file.read_bytes(), valid_pdf)

    def test_oversized_payload_rejection_50mb(self):
        """Verify payload exceeding 50MB is rejected with HTTP 413 without memory exhaustion."""
        # 50MB limit is 50 * 1024 * 1024 = 52,428,800 bytes
        oversized_length = 50 * 1024 * 1024 + 1024  # 50MB + 1KB
        parsed = urllib.parse.urlparse(self.srv.base_url)
        host = parsed.hostname
        port = parsed.port

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        try:
            s.connect((host, port))
            boundary = "----OversizeBoundary123"
            header_data = (
                f"POST /api/upload-pdf HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Content-Type: multipart/form-data; boundary={boundary}\r\n"
                f"Content-Length: {oversized_length}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("utf-8")
            s.sendall(header_data)

            response_bytes = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                response_bytes += chunk

            response_text = response_bytes.decode("utf-8", errors="replace")
            self.assertIn("413", response_text, f"Expected HTTP 413, got: {response_text[:200]}")
            self.assertIn("Payload too large", response_text)
        finally:
            s.close()

    def test_content_length_header_edge_cases(self):
        """Verify missing, zero, negative, and invalid Content-Length headers return HTTP 400."""
        parsed = urllib.parse.urlparse(self.srv.base_url)
        host = parsed.hostname
        port = parsed.port

        cases = [
            ("Missing Content-Length", f"POST /api/upload-pdf HTTP/1.1\r\nHost: {host}:{port}\r\nContent-Type: multipart/form-data; boundary=b\r\nConnection: close\r\n\r\n"),
            ("Zero Content-Length", f"POST /api/upload-pdf HTTP/1.1\r\nHost: {host}:{port}\r\nContent-Type: multipart/form-data; boundary=b\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"),
            ("Negative Content-Length", f"POST /api/upload-pdf HTTP/1.1\r\nHost: {host}:{port}\r\nContent-Type: multipart/form-data; boundary=b\r\nContent-Length: -10\r\nConnection: close\r\n\r\n"),
            ("Non-integer Content-Length", f"POST /api/upload-pdf HTTP/1.1\r\nHost: {host}:{port}\r\nContent-Type: multipart/form-data; boundary=b\r\nContent-Length: invalid\r\nConnection: close\r\n\r\n"),
        ]

        for label, raw_req in cases:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            try:
                s.connect((host, port))
                s.sendall(raw_req.encode("utf-8"))
                resp = s.recv(4096).decode("utf-8", errors="replace")
                self.assertIn("400", resp, f"Expected HTTP 400 for '{label}', got: {resp[:200]}")
            finally:
                s.close()


class TestAdversarialDualSync(unittest.TestCase):
    """Adversarial stress tests for Dual-Sync HTML synchronization and escaping."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox_ctx = SafeIsolatedSandbox()
        cls.srv = cls.sandbox_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.sandbox_ctx.__exit__(None, None, None)

    def test_latex_backslashes_preservation(self):
        """Verify complex LaTeX math with backslashes is perfectly preserved in index.html without corruption."""
        complex_latex_formulas = [
            r"\frac{\partial^2 \psi}{\partial x^2} + \frac{8\pi^2 m}{h^2}(E - V)\psi = 0",
            r"\sum_{n=1}^\infty \frac{(-1)^{n-1}}{n} = \ln 2",
            r"\int_{-\infty}^{+\infty} e^{-x^2} dx = \sqrt{\pi}",
            r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}",
            r"\lim_{x \to 0} \frac{\sin x}{x} = 1",
            r"\1 \2 \g<1> \b \n \t \r \a \f \v \\ \/ \^ \$ \* \+ \? \. \[ \] \{ \}",
        ]

        sheet_payload = {
            "id": "sheet_latex_stress",
            "title": r"Листок с формулами $\sum \frac{1}{n^2}$",
            "category": "олимпиада",
            "desc": r"Подробный разбор формул \int e^{-x^2} dx и матриц \begin{pmatrix} 1 & 0 \\ 0 & 1 \end{pmatrix}",
            "tasks": [
                {
                    "num": i + 1,
                    "title": f"Задача {i + 1}",
                    "d": 4,
                    "text": formula,
                    "answer": r"\frac{\sqrt{5} + 1}{2}",
                    "hint": r"Используйте подстановку $u = \sqrt{x}$",
                }
                for i, formula in enumerate(complex_latex_formulas)
            ],
        }

        status, res = post_json_api(self.srv.base_url, "/api/save-sheet", {"sheet": sheet_payload})
        self.assertEqual(status, 200, f"Save sheet failed: {res}")

        html_content = (self.srv.workdir / "index.html").read_text(encoding="utf-8")
        embedded_data = extract_embedded_json(html_content, "sheets-data")

        retrieved = embedded_data["sheets"]["sheet_latex_stress"]
        self.assertEqual(retrieved["title"], sheet_payload["title"])
        self.assertEqual(retrieved["desc"], sheet_payload["desc"])

        for i, formula in enumerate(complex_latex_formulas):
            task_text = retrieved["tasks"][i]["text"]
            self.assertEqual(
                task_text, formula,
                f"LaTeX backslash corruption in task {i + 1}!\nOriginal:  {formula!r}\nRetrieved: {task_text!r}"
            )

    def test_closing_script_tag_escaping_and_xss_protection(self):
        """Verify closing script tags </script> in payloads are escaped to prevent breaking HTML structure."""
        adversarial_strings = [
            "</script>",
            "</SCRIPT>",
            "</Script>",
            "</script >",
            "</script\n>",
            "</script><script>alert('xss_attack')</script>",
            r"Math with script: $\frac{a}{b}$ </script> continue math $\sum$",
        ]

        note_payload = {
            "id": "note_script_xss",
            "title": "Adversarial Note with </script> Tags",
            "domain": "combinatorics",
            "type": "latex",
            "tags": ["xss", "</script>", r"\frac{1}{2}"],
            "content": "\n".join(adversarial_strings),
        }

        status, res = post_json_api(self.srv.base_url, "/api/save-note", {"note": note_payload})
        self.assertEqual(status, 200, f"Save note failed: {res}")

        html_content = (self.srv.workdir / "index.html").read_text(encoding="utf-8")

        # Verify that literal unescaped '</script>' does NOT appear inside the JSON content before the real tag closes
        match = re.search(r'<script\s+id=[\x22\x27]notes-data[\x22\x27][^>]*>(.*?)</script>', html_content, flags=re.DOTALL | re.IGNORECASE)
        self.assertIsNotNone(match, "Could not find <script id='notes-data'> in index.html")
        raw_script_inner = match.group(1)

        unescaped_closing = re.findall(r'</script', raw_script_inner, flags=re.IGNORECASE)
        self.assertEqual(
            unescaped_closing, [],
            f"Found unescaped closing script tag inside <script id='notes-data'>: {unescaped_closing}"
        )

        embedded_notes = extract_embedded_json(html_content, "notes-data")
        retrieved_note = embedded_notes["notes"]["note_script_xss"]

        self.assertEqual(retrieved_note["title"], note_payload["title"])
        self.assertEqual(retrieved_note["domain"], note_payload["domain"])
        self.assertEqual(retrieved_note["type"], note_payload["type"])
        self.assertEqual(
            retrieved_note["content"], note_payload["content"],
            "Note content containing </script> was not losslessly recovered!"
        )

    def test_dual_sync_html_structural_integrity(self):
        """Verify repeated Dual-Sync updates preserve overall HTML structure and existing elements."""
        initial_html = (self.srv.workdir / "index.html").read_text(encoding="utf-8")
        self.assertTrue(initial_html.lower().strip().startswith("<!doctype html>"))
        self.assertIn("</html>", initial_html)
        self.assertIn("</body>", initial_html)

        # Perform 10 consecutive sheet updates
        for i in range(10):
            payload = {
                "id": f"sheet_stress_{i}",
                "title": f"Stress Sheet #{i}",
                "category": "кружок",
                "desc": f"Description {i} with \\frac{{{i}}}{{100}}",
                "tasks": [],
            }
            status, res = post_json_api(self.srv.base_url, "/api/save-sheet", {"sheet": payload})
            self.assertEqual(status, 200)

        final_html = (self.srv.workdir / "index.html").read_text(encoding="utf-8")

        self.assertEqual(final_html.lower().count("<!doctype html>"), initial_html.lower().count("<!doctype html>"))
        self.assertEqual(final_html.lower().count("</html>"), initial_html.lower().count("</html>"))
        self.assertEqual(final_html.lower().count("</body>"), initial_html.lower().count("</body>"))

        sheets_tag_count = len(re.findall(r'<script\s+[^>]*id=[\x22\x27]sheets-data[\x22\x27]', final_html, re.IGNORECASE))
        notes_tag_count = len(re.findall(r'<script\s+[^>]*id=[\x22\x27]notes-data[\x22\x27]', final_html, re.IGNORECASE))
        self.assertEqual(sheets_tag_count, 1, f"Expected exactly 1 sheets-data script tag, found {sheets_tag_count}")
        self.assertEqual(notes_tag_count, 1, f"Expected exactly 1 notes-data script tag, found {notes_tag_count}")
        self.assertIn("GeoGebra", final_html)


class TestAdversarialStressAndConcurrency(unittest.TestCase):
    """Concurrency and advanced edge case stress testing."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox_ctx = SafeIsolatedSandbox()
        cls.srv = cls.sandbox_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.sandbox_ctx.__exit__(None, None, None)

    def test_concurrent_pdf_uploads(self):
        """Verify concurrent PDF uploads are serialized by WRITE_LOCK without race conditions."""
        num_workers = 5
        pdf_bytes = make_valid_pdf_bytes("Concurrent PDF Test")

        def do_upload(worker_id):
            filename = f"concurrent_lecture_{worker_id}.pdf"
            return upload_multipart(self.srv.base_url, filename, pdf_bytes)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            results = list(executor.map(do_upload, range(num_workers)))

        scans_dir = self.srv.workdir / "data" / "scans"
        for status, res in results:
            self.assertEqual(status, 200, f"Concurrent upload failed: {res}")
            self.assertEqual(res.get("status"), "ok")
            saved_fn = res.get("filename")
            self.assertTrue((scans_dir / saved_fn).exists())

    def test_concurrent_dualsync_sheet_saves(self):
        """Verify concurrent Dual-Sync sheet writes maintain HTML consistency and valid JSON."""
        num_sheets = 6

        def do_save_sheet(idx):
            payload = {
                "id": f"sheet_concurrent_{idx}",
                "title": f"Параллельный листок {idx}",
                "category": "олимпиада",
                "desc": f"Формула \\sum_{{k=1}}^{{{idx}}} k^2 = \\frac{{{idx}({idx}+1)(2{idx}+1)}}{{6}}",
                "tasks": [
                    {
                        "num": 1,
                        "title": "Задача 1",
                        "d": 3,
                        "text": f"Докажите тождество для n = {idx}: \\sqrt{{{idx}}}",
                        "answer": f"{idx}",
                    }
                ],
            }
            return post_json_api(self.srv.base_url, "/api/save-sheet", {"sheet": payload})

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_sheets) as executor:
            results = list(executor.map(do_save_sheet, range(num_sheets)))

        for status, res in results:
            self.assertEqual(status, 200, f"Concurrent sheet save failed: {res}")

        html_content = (self.srv.workdir / "index.html").read_text(encoding="utf-8")
        embedded_data = extract_embedded_json(html_content, "sheets-data")

        for idx in range(num_sheets):
            sheet_key = f"sheet_concurrent_{idx}"
            self.assertIn(sheet_key, embedded_data["sheets"], f"Missing {sheet_key} in embedded sheets!")
            task_text = embedded_data["sheets"][sheet_key]["tasks"][0]["text"]
            self.assertIn(f"\\sqrt{{{idx}}}", task_text)

    def test_cyrillic_and_unicode_filename_sanitization(self):
        """Verify filenames with Cyrillic characters and special punctuation are safely sanitized."""
        valid_pdf = make_valid_pdf_bytes("Cyrillic Test")
        scans_dir = self.srv.workdir / "data" / "scans"

        test_filenames = [
            ("Лекция_по_графам_Эйлер.pdf", True),
            ("Calculus (Part 1) [Final] & Notes!.pdf", True),
            ("----____....pdf", True),  # Dots and dashes only -> stem empty after strip -> scan_<ts>.pdf
            ("valid.name.with.many.dots.pdf", True),
        ]

        for fname, should_succeed in test_filenames:
            status, res = upload_multipart(self.srv.base_url, fname, valid_pdf)
            self.assertEqual(status, 200, f"Upload failed for '{fname}': {res}")
            saved_name = res.get("filename")
            self.assertTrue(saved_name.endswith(".pdf"))
            disk_file = scans_dir / saved_name
            self.assertTrue(disk_file.exists())
            self.assertEqual(disk_file.read_bytes(), valid_pdf)

    def test_header_case_and_formatting_variants(self):
        """Verify strict %PDF- header checking rejects case variants and header prefixes."""
        invalid_headers = [
            ("lowercase.pdf", b"%pdf-1.4\nvalid body"),
            ("missing_hyphen.pdf", b"%PDF1.4\nvalid body"),
            ("space_before.pdf", b" %PDF-1.4\nvalid body"),
            ("crlf_before.pdf", b"\r\n%PDF-1.4\nvalid body"),
            ("html_doc.pdf", b"<!DOCTYPE html><html><head></head><body>%PDF-1.4</body></html>"),
        ]
        for fn, payload in invalid_headers:
            status, res = upload_multipart(self.srv.base_url, fn, payload)
            self.assertEqual(
                status, 400,
                f"Expected HTTP 400 for invalid header in '{fn}', got {status}: {res}"
            )
            self.assertEqual(res.get("status"), "error")

    def test_angle_brackets_and_quotes_in_latex(self):
        """Verify angle brackets, quotes, and HTML-like tokens inside LaTeX are not mangled by dual sync."""
        special_latex = r'\langle x, y \rangle < \sqrt{x^2 + y^2} > 0 \text{ and } "quotes" \text{ with \'single\'}'
        sheet_payload = {
            "id": "sheet_brackets_quotes",
            "title": 'Sheet with "Quotes" & <Brackets>',
            "category": "авторская",
            "desc": 'Detailed "description" with <brackets> & symbols',
            "tasks": [
                {
                    "num": 1,
                    "title": "Task 1",
                    "d": 5,
                    "text": special_latex,
                    "answer": r'< \infty >',
                }
            ],
        }

        status, res = post_json_api(self.srv.base_url, "/api/save-sheet", {"sheet": sheet_payload})
        self.assertEqual(status, 200)

        html_content = (self.srv.workdir / "index.html").read_text(encoding="utf-8")
        embedded_data = extract_embedded_json(html_content, "sheets-data")

        retrieved = embedded_data["sheets"]["sheet_brackets_quotes"]
        self.assertEqual(retrieved["title"], sheet_payload["title"])
        self.assertEqual(retrieved["desc"], sheet_payload["desc"])
        self.assertEqual(retrieved["tasks"][0]["text"], special_latex)
        self.assertEqual(retrieved["tasks"][0]["answer"], r'< \infty >')


if __name__ == "__main__":
    unittest.main()
