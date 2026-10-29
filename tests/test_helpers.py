"""Shared Test Helpers and Fixtures for Math Archive E2E Test Suite.

Provides path resolution, schema validation, emoji detection, embedded JSON parsing,
sandbox environment isolation, and ephemeral server process lifecycle management.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

# Ensure UTF-8 console output on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Paths
TESTS_DIR = Path(__file__).resolve().parent
MATH_ARCHIVE_DIR = TESTS_DIR.parent
ROOT_DIR = MATH_ARCHIVE_DIR.parent
OLIMP_DIR = ROOT_DIR / "olimp-kombinatorika"
DATA_DIR = MATH_ARCHIVE_DIR / "data"
INDEX_HTML_PATH = MATH_ARCHIVE_DIR / "index.html"
SERVER_PY_PATH = MATH_ARCHIVE_DIR / "server.py"

# Domain names
DOMAINS = ["combinatorics", "algebra", "number-theory", "geometry"]

# Palettes mapping
PALETTES = {
    "combinatorics": "#a9d850",  # Lime
    "algebra": "#38bdf8",        # Cyan
    "number-theory": "#c084fc",  # Violet
    "geometry": "#fb923c",       # Amber
}

# Emoji Codepoint ranges to detect
EMOJI_RANGES = [
    (0x1F600, 0x1F64F),  # Emoticons
    (0x1F300, 0x1F5FF),  # Misc Symbols and Pictographs
    (0x1F680, 0x1F6FF),  # Transport and Map
    (0x1F700, 0x1F77F),  # Alchemical Symbols
    (0x1F780, 0x1F7FF),  # Geometric Shapes Extended
    (0x1F800, 0x1F8FF),  # Supplemental Arrows-C
    (0x1F900, 0x1F9FF),  # Supplemental Symbols and Pictographs
    (0x1FA00, 0x1FA6F),  # Chess Symbols
    (0x1FA70, 0x1FAFF),  # Symbols and Pictographs Extended-A
    (0x2600, 0x26FF),    # Misc Symbols (including dingbats)
    (0x2700, 0x27BF),    # Dingbats (includes checkmarks e.g. U+2713, U+2714)
]


def is_emoji_codepoint(cp: int) -> bool:
    """Return True if character codepoint falls in emoji or dingbat range."""
    for start, end in EMOJI_RANGES:
        if start <= cp <= end:
            return True
    return False


def find_emoji_violations(text: str):
    """Return list of (character, hex_codepoint, index) for any emoji violations."""
    violations = []
    for i, ch in enumerate(text):
        cp = ord(ch)
        if is_emoji_codepoint(cp):
            violations.append((ch, f"U+{cp:04X}", i))
    return violations


def find_free_port() -> int:
    """Find a random available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class EmbeddedScriptParser(HTMLParser):
    """HTML parser to extract <script type="application/json" id="..."> contents."""

    def __init__(self):
        super().__init__()
        self.scripts = {}
        self._current_id = None
        self._current_type = None
        self._accumulator = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            attr_dict = {k.lower(): v for k, v in attrs}
            script_type = attr_dict.get("type", "").lower()
            script_id = attr_dict.get("id", "")
            if "application/json" in script_type and script_id:
                self._current_id = script_id
                self._current_type = script_type
                self._accumulator = []

    def handle_data(self, data):
        if self._current_id is not None:
            self._accumulator.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._current_id is not None:
            self.scripts[self._current_id] = "".join(self._accumulator).strip()
            self._current_id = None
            self._current_type = None
            self._accumulator = []


# Alias for DOM inspection
DomInspector = EmbeddedScriptParser


def extract_embedded_json_map(html_content: str):
    """Extract all application/json script tags from HTML string into a dict {id: parsed_json}."""
    parser = EmbeddedScriptParser()
    parser.feed(html_content)
    result = {}
    for script_id, raw_json in parser.scripts.items():
        try:
            result[script_id] = json.loads(raw_json)
        except json.JSONDecodeError as err:
            result[script_id] = {"__parse_error__": str(err), "__raw__": raw_json}
    return result


