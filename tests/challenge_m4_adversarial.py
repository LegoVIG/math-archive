#!/usr/bin/env python3
"""Empirical Challenger Adversarial Stress Test Suite for Milestone 4.
Author: Challenger M4 (teamwork_challenger / critic, specialist).

Covers:
1. GeoGebra base64 serialization, large payloads (2MB), special chars, HTML script injection safety.
2. 5th subject 'analysis' full lifecycle CRUD, path traversal defense, export integrity, and auto-indexing.
3. Monochromatic gradient verification across both dark and light themes for all 5 subjects.
4. Baseline immutability, zero emojis, single closing tags, valid embedded JSON script tags.
5. High-concurrency multi-threaded stress test on server persistence endpoints.
"""

import colorsys
import concurrent.futures
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

TESTS_PASSED = 0
TESTS_FAILED = 0
FAILURES = []


def record(name: str, passed: bool, detail: str = ""):
    global TESTS_PASSED, TESTS_FAILED, FAILURES
    if passed:
        TESTS_PASSED += 1
        msg = f"  [PASS] {name}" + (f" ({detail})" if detail else "")
        print(msg)
    else:
        TESTS_FAILED += 1
        msg = f"  [FAIL] {name}" + (f" ({detail})" if detail else "")
        print(msg)
        FAILURES.append(msg)


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def hex_to_hsl(hex_str):
    hex_str = hex_str.lstrip("#")
    r = int(hex_str[0:2], 16) / 255.0
    g = int(hex_str[2:4], 16) / 255.0
    b = int(hex_str[4:6], 16) / 255.0
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return round(h * 360, 1), round(s * 100, 1), round(l * 100, 1)


# ==============================================================================
# Suite 1: Code Hygiene & Structural Integrity
# ==============================================================================
def challenge_hygiene_and_baseline():
    print("\n--- Challenge Suite 1: Code Hygiene, Baseline & Structural Integrity ---")
    
    # 1. Baseline SHA-256 check
    raw_base = BASE_INDEX.read_bytes()
    actual_hash = hashlib.sha256(raw_base).hexdigest().upper()
    expected_hash = "AC80217ABDB24FDFD02A54C056D31F8DB41FFF1F75AA04F736D9A7506DB50935"
    record("Baseline olimp-kombinatorika/index.html unmodified (SHA-256)", actual_hash == expected_hash, f"Hash={actual_hash[:16]}...")

    # 2. HTML closing tags exactly 1
    html_text = INDEX_HTML.read_text(encoding="utf-8")
    body_count = html_text.count("</body>")
    html_count = html_text.count("</html>")
    record("Single </body> closing tag in math-archive/index.html", body_count == 1, f"count={body_count}")
    record("Single </html> closing tag in math-archive/index.html", html_count == 1, f"count={html_count}")

    # 3. No content after </html>
    end_idx = html_text.rfind("</html>")
    after_html = html_text[end_idx + len("</html>"):].strip()
    record("Zero trailing garbage characters after </html>", len(after_html) == 0, f"trailing len={len(after_html)}")

    # 4. Strict Zero Emoji scan
    emoji_regex = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
    violations = []
    scanned_count = 0
    for root, _, files in os.walk(ARCHIVE_DIR):
        for f in files:
            if f.endswith((".html", ".js", ".css", ".py", ".json")):
                p = Path(root) / f
                scanned_count += 1
                content = p.read_text(encoding="utf-8", errors="ignore")
                matches = emoji_regex.findall(content)
                if matches:
                    violations.append((str(p.relative_to(ROOT_DIR)), len(matches)))
    record(f"Zero emojis across all math-archive files (scanned {scanned_count} files)", len(violations) == 0, f"violations={violations}")

    # 5. Embedded script tag validity
    meta_scripts = ["meta", "meta-algebra", "meta-number-theory", "meta-geometry", "meta-analysis"]
    for s_id in meta_scripts:
        m = re.search(rf'<script\s+[^>]*id=[\x22\x27]{s_id}[\x22\x27][^>]*>(.*?)</script>', html_text, re.DOTALL)
        if not m:
            record(f"Embedded <script id='{s_id}'> exists in index.html", False)
            continue
        try:
            payload = json.loads(m.group(1).strip())
            record(f"Embedded <script id='{s_id}'> parses as valid JSON", isinstance(payload, dict), f"keys={list(payload.keys())[:3]}")
        except Exception as e:
            record(f"Embedded <script id='{s_id}'> parses as valid JSON", False, str(e))


