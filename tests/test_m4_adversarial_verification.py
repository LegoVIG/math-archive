#!/usr/bin/env python3
"""Adversarial stress and integrity verification for Milestone 4.
Author: Reviewer M4 (teamwork_reviewer / critic).
"""

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ARCHIVE_DIR = ROOT_DIR / "math-archive"
INDEX_HTML = ARCHIVE_DIR / "index.html"
BASE_INDEX = ROOT_DIR / "olimp-kombinatorika" / "index.html"
DATA_DIR = ARCHIVE_DIR / "data"

PASSED = 0
FAILED = 0


def record(name: str, passed: bool, detail: str = ""):
    global PASSED, FAILED
    if passed:
        PASSED += 1
        print(f"  [PASS] {name}" + (f" ({detail})" if detail else ""))
    else:
        FAILED += 1
        print(f"  [FAIL] {name}" + (f" ({detail})" if detail else ""))


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_baseline_and_emojis():
    print("\n--- Test Suite 1: Baseline Integrity & Zero Emojis ---")
    # 1. Baseline SHA-256
    raw_base = BASE_INDEX.read_bytes()
    h = hashlib.sha256(raw_base).hexdigest().upper()
    expected = "AC80217ABDB24FDFD02A54C056D31F8DB41FFF1F75AA04F736D9A7506DB50935"
    record("Baseline olimp-kombinatorika/index.html SHA-256 matches", h == expected, f"Hash={h[:16]}...")

    # 2. Closing tags in index.html
    html_text = INDEX_HTML.read_text(encoding="utf-8")
    bc = html_text.count("</body>")
    hc = html_text.count("</html>")
    record("Single closing </body> and </html> tags", bc == 1 and hc == 1, f"body={bc}, html={hc}")

    # 3. Emojis
    emoji_pattern = re.compile(r"[\U0001F300-\U0001FAFF\u2702-\u27B0]")
    all_files = []
    emoji_violations = {}
    for root, _, files in os.walk(ARCHIVE_DIR):
        for f in files:
            if f.endswith((".html", ".js", ".css", ".py", ".json")):
                p = Path(root) / f
                all_files.append(p)
                m = emoji_pattern.findall(p.read_text(encoding="utf-8", errors="ignore"))
                if m:
                    emoji_violations[str(p)] = len(m)
    record("Zero emojis across all project files", len(emoji_violations) == 0, f"checked {len(all_files)} files")


def test_geogebra_component():
    print("\n--- Test Suite 2: GeoGebra Web Applet Architecture ---")
    html_text = INDEX_HTML.read_text(encoding="utf-8")

    # 1. deployggb.js URL
    has_deployggb = "https://www.geogebra.org/apps/deployggb.js" in html_text
    record("GeoGebra script deployggb.js referenced", has_deployggb)

    # 2. GGBApplet params: appName: 'classic', language: 'ru'
    record("appName configured as 'classic'", '"appName": "classic"' in html_text)
    record("Language configured as Russian ('ru')", '"language": "ru"' in html_text)
    record("Toolbar enabled (showToolBar: true)", '"showToolBar": true' in html_text)
    record("Algebra input enabled (showAlgebraInput: true)", '"showAlgebraInput": true' in html_text)

    # 3. Geometry only button rendering
    has_geom_guard = "curSubject === 'geometry' || curSubject === 'geom'" in html_text
    record("GeoGebra button guarded strictly for geometry subject", has_geom_guard)

    # 4. Fallback handler
    has_fallback = "ggb-fallback" in html_text and "ggb-retry" in html_text
    record("GeoGebra offline/network error fallback and retry implemented", has_fallback)

    # 5. Fullscreen support
    has_fs = "requestFullscreen" in html_text and "data-act=\"ggb-fullscreen\"" in html_text
    record("GeoGebra fullscreen toggling implemented", has_fs)

    # 6. DiskSync save on ggb-save
    has_disksync = "window.DiskSync.saveTask" in html_text and "getBase64" in html_text
    record("GeoGebra getBase64() connected to DiskSync.saveTask()", has_disksync)