def extract_embedded_json(html_content: str, script_id: str):
    """Extract and parse a specific JSON script tag by id."""
    scripts = extract_embedded_json_map(html_content)
    return scripts.get(script_id)


def validate_meta_schema(meta_data: dict) -> list:
    """Validate meta.json schema. Returns list of error messages (empty if valid)."""
    errors = []
    if not isinstance(meta_data, dict):
        return ["meta.json must be a JSON object"]

    expected_keys = ["total", "parts", "ch", "edges", "macros", "preface", "credit", "pos"]
    for k in expected_keys:
        if k not in meta_data:
            errors.append(f"Missing required key '{k}' in meta.json")

    if "total" in meta_data and not isinstance(meta_data["total"], int):
        errors.append("Field 'total' must be an integer")

    if "parts" in meta_data and not isinstance(meta_data["parts"], list):
        errors.append("Field 'parts' must be a list of strings")

    if "ch" in meta_data:
        if not isinstance(meta_data["ch"], list):
            errors.append("Field 'ch' must be a list of topic objects")
        else:
            for idx, ch in enumerate(meta_data["ch"]):
                if not isinstance(ch, dict):
                    errors.append(f"Topic ch[{idx}] must be an object")
                    continue
                for req in ["n", "t", "s", "p", "secs"]:
                    if req not in ch:
                        errors.append(f"Topic ch[{idx}] missing '{req}'")
                if "secs" in ch and isinstance(ch["secs"], list):
                    for s_idx, sec in enumerate(ch["secs"]):
                        if not isinstance(sec, dict):
                            errors.append(f"Topic ch[{idx}].secs[{s_idx}] must be an object")
                            continue
                        for s_req in ["n", "t", "ids", "d"]:
                            if s_req not in sec:
                                errors.append(f"Topic ch[{idx}].secs[{s_idx}] missing '{s_req}'")

    if "pos" in meta_data and not isinstance(meta_data["pos"], dict):
        errors.append("Field 'pos' must be an object mapping topic id to [x, y]")

    return errors


def validate_ch_schema(ch_data: dict) -> list:
    """Validate ch{n}.json schema. Returns list of error messages (empty if valid)."""
    errors = []
    if not isinstance(ch_data, dict):
        return ["ch{n}.json must be a JSON object"]

    for req in ["n", "intro", "secs"]:
        if req not in ch_data:
            errors.append(f"Missing required key '{req}' in ch{n}.json")

    if "intro" in ch_data and not isinstance(ch_data["intro"], list):
        errors.append("Field 'intro' must be a list of objects")

    if "secs" in ch_data:
        if not isinstance(ch_data["secs"], list):
            errors.append("Field 'secs' must be a list of section objects")
        else:
            for s_idx, sec in enumerate(ch_data["secs"]):
                if not isinstance(sec, dict):
                    errors.append(f"sec[{s_idx}] must be an object")
                    continue
                for s_req in ["n", "t", "th", "g"]:
                    if s_req not in sec:
                        errors.append(f"sec[{s_idx}] missing '{s_req}'")
                if "g" in sec and isinstance(sec["g"], list):
                    for g_idx, grp in enumerate(sec["g"]):
                        if not isinstance(grp, dict):
                            errors.append(f"sec[{s_idx}].g[{g_idx}] must be an object")
                            continue
                        if "p" in grp and isinstance(grp["p"], list):
                            for p_idx, p in enumerate(grp["p"]):
                                if not isinstance(p, dict):
                                    errors.append(f"Problem at sec[{s_idx}].g[{g_idx}].p[{p_idx}] must be object")
                                    continue
                                for p_req in ["id", "src", "st", "d", "sol"]:
                                    if p_req not in p:
                                        errors.append(f"Problem {p.get('id', p_idx)} missing '{p_req}'")
                                if "sol" in p and isinstance(p["sol"], dict):
                                    for sol_req in ["ans", "idea", "plan"]:
                                        if sol_req not in p["sol"]:
                                            errors.append(f"Problem {p.get('id', p_idx)}.sol missing '{sol_req}'")
    return errors