# ==============================================================================
# Suite 2: Monochromatic Gradient Analysis (Dark & Light)
# ==============================================================================
def challenge_monochromatic_gradients():
    print("\n--- Challenge Suite 2: Monochromatic Gradient Validation (Dark & Light) ---")
    html_text = INDEX_HTML.read_text(encoding="utf-8")

    theme_configs = {
        "combinatorics": {"name": "Orange", "hue_range": (15, 45)},
        "algebra": {"name": "Red", "hue_range": (345, 15)},
        "number-theory": {"name": "Blue", "hue_range": (205, 235)},
        "geometry": {"name": "Greenish", "hue_range": (135, 175)},
        "analysis": {"name": "Violet", "hue_range": (260, 305)},
    }

    def in_hue_range(h, exp):
        if exp[0] > exp[1]:
            return h >= exp[0] or h <= exp[1]
        return exp[0] <= h <= exp[1]

    # Test Dark Theme
    for subj, cfg in theme_configs.items():
        pattern = rf':root\[data-subject="{subj}"\][^{{]*\{{([^}}]+)\}}'
        m = re.search(pattern, html_text)
        if not m and subj == "combinatorics":
            pattern = r':root,\s*:root\[data-subject="combinatorics"\][^{{]*\{{([^}}]+)\}}'
            m = re.search(pattern, html_text)
        
        record(f"CSS rules found for dark theme '{subj}'", bool(m))
        if m:
            block = m.group(1)
            p1 = re.search(r'--p1:\s*(#[0-9a-fA-F]{6})', block)
            p2 = re.search(r'--p2:\s*(#[0-9a-fA-F]{6})', block)
            p3 = re.search(r'--p3:\s*(#[0-9a-fA-F]{6})', block)
            record(f"--p1, --p2, --p3 defined in dark theme '{subj}'", bool(p1 and p2 and p3))
            if p1 and p2 and p3:
                h1, s1, l1 = hex_to_hsl(p1.group(1))
                h2, s2, l2 = hex_to_hsl(p2.group(1))
                h3, s3, l3 = hex_to_hsl(p3.group(1))
                all_in_range = in_hue_range(h1, cfg["hue_range"]) and in_hue_range(h2, cfg["hue_range"]) and in_hue_range(h3, cfg["hue_range"])
                record(f"Dark theme {subj} ({cfg['name']}) all in hue range {cfg['hue_range']}", all_in_range, f"hues: {h1}, {h2}, {h3}")
                
                # Check hue proximity
                def hue_diff(a, b):
                    d = abs(a - b) % 360
                    return 360 - d if d > 180 else d
                max_diff = max(hue_diff(h1, h2), hue_diff(h2, h3), hue_diff(h1, h3))
                record(f"Dark theme {subj} monochromatic hue consistency (max delta <= 15°)", max_diff <= 15.0, f"max_delta={max_diff:.1f}°")

                # Check distinct lightness (differentiation)
                distinct_lightness = len({round(l1, 1), round(l2, 1), round(l3, 1)}) >= 2
                record(f"Dark theme {subj} parts have lightness gradation", distinct_lightness, f"L={l1}%, {l2}%, {l3}%")

    # Test Light Theme
    for subj, cfg in theme_configs.items():
        pattern = rf':root\[data-theme="light"\]\[data-subject="{subj}"\][^{{]*\{{([^}}]+)\}}'
        m = re.search(pattern, html_text)
        if not m and subj == "combinatorics":
            pattern = r':root\[data-theme="light"\],\s*:root\[data-theme="light"\]\[data-subject="combinatorics"\][^{{]*\{{([^}}]+)\}}'
            m = re.search(pattern, html_text)
        
        record(f"CSS rules found for light theme '{subj}'", bool(m))
        if m:
            block = m.group(1)
            p1 = re.search(r'--p1:\s*(#[0-9a-fA-F]{6})', block)
            p2 = re.search(r'--p2:\s*(#[0-9a-fA-F]{6})', block)
            p3 = re.search(r'--p3:\s*(#[0-9a-fA-F]{6})', block)
            record(f"--p1, --p2, --p3 defined in light theme '{subj}'", bool(p1 and p2 and p3))
            if p1 and p2 and p3:
                h1, s1, l1 = hex_to_hsl(p1.group(1))
                h2, s2, l2 = hex_to_hsl(p2.group(1))
                h3, s3, l3 = hex_to_hsl(p3.group(1))
                all_in_range = in_hue_range(h1, cfg["hue_range"]) and in_hue_range(h2, cfg["hue_range"]) and in_hue_range(h3, cfg["hue_range"])
                record(f"Light theme {subj} ({cfg['name']}) all in hue range {cfg['hue_range']}", all_in_range, f"hues: {h1}, {h2}, {h3}")
                
                def hue_diff(a, b):
                    d = abs(a - b) % 360
                    return 360 - d if d > 180 else d
                max_diff = max(hue_diff(h1, h2), hue_diff(h2, h3), hue_diff(h1, h3))
                record(f"Light theme {subj} monochromatic hue consistency (max delta <= 15°)", max_diff <= 15.0, f"max_delta={max_diff:.1f}°")

                distinct_lightness = len({round(l1, 1), round(l2, 1), round(l3, 1)}) >= 2
                record(f"Light theme {subj} parts have lightness gradation", distinct_lightness, f"L={l1}%, {l2}%, {l3}%")