def test_analysis_subject():
    print("\n--- Test Suite 3: 5th Subject 'Матанализ' Integrity ---")
    # 1. meta.json schema
    meta_path = DATA_DIR / "analysis" / "meta.json"
    record("data/analysis/meta.json exists", meta_path.exists())
    try:
        meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
        valid_schema = (
            meta_data.get("total") == 0
            and meta_data.get("parts") == ["Методы", "Типы задач", "Разделы"]
            and meta_data.get("ch") == []
            and isinstance(meta_data.get("pos"), dict)
        )
        record("data/analysis/meta.json adheres to schema contract", valid_schema, f"total={meta_data.get('total')}")
    except Exception as e:
        record("data/analysis/meta.json adheres to schema contract", False, str(e))

    # 2. index.html script tag
    html_text = INDEX_HTML.read_text(encoding="utf-8")
    has_meta_script = '<script id="meta-analysis" type="application/json">' in html_text
    record("Embedded <script id=\"meta-analysis\"> present in index.html", has_meta_script)

    # 3. subjNavHtml
    has_analysis_nav = (
        "id: 'analysis'" in html_text
        and "slug: 'anal'" in html_text
        and "I.subj_anal" in html_text
    )
    record("5th subject pill present in subjNavHtml()", has_analysis_nav)

    # 4. SUBJECTS map
    has_subj_map = "'analysis':" in html_text and "scriptId: 'meta-analysis'" in html_text
    record("analysis configured in SUBJECTS dictionary", has_subj_map)


def hex_to_hsl(hex_str):
    hex_str = hex_str.lstrip("#")
    r = int(hex_str[0:2], 16) / 255.0
    g = int(hex_str[2:4], 16) / 255.0
    b = int(hex_str[4:6], 16) / 255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        h = s = 0.0
    else:
        d = mx - mn
        s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
        if mx == r:
            h = (g - b) / d + (6.0 if g < b else 0.0)
        elif mx == g:
            h = (b - r) / d + 2.0
        else:
            h = (r - g) / d + 4.0
        h *= 60.0
    return h, s * 100, l * 100


def test_theme_colors():
    print("\n--- Test Suite 4: Monochromatic Gradient & Palette Validation ---")
    html_text = INDEX_HTML.read_text(encoding="utf-8")

    # Dark mode regex
    subjects = {
        "combinatorics": {"expected_hue": (15, 45), "name": "Orange"},
        "algebra": {"expected_hue": (345, 15), "name": "Red"},
        "number-theory": {"expected_hue": (205, 235), "name": "Blue"},
        "geometry": {"expected_hue": (135, 175), "name": "Greenish"},
        "analysis": {"expected_hue": (265, 305), "name": "Violet/Purple"},
    }

    # Extract dark theme rules
    for subj, meta in subjects.items():
        pattern = rf':root\[data-subject="{subj}"\][^{{]*\{{([^}}]+)\}}'
        m = re.search(pattern, html_text)
        if not m:
            # fallback combinatorics
            if subj == "combinatorics":
                pattern = r':root,\s*:root\[data-subject="combinatorics"\][^{{]*\{{([^}}]+)\}}'
                m = re.search(pattern, html_text)
        record(f"Dark theme CSS rules found for {subj}", bool(m))
        if m:
            block = m.group(1)
            p1 = re.search(r'--p1:\s*(#[0-9a-fA-F]{6})', block)
            p2 = re.search(r'--p2:\s*(#[0-9a-fA-F]{6})', block)
            p3 = re.search(r'--p3:\s*(#[0-9a-fA-F]{6})', block)
            record(f"--p1, --p2, --p3 present for {subj}", bool(p1 and p2 and p3))
            if p1 and p2 and p3:
                h1, _, _ = hex_to_hsl(p1.group(1))
                h2, _, _ = hex_to_hsl(p2.group(1))
                h3, _, _ = hex_to_hsl(p3.group(1))

                def in_hue_range(h, exp):
                    if exp[0] > exp[1]:  # Wrap around 360 (Red)
                        return h >= exp[0] or h <= exp[1]
                    return exp[0] <= h <= exp[1]

                m_hues = in_hue_range(h1, meta["expected_hue"]) and in_hue_range(h2, meta["expected_hue"]) and in_hue_range(h3, meta["expected_hue"])
                record(
                    f"Monochromatic {meta['name']} gradation for {subj} (hues: {h1:.1f}, {h2:.1f}, {h3:.1f})",
                    m_hues
                )