def _handle_remove_readonly(func, path, exc):
    """Clear Windows read-only/ACL flags when removing temporary sandboxes."""
    import stat
    excvalue = exc[1]
    if func in (os.rmdir, os.remove, os.unlink) and getattr(excvalue, "errno", None) == 13:
        try:
            os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
            func(path)
        except Exception:
            pass


class SandboxEnvironment:
    """Isolated temporary directory mirroring math-archive for safe mutating tests."""

    def __init__(self):
        self.temp_dir = tempfile.mkdtemp(prefix="math_archive_sandbox_")
        self.path = Path(self.temp_dir)
        self.math_archive_path = self.path / "math-archive"
        self.data_path = self.math_archive_path / "data"

    def __enter__(self):
        # Copy math-archive into sandbox if it exists
        if MATH_ARCHIVE_DIR.exists():
            shutil.copytree(MATH_ARCHIVE_DIR, self.math_archive_path, ignore=shutil.ignore_patterns("tests"))
        else:
            self.math_archive_path.mkdir(parents=True, exist_ok=True)
            self.data_path.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            shutil.rmtree(self.temp_dir, onerror=_handle_remove_readonly)
        except Exception:
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception:
                pass


class IsolatedDataSandbox:
    """Isolated test sandbox providing an ephemeral ServerInstance.

    Clones math-archive into a temporary sandbox and launches server.py
    within it. All mutating API calls (POST/PUT/DELETE) and dual-sync writes
    occur strictly inside the temporary directory. The sandbox and server
    process are cleanly dismantled upon context exit, preventing workspace pollution.
    """

    def __init__(self, port: int = None):
        self.sandbox = SandboxEnvironment()
        self.port = port
        self.server = None

    def __enter__(self) -> "ServerInstance":
        self.sandbox.__enter__()
        self.server = ServerInstance(workdir=self.sandbox.math_archive_path, port=self.port)
        if not self.server.start():
            self.sandbox.__exit__(None, None, None)
            raise RuntimeError(f"Failed to start server in IsolatedDataSandbox on port {self.server.port}")
        return self.server

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.server:
            try:
                self.server.stop()
            except Exception:
                pass
        self.sandbox.__exit__(exc_type, exc_val, exc_tb)


class ServerInstance:
    """Manages background lifecycle of server.py on an ephemeral port."""

    def __init__(self, workdir: Path = None, port: int = None):
        self.workdir = workdir or MATH_ARCHIVE_DIR
        self.port = port or find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process = None

    def start(self, timeout: float = 6.0) -> bool:
        """Start server.py and wait until /api/status responds."""
        server_script = self.workdir / "server.py"
        if not server_script.exists():
            return False

        py_exe = sys.executable
        if py_exe.lower().endswith(".dll"):
            import shutil
            py_exe = shutil.which("python") or shutil.which("python3") or "python"
        cmd = [py_exe, str(server_script), "--port", str(self.port)]
        self.process = subprocess.Popen(
            cmd,
            cwd=str(self.workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait for server to respond
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.process.poll() is not None:
                # Process exited early
                return False
            try:
                res = self.get("/api/status")
                if res.get("status") == "ok":
                    return True
            except Exception:
                time.sleep(0.15)
        return False

    def stop(self):
        """Terminate server process."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

    def get(self, endpoint: str):
        """Perform HTTP GET request and return parsed JSON."""
        url = urllib.parse.urljoin(self.base_url, endpoint)
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)

    def post(self, endpoint: str, payload: dict):
        """Perform HTTP POST request with JSON payload and return parsed JSON."""
        url = urllib.parse.urljoin(self.base_url, endpoint)
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