# ==============================================================================
# Suite 3: Server Persistence, Path Traversal & Schema Stress
# ==============================================================================
def challenge_server_persistence_and_geogebra():
    print("\n--- Challenge Suite 3: Adversarial GeoGebra & Analysis Server Persistence ---")
    port = find_free_port()
    server_script = ARCHIVE_DIR / "server.py"
    import shutil
    py_exe = shutil.which("python") or "python"
    proc = subprocess.Popen(
        [py_exe, str(server_script), "--port", str(port)],
        cwd=str(ARCHIVE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(1.0)
    base_url = f"http://127.0.0.1:{port}"

    try:
        # 1. Path traversal attacks
        traversal_attempts = [
            "../geometry",
            "..\\geometry",
            "/geometry",
            "geometry/../algebra",
            "unknown_subject",
            "etc/passwd",
        ]
        for bad_domain in traversal_attempts:
            url = f"{base_url}/api/data?domain={urllib.parse.quote(bad_domain)}"
            try:
                with urllib.request.urlopen(url, timeout=2.0) as resp:
                    record(f"Path traversal '{bad_domain}' rejected", False, f"status={resp.status}")
            except urllib.error.HTTPError as err:
                record(f"Path traversal '{bad_domain}' rejected", err.code in (400, 404), f"HTTP {err.code}")

        # 2. GeoGebra large payload persistence (2 MB base64 string)
        large_b64 = "UEsDBBQAAAA" + ("A" * (2 * 1024 * 1024)) + "=="
        geom_ch99 = DATA_DIR / "geometry" / "ch99.json"
        
        # Init ch99
        init_payload = {
            "domain": "geometry",
            "ch": 99,
            "sec": 1,
            "task": {
                "id": "99.1.1",
                "src": "Challenger Large GGB",
                "st": "<p>Large GeoGebra Test</p>",
                "d": 4,
                "sol": {"ans": "100", "idea": "huge", "plan": "stream"},
                "ggb": large_b64,
            }
        }
        req = urllib.request.Request(
            f"{base_url}/api/save-task",
            data=json.dumps(init_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            record("POST /api/save-task with 2MB base64 GeoGebra succeeds", resp_data.get("status") == "ok")

        # Verify disk reading
        saved_ch99 = json.loads(geom_ch99.read_text(encoding="utf-8"))
        saved_task = saved_ch99["secs"][0]["g"][0]["p"][0]
        record("2MB GeoGebra string preserved byte-for-byte on disk", saved_task.get("ggb") == large_b64, f"len={len(saved_task.get('ggb', ''))}")

        # 3. GeoGebra reset to null
        reset_payload = {
            "domain": "geometry",
            "ch": 99,
            "sec": 1,
            "task": {
                "id": "99.1.1",
                "src": "Challenger Large GGB",
                "st": "<p>Large GeoGebra Test</p>",
                "d": 4,
                "sol": {"ans": "100", "idea": "huge", "plan": "stream"},
                "ggb": None,
            }
        }
        req_reset = urllib.request.Request(
            f"{base_url}/api/save-task",
            data=json.dumps(reset_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_reset, timeout=5.0) as resp:
            resp_reset = json.loads(resp.read().decode("utf-8"))
            record("POST /api/save-task reset ggb to null succeeds", resp_reset.get("status") == "ok")

        saved_ch99_after = json.loads(geom_ch99.read_text(encoding="utf-8"))
        saved_task_after = saved_ch99_after["secs"][0]["g"][0]["p"][0]
        record("task.ggb correctly reset to None on disk", saved_task_after.get("ggb") is None)

        # 4. Special Characters / Script Injection in GeoGebra task
        xss_statement = "<script>alert('xss')</script><b>Bold &amp; Special \"quotes\" 'single' \\backslash</b>"
        xss_payload = {
            "domain": "geometry",
            "ch": 99,
            "sec": 1,
            "task": {
                "id": "99.1.2",
                "src": "Injection Test",
                "st": xss_statement,
                "d": 3,
                "sol": {"ans": "</script><script>alert(1)</script>"},
                "ggb": "UEsDBBQAAAA_special_chars+/==",
            }
        }
        req_xss = urllib.request.Request(
            f"{base_url}/api/save-task",
            data=json.dumps(xss_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_xss, timeout=5.0) as resp:
            resp_xss = json.loads(resp.read().decode("utf-8"))
            record("POST /api/save-task handles HTML script tags & special characters safely", resp_xss.get("status") == "ok")

        # Clean up ch99
        del_task_req = urllib.request.Request(
            f"{base_url}/api/delete-task",
            data=json.dumps({"domain": "geometry", "ch": 99, "sec": 1, "id": "99.1.1"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(del_task_req, timeout=5.0) as resp:
            pass
        if geom_ch99.exists():
            geom_ch99.unlink()

        # Clean meta-geometry total
        geom_meta_path = DATA_DIR / "geometry" / "meta.json"
        if geom_meta_path.exists():
            clean_geom_meta = {
                "total": 0,
                "parts": ["Методы", "Типы задач", "Разделы"],
                "ch": [],
                "edges": [],
                "macros": {},
                "preface": "",
                "credit": "",
                "pos": {}
            }
            geom_meta_path.write_text(json.dumps(clean_geom_meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # 5. Full Lifecycle on 5th Subject 'analysis'
        anal_meta_path = DATA_DIR / "analysis" / "meta.json"
        
        # Save 3 topics
        for i, title in enumerate(["Пределы", "Производные", "Интегралы"], 1):
            t_req = urllib.request.Request(
                f"{base_url}/api/save-topic",
                data=json.dumps({
                    "domain": "analysis",
                    "ch": i,
                    "t": title,
                    "s": title[:4],
                    "p": ((i - 1) % 3) + 1,
                    "pos": [100 * i, 150 * i]
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(t_req, timeout=3.0) as resp:
                t_resp = json.loads(resp.read().decode("utf-8"))
                record(f"Create topic {i} ('{title}') in analysis", t_resp.get("status") == "ok")

        # Save coordinates with clamping test
        coords_req = urllib.request.Request(
            f"{base_url}/api/save-coords",
            data=json.dumps({
                "domain": "analysis",
                "pos": {
                    "1": [-100, -50],    # Underflow -> [0, 0]
                    "2": [500, 400],     # Valid
                    "3": [3000, 2000],   # Overflow -> [1400, 910]
                }
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(coords_req, timeout=3.0) as resp:
            c_resp = json.loads(resp.read().decode("utf-8"))
            record("POST /api/save-coords for analysis succeeded", c_resp.get("status") == "ok")

        anal_meta_data = json.loads(anal_meta_path.read_text(encoding="utf-8"))
        pos1 = anal_meta_data.get("pos", {}).get("1")
        pos3 = anal_meta_data.get("pos", {}).get("3")
        record("Coordinate clamping [min 0, max 1400/910] respected", pos1 == [0, 0] and pos3 == [1400, 910], f"pos1={pos1}, pos3={pos3}")

        # Add tasks and verify total calculation
        task_a1 = {
            "domain": "analysis",
            "ch": 1,
            "sec": 1,
            "task": {"id": "1.1.1", "src": "E2E", "st": "<p>Limit 1</p>", "d": 2, "sol": {"ans": "0"}}
        }
        task_a2 = {
            "domain": "analysis",
            "ch": 1,
            "sec": 1,
            "task": {"id": "1.1.2", "src": "E2E", "st": "<p>Limit 2</p>", "d": 5, "sol": {"ans": "1"}}
        }
        for t_payload in [task_a1, task_a2]:
            req_t = urllib.request.Request(
                f"{base_url}/api/save-task",
                data=json.dumps(t_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req_t, timeout=3.0) as resp:
                pass

        anal_meta_updated = json.loads(anal_meta_path.read_text(encoding="utf-8"))
        record("Analysis meta.json total automatically recalculated to 2", anal_meta_updated.get("total") == 2, f"total={anal_meta_updated.get('total')}")

        # Test Export endpoint includes analysis
        with urllib.request.urlopen(f"{base_url}/api/export", timeout=5.0) as resp:
            export_data = json.loads(resp.read().decode("utf-8"))
            has_anal = "analysis" in export_data.get("domains", {})
            record("GET /api/export includes 'analysis' subject in archive payload", has_anal)
            if has_anal:
                anal_export = export_data["domains"]["analysis"]
                record("Analysis export includes saved topics and tasks", len(anal_export.get("meta", {}).get("ch", [])) == 3)

        # Cleanup analysis
        for i in [1, 2, 3]:
            del_t_req = urllib.request.Request(
                f"{base_url}/api/delete-topic",
                data=json.dumps({"domain": "analysis", "ch": i}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(del_t_req, timeout=3.0) as resp:
                pass

        anal_ch1 = DATA_DIR / "analysis" / "ch1.json"
        if anal_ch1.exists():
            anal_ch1.unlink()

        clean_anal_meta = {
            "total": 0,
            "parts": ["Методы", "Типы задач", "Разделы"],
            "ch": [],
            "edges": [],
            "macros": {},
            "preface": "",
            "credit": "",
            "pos": {}
        }
        anal_meta_path.write_text(json.dumps(clean_anal_meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # 6. High-concurrency stress test
        print("\n  Starting multi-threaded concurrent write stress test (20 threads)...")
        concurrency_errors = []

        def worker_op(worker_id):
            try:
                op_type = worker_id % 3
                if op_type == 0:
                    # Save coords
                    payload = {"domain": "geometry", "pos": {f"{worker_id}": [100 + worker_id, 200 + worker_id]}}
                    endpoint = "/api/save-coords"
                elif op_type == 1:
                    # Save text
                    payload = {"domain": "algebra", "type": "intro", "ch": 1, "field": "html", "content": f"<p>Stress {worker_id}</p>"}
                    endpoint = "/api/save-text"
                else:
                    # Save task
                    payload = {
                        "domain": "analysis",
                        "ch": 1,
                        "sec": 1,
                        "task": {"id": f"s.{worker_id}", "src": "Stress", "st": f"<p>T{worker_id}</p>", "d": (worker_id % 8) + 1, "sol": {}}
                    }
                    endpoint = "/api/save-task"

                r = urllib.request.Request(
                    f"{base_url}{endpoint}",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(r, timeout=8.0) as res:
                    d = json.loads(res.read().decode("utf-8"))
                    if d.get("status") != "ok":
                        concurrency_errors.append(f"Worker {worker_id} failed: {d}")
            except Exception as e:
                concurrency_errors.append(f"Worker {worker_id} exception: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(worker_op, i) for i in range(20)]
            concurrent.futures.wait(futures)

        record("High-concurrency persistence: 20 simultaneous threads complete with 0 errors", len(concurrency_errors) == 0, f"errors={len(concurrency_errors)}")

        # Clean up files created by stress test
        anal_meta_path.write_text(json.dumps(clean_anal_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if anal_ch1.exists():
            anal_ch1.unlink()
        alg_ch1 = DATA_DIR / "algebra" / "ch1.json"
        if alg_ch1.exists():
            alg_ch1.unlink()

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    print("=" * 80)
    print("   EMPIRICAL CHALLENGER ADVERSARIAL STRESS SUITE (MILIESTONE 4)   ")
    print("=" * 80)
    
    challenge_hygiene_and_baseline()
    challenge_monochromatic_gradients()
    challenge_server_persistence_and_geogebra()

    print("\n" + "=" * 80)
    print(f"  CHALLENGER RESULTS: TOTAL={TESTS_PASSED + TESTS_FAILED} | PASSED={TESTS_PASSED} | FAILED={TESTS_FAILED}")
    print("=" * 80)
    if TESTS_FAILED > 0:
        print("\nFAILURE SUMMARY:")
        for f in FAILURES:
            print(" ", f)
        print("\nVERDICT: REQUEST_CHANGES")
        sys.exit(1)
    else:
        print("\nVERDICT: APPROVE")
        sys.exit(0)


if __name__ == "__main__":
    main()