def test_server_persistence_end_to_end():
    print("\n--- Test Suite 5: Adversarial E2E Server Persistence (GeoGebra & Analysis) ---")
    port = find_free_port()
    server_script = ARCHIVE_DIR / "server.py"
    import shutil
    py_exe = sys.executable if not sys.executable.lower().endswith(".dll") else (shutil.which("python") or "python")
    proc = subprocess.Popen(
        [py_exe, str(server_script), "--port", str(port)],
        cwd=str(ARCHIVE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(1.0)
    base_url = f"http://127.0.0.1:{port}"

    try:
        # Check status
        with urllib.request.urlopen(f"{base_url}/api/status", timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            record("Server launched and /api/status returns ok", data.get("status") == "ok")

        # 1. Test saving GeoGebra diagram to geometry task
        # First ensure geometry ch1 exists
        ch1_init = {
            "n": 1,
            "intro": [],
            "secs": [
                {
                    "n": 1,
                    "t": "Adversarial GeoGebra Sec",
                    "th": [],
                    "g": [
                        {
                            "t": "Group 1",
                            "p": [
                                {
                                    "id": "1.1.1",
                                    "src": "Reviewer Test",
                                    "st": "<p>GeoGebra Test</p>",
                                    "d": 1,
                                    "sol": {"ans": "42"},
                                    "ggb": None,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        geom_ch1_path = DATA_DIR / "geometry" / "ch1.json"
        geom_ch1_path.parent.mkdir(parents=True, exist_ok=True)
        geom_ch1_path.write_text(json.dumps(ch1_init, ensure_ascii=False), encoding="utf-8")

        # Save base64 string
        fake_b64 = "UEsDBBQAAAAIAI61U1kAAAAAAAAAAAAAAAcAAABnZW8ueG1s7Z1/b9tGEMA/t59ipF+6A5ZFSbZkG7V/..." + "A" * 1000
        task_payload = {
            "domain": "geometry",
            "ch": 1,
            "sec": 1,
            "task": {
                "id": "1.1.1",
                "src": "Reviewer Test",
                "st": "<p>GeoGebra Test</p>",
                "d": 1,
                "sol": {"ans": "42"},
                "ggb": fake_b64,
            },
        }
        req = urllib.request.Request(
            f"{base_url}/api/save-task",
            data=json.dumps(task_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            save_resp = json.loads(resp.read().decode("utf-8"))
            record("POST /api/save-task with base64 ggb succeeded", save_resp.get("status") == "ok")

        # Verify disk contents
        saved_disk = json.loads(geom_ch1_path.read_text(encoding="utf-8"))
        saved_task = saved_disk["secs"][0]["g"][0]["p"][0]
        record("task.ggb correctly persisted to disk without truncation", saved_task.get("ggb") == fake_b64)

        # Verify GET /api/data returns it accurately
        with urllib.request.urlopen(f"{base_url}/api/data?domain=geometry&ch=1", timeout=3.0) as resp:
            data_resp = json.loads(resp.read().decode("utf-8"))
            api_task = data_resp["secs"][0]["g"][0]["p"][0]
            record("GET /api/data?domain=geometry&ch=1 retrieves intact task.ggb", api_task.get("ggb") == fake_b64)

        # 2. Test analysis domain CRUD
        analysis_topic = {
            "domain": "analysis",
            "ch": 1,
            "t": "Пределы функций",
            "s": "Пределы",
            "p": 1,
            "pos": [150, 250],
        }
        req2 = urllib.request.Request(
            f"{base_url}/api/save-topic",
            data=json.dumps(analysis_topic).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=3.0) as resp:
            save_topic_resp = json.loads(resp.read().decode("utf-8"))
            record("POST /api/save-topic on domain 'analysis' succeeded", save_topic_resp.get("status") == "ok")

        analysis_meta_path = DATA_DIR / "analysis" / "meta.json"
        anal_meta = json.loads(analysis_meta_path.read_text(encoding="utf-8"))
        ch_found = any(c.get("n") == 1 for c in anal_meta.get("ch", []))
        record("Topic saved in data/analysis/meta.json", ch_found)
        pos_found = anal_meta.get("pos", {}).get("1") == [150, 250]
        record("Coordinate saved in data/analysis/meta.json", pos_found)

        # Clean up test topic
        del_req = urllib.request.Request(
            f"{base_url}/api/delete-topic",
            data=json.dumps({"domain": "analysis", "ch": 1}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(del_req, timeout=3.0) as resp:
            pass

        # Restore clean templates
        clean_anal_meta = {
            "total": 0,
            "parts": ["Методы", "Типы задач", "Разделы"],
            "ch": [],
            "edges": [],
            "macros": {},
            "preface": "",
            "credit": "",
            "pos": {},
        }
        analysis_meta_path.write_text(json.dumps(clean_anal_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if geom_ch1_path.exists():
            geom_ch1_path.unlink()

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    print("================================================================================")
    print("   MILESTONE 4 ADVERSARIAL STRESS TEST & INTEGRITY VERIFICATION (REVIEWER M4)   ")
    print("================================================================================")
    test_baseline_and_emojis()
    test_geogebra_component()
    test_analysis_subject()
    test_theme_colors()
    test_server_persistence_end_to_end()

    print("\n================================================================================")
    print(f"  TOTAL: {PASSED + FAILED} | PASSED: {PASSED} | FAILED: {FAILED}")
    print("================================================================================")
    if FAILED > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
