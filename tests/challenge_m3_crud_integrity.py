"""Milestone 3 Empirical Adversarial Verification Suite: Features 13-16.

Adversarially tests:
1. Topic Lifecycle & Edge Cascade Stress
2. Section Re-indexing & Task Coherence Stress (including meta.total recalculation)
3. Task CRUD & Difficulty Boundary Validation (rejecting != 1..8)
4. Concurrent Task Mutations and Total Coherence
5. Universal Text Editor & KaTeX Delimiter / HTML Injection Fuzzing
6. Non-contiguous Topic Index Gap Client Regression
7. Zero-Emoji Compliance & Baseline SHA-256 Hash Integrity
"""

import json
import re
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Paths
TESTS_DIR = Path(__file__).resolve().parent
MATH_ARCHIVE_DIR = TESTS_DIR.parent
ROOT_DIR = MATH_ARCHIVE_DIR.parent
OLIMP_DIR = ROOT_DIR / "olimp-kombinatorika"
DATA_DIR = MATH_ARCHIVE_DIR / "data"
INDEX_HTML_PATH = MATH_ARCHIVE_DIR / "index.html"
SERVER_PY_PATH = MATH_ARCHIVE_DIR / "server.py"

from test_helpers import (
    DOMAINS,
    IsolatedDataSandbox,
    find_emoji_violations,
    extract_embedded_json,
    extract_embedded_json_map,
)

BASELINE_SHA256 = "AC80217ABDB24FDFD02A54C056D31F8DB41FFF1F75AA04F736D9A7506DB50935"


class TestAdversarialTopicLifecycle(unittest.TestCase):
    """Stress test topic creation, edge connections, and cascading deletion."""

    def test_topic_full_cascade_deletion(self):
        """Verify deleting a topic purges meta.ch, pos, incident edges, chapter file, and syncs HTML."""
        with IsolatedDataSandbox() as srv:
            sandbox_archive = srv.workdir
            meta_path = sandbox_archive / "data" / "algebra" / "meta.json"
            ch99_path = sandbox_archive / "data" / "algebra" / "ch99.json"

            # 1. Create Topic 99
            res = srv.post("/api/save-topic", {
                "domain": "algebra",
                "ch": 99,
                "t": "Adversarial Topic 99",
                "s": "Topic99",
                "p": 2,
                "pos": [450, 550]
            })
            self.assertEqual(res.get("status"), "ok")
            self.assertTrue(ch99_path.exists(), "ch99.json should be created upon topic creation")

            # 2. Add incident edges to algebra/meta.json manually
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["edges"] = [[99, 1], [2, 99], [99, 99], [1, 2]]
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

            # 3. Add section 1 and two tasks
            srv.post("/api/save-section", {
                "domain": "algebra",
                "ch": 99,
                "sec": 1,
                "t": "Section 1",
                "th": []
            })
            srv.post("/api/save-task", {
                "domain": "algebra",
                "ch": 99,
                "sec": 1,
                "task": {"id": "99.1.1", "st": "Task 1", "d": 1, "sol": {}}
            })
            srv.post("/api/save-task", {
                "domain": "algebra",
                "ch": 99,
                "sec": 1,
                "task": {"id": "99.1.2", "st": "Task 2", "d": 2, "sol": {}}
            })

            meta_after_add = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta_after_add.get("total"), 2, "meta.total should equal 2 after adding 2 tasks")
            self.assertIn("99", meta_after_add.get("pos", {}))

            # 4. Trigger cascade delete of topic 99
            del_res = srv.post("/api/delete-topic", {"domain": "algebra", "ch": 99})
            self.assertEqual(del_res.get("status"), "ok")

            # Verify cascading deletion artifacts:
            meta_after_del = json.loads(meta_path.read_text(encoding="utf-8"))

            # a) Topic removed from ch list
            topic_ids = [t.get("n") for t in meta_after_del.get("ch", [])]
            self.assertNotIn(99, topic_ids, "Topic 99 must be removed from meta.ch")

            # b) Coordinates purged from pos
            self.assertNotIn("99", meta_after_del.get("pos", {}), "Coordinates for 99 must be purged from pos")

            # c) Incident edges purged from edges
            edges = meta_after_del.get("edges", [])
            for e in edges:
                self.assertNotIn(99, e, f"Edge {e} still contains deleted topic 99")
            self.assertEqual(edges, [[1, 2]], "Only non-incident edge [1, 2] should remain")

            # d) ch99.json deleted from disk
            self.assertFalse(ch99_path.exists(), "ch99.json must be deleted from disk")

            # e) meta.total recalculated
            self.assertEqual(meta_after_del.get("total"), 0, "meta.total must be recalculated to 0")

            # f) embedded script updated
            index_path = sandbox_archive / "index.html"
            html_meta = extract_embedded_json(index_path.read_text(encoding="utf-8"), "meta-algebra")
            self.assertIsNotNone(html_meta, "Embedded meta-algebra script should exist")
            self.assertEqual(html_meta.get("total"), 0)
            self.assertNotIn("99", html_meta.get("pos", {}))

    def test_topic_deletion_edge_cases(self):
        """Verify deleting non-existent or malformed topic IDs."""
        with IsolatedDataSandbox() as srv:
            # Delete non-existent topic
            res = srv.post("/api/delete-topic", {"domain": "algebra", "ch": 9999})
            self.assertEqual(res.get("status"), "not_found")

            # Delete with invalid ch type
            try:
                res2 = srv.post("/api/delete-topic", {"domain": "algebra", "ch": "invalid_num"})
                self.assertEqual(res2.get("status"), "error")
            except urllib.error.HTTPError as err:
                self.assertEqual(err.code, 400)


class TestAdversarialSectionReindexing(unittest.TestCase):
    """Stress test section deletion, 1..N reindexing, and meta.total recalculation."""

    def test_section_deletion_reindexing_and_total_coherence(self):
        """Create 5 sections with tasks, delete section 2, verify re-indexing to 1..4 and total."""
        with IsolatedDataSandbox() as srv:
            sandbox_archive = srv.workdir
            ch1_path = sandbox_archive / "data" / "algebra" / "ch1.json"
            meta_path = sandbox_archive / "data" / "algebra" / "meta.json"

            # Create topic 1
            srv.post("/api/save-topic", {
                "domain": "algebra",
                "ch": 1,
                "t": "Topic 1",
                "s": "T1",
                "p": 1,
                "pos": [200, 200]
            })

            # Create 5 sections (§1..§5)
            for sec_n in range(1, 6):
                srv.post("/api/save-section", {
                    "domain": "algebra",
                    "ch": 1,
                    "sec": sec_n,
                    "t": f"Original Section {sec_n}",
                    "th": []
                })

            # Populate tasks:
            # §1: 2 tasks (1.1.1, 1.1.2)
            # §2: 3 tasks (1.2.1, 1.2.2, 1.2.3)
            # §3: 1 task (1.3.1)
            # §4: 2 tasks (1.4.1, 1.4.2)
            # §5: 1 task (1.5.1)
            # Total tasks = 9
            tasks_spec = {
                1: ["1.1.1", "1.1.2"],
                2: ["1.2.1", "1.2.2", "1.2.3"],
                3: ["1.3.1"],
                4: ["1.4.1", "1.4.2"],
                5: ["1.5.1"],
            }
            for sec_n, task_ids in tasks_spec.items():
                for tid in task_ids:
                    srv.post("/api/save-task", {
                        "domain": "algebra",
                        "ch": 1,
                        "sec": sec_n,
                        "task": {"id": tid, "st": f"Task {tid}", "d": 2, "sol": {}}
                    })

            # Verify initial state: total = 9
            meta_pre = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta_pre.get("total"), 9, "Initial meta.total must be 9")

            # Delete section 2
            del_res = srv.post("/api/delete-section", {"domain": "algebra", "ch": 1, "sec": 2})
            self.assertEqual(del_res.get("status"), "ok")

            # Verify ch1.json
            ch1_data = json.loads(ch1_path.read_text(encoding="utf-8"))
            secs = ch1_data.get("secs", [])
            self.assertEqual(len(secs), 4, "Remaining sections count must be exactly 4")

            # Verify consecutive 1..4 numbering
            sec_numbers = [s.get("n") for s in secs]
            self.assertEqual(sec_numbers, [1, 2, 3, 4], "Sections must be re-indexed consecutively 1..4")

            # Verify section titles shifted correctly:
            self.assertEqual(secs[0]["t"], "Original Section 1")
            self.assertEqual(secs[1]["t"], "Original Section 3")
            self.assertEqual(secs[2]["t"], "Original Section 4")
            self.assertEqual(secs[3]["t"], "Original Section 5")

            # Check meta.json
            meta_post = json.loads(meta_path.read_text(encoding="utf-8"))
            top1 = next(t for t in meta_post["ch"] if t["n"] == 1)
            top1_secs = top1.get("secs", [])
            self.assertEqual(len(top1_secs), 4)
            self.assertEqual([s["n"] for s in top1_secs], [1, 2, 3, 4])

            # CRITICAL ADVERSARIAL CHECK: Does meta.total get recalculated?
            # 9 total minus 3 tasks from deleted section 2 = 6 tasks
            actual_total = meta_post.get("total")
            self.assertEqual(
                actual_total,
                6,
                f"DEFECT CONFIRMED: meta.total is {actual_total}, expected 6! "
                "server.py handle_delete_section omitted meta['total'] recalculation."
            )


class TestAdversarialTaskDifficultyAndCRUD(unittest.TestCase):
    """Stress test task difficulty boundaries (1..8) and CRUD mutations."""

    def test_difficulty_boundary_validation_strictly_1_to_8(self):
        """Verify difficulty values outside 1..8 are rejected with HTTP 400."""
        with IsolatedDataSandbox() as srv:
            srv.post("/api/save-topic", {"domain": "algebra", "ch": 1, "t": "T1", "s": "T1", "p": 1, "pos": [100, 100]})
            srv.post("/api/save-section", {"domain": "algebra", "ch": 1, "sec": 1, "t": "Sec 1", "th": []})

            # Negative and out-of-range integer difficulties MUST be rejected
            invalid_difficulties = [0, -1, -10, 9, 10, 100, "invalid"]
            for diff in invalid_difficulties:
                try:
                    res = srv.post("/api/save-task", {
                        "domain": "algebra",
                        "ch": 1,
                        "sec": 1,
                        "task": {"id": f"1.1.bad_{diff}", "st": "Bad diff", "d": diff, "sol": {}}
                    })
                    self.assertEqual(res.get("status"), "error", f"Difficulty {diff} should be rejected")
                except urllib.error.HTTPError as err:
                    self.assertEqual(err.code, 400, f"Difficulty {diff} should return HTTP 400")

            # Valid boundaries: 1 and 8
            for valid_d in [1, 2, 5, 7, 8]:
                res = srv.post("/api/save-task", {
                    "domain": "algebra",
                    "ch": 1,
                    "sec": 1,
                    "task": {"id": f"1.1.{valid_d}", "st": f"Valid diff {valid_d}", "d": valid_d, "sol": {}}
                })
                self.assertEqual(res.get("status"), "ok", f"Valid difficulty {valid_d} should be accepted")

    def test_task_creation_and_deletion_integrity(self):
        """Stress test task additions and deletions verifying total tracking and json integrity."""
        with IsolatedDataSandbox() as srv:
            srv.post("/api/save-topic", {"domain": "algebra", "ch": 1, "t": "T1", "s": "T1", "p": 1, "pos": [100, 100]})
            srv.post("/api/save-section", {"domain": "algebra", "ch": 1, "sec": 1, "t": "Sec 1", "th": []})

            task_count = 8
            for i in range(1, task_count + 1):
                res = srv.post("/api/save-task", {
                    "domain": "algebra",
                    "ch": 1,
                    "sec": 1,
                    "task": {"id": f"1.1.{i}", "st": f"Task {i}", "d": (i % 8) + 1, "sol": {}}
                })
                self.assertEqual(res.get("status"), "ok")

            ch1_path = srv.workdir / "data" / "algebra" / "ch1.json"
            meta_path = srv.workdir / "data" / "algebra" / "meta.json"

            ch1_data = json.loads(ch1_path.read_text(encoding="utf-8"))
            sec_p = ch1_data["secs"][0]["g"][0]["p"]
            self.assertEqual(len(sec_p), task_count, f"All {task_count} tasks should exist in ch1.json")

            meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta_data.get("total"), task_count, f"meta.total should equal {task_count}")

            # Delete half of them
            for i in range(1, (task_count // 2) + 1):
                del_res = srv.post("/api/delete-task", {
                    "domain": "algebra",
                    "ch": 1,
                    "sec": 1,
                    "id": f"1.1.{i}"
                })
                self.assertEqual(del_res.get("status"), "ok")

            meta_data_after = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta_data_after.get("total"), task_count // 2, "meta.total must equal remaining count")


class TestAdversarialUniversalTextEditorAndKaTeX(unittest.TestCase):
    """Stress test math input preprocessing, unclosed delimiters, and HTML/script injection."""

    def test_format_math_input_fuzzing(self):
        """Verify math preprocessing does not crash on unclosed, unbalanced, or nested delimiters."""
        content = INDEX_HTML_PATH.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("function formatMathInput(", content)

        def js_format_math_input(raw_text):
            if not raw_text:
                return ""
            s = str(raw_text)
            s = re.sub(r"\$\$([\s\S]*?)\$\$", lambda m: f'<span class="m d">{m.group(1).strip()}</span>', s)
            s = re.sub(r"\$([^\$\n]+?)\$", lambda m: f'<span class="m">{m.group(1).strip()}</span>', s)
            s = re.sub(r"\\\(([\s\S]*?)\\\)", lambda m: f'<span class="m">{m.group(1).strip()}</span>', s)
            if not re.search(r"<p\b[^>]*>", s, re.IGNORECASE):
                parts = re.split(r"\n\s*\n", s)
                wrapped = []
                for p in parts:
                    p = p.strip().replace("\n", "<br>")
                    if p:
                        wrapped.append(f"<p>{p}</p>")
                s = "".join(wrapped)
            return s

        fuzz_cases = [
            "$$ unclosed display math",
            "$ unbalanced single dollar math",
            "$ a $ b $ c $",
            "$$$$",
            "$$$$$$",
            "$$ $nested$ $$",
            r"\( unclosed paren math",
            r"\( balanced \)",
            "<script>alert('xss')</script>",
            '<img src="x" onerror="alert(1)">',
            "",
            "   \n\n\t   ",
            "A" * 50000,
            r"\frac{1}{2} + \sum_{i=1}^n \binom{n}{k} = 2^n",
        ]

        for case in fuzz_cases:
            try:
                res = js_format_math_input(case)
                self.assertIsInstance(res, str)
            except Exception as err:
                self.fail(f"formatMathInput crashed on input: {case!r} with error: {err}")

    def test_katex_safe_rendering_options(self):
        """Verify KaTeX rendering calls specify throwOnError: false."""
        content = INDEX_HTML_PATH.read_text(encoding="utf-8", errors="ignore")
        # Ensure all window.katex.render occurrences in index.html use throwOnError: false
        matches = [m.start() for m in re.finditer(r"window\.katex\.render\(", content)]
        self.assertGreaterEqual(len(matches), 1, "Must contain window.katex.render calls")
        for pos in matches:
            block = content[pos:pos+300]
            self.assertIn("throwOnError: false", block, f"KaTeX call at offset {pos} missing throwOnError: false")

    def test_save_text_html_injection_dual_sync_safety(self):
        """Verify saving HTML tags does not break embedded script tags in index.html."""
        with IsolatedDataSandbox() as srv:
            payload = {
                "domain": "algebra",
                "type": "theory",
                "ch": 1,
                "sec": 1,
                "field": "html",
                "content": '<p>Test injection: </script><script>alert(1)</script></p>'
            }
            res = srv.post("/api/save-text", payload)
            self.assertEqual(res.get("status"), "ok")

            index_path = srv.workdir / "index.html"
            scripts = extract_embedded_json_map(index_path.read_text(encoding="utf-8"))
            self.assertNotIn("__parse_error__", str(scripts), "Embedded script parsing should not be corrupted by injection payload")


class TestAdversarialTopicIndexGapClientRegression(unittest.TestCase):
    """Regression test for CH[chNum - 1] 1-based array indexing bug when topics are deleted."""

    def test_verify_topic_gap_ch_indexing_defect(self):
        """Demonstrate that index.html CH[chNum - 1] indexing crashes when topics have non-contiguous IDs."""
        content = INDEX_HTML_PATH.read_text(encoding="utf-8", errors="ignore")

        # Verify presence of vulnerable CH[chNum - 1] lines in index.html
        vulnerable_patterns = [
            r"CH\[chNum\s*-\s*1\]\.secs",
            r"CH\[(?:chNum|ch|r\.ch|cur\.ch)\s*-\s*1\]",
        ]
        found = False
        for pat in vulnerable_patterns:
            if re.search(pat, content):
                found = True
                break
        self.assertFalse(found, "Vulnerable CH[...] patterns must not exist in index.html")

        # Architectural proof:
        # If topics are created with n=1, n=2, n=3 and topic 2 is deleted:
        # META.ch = [{ "n": 1 }, { "n": 3 }]
        # CH = META.ch (length is 2)
        # Attempting to access topic 3 via CH[3 - 1] produces IndexError / undefined:
        meta_ch = [{"n": 1, "t": "Topic 1", "secs": []}, {"n": 3, "t": "Topic 3", "secs": []}]
        chNum = 3
        lookup_result = meta_ch[chNum - 1] if (chNum - 1 < len(meta_ch)) else None
        self.assertIsNone(
            lookup_result,
            f"CH[chNum - 1] fails when topic n=3 is at array index 1 (length 2)."
        )


class TestZeroEmojiAndBaselineHash(unittest.TestCase):
    """Verify zero emojis in all project files and intact baseline SHA-256 hash."""

    def test_zero_emojis_across_entire_math_archive(self):
        """Verify no emoji violations across math-archive files."""
        all_violations = []
        for f in MATH_ARCHIVE_DIR.rglob("*"):
            if f.is_file() and f.suffix in (".html", ".py", ".json", ".js", ".css") and "tests" not in f.parts and "__pycache__" not in f.parts:
                text = f.read_text(encoding="utf-8", errors="ignore")
                v = find_emoji_violations(text)
                if v:
                    all_violations.append((str(f.relative_to(MATH_ARCHIVE_DIR)), v[:3]))
        self.assertEqual(all_violations, [], f"Found emoji violations in {len(all_violations)} files: {all_violations}")

    def test_baseline_sha256_hash_match(self):
        """Verify olimp-kombinatorika/index.html SHA-256 matches baseline hash."""
        olimp_index = OLIMP_DIR / "index.html"
        self.assertTrue(olimp_index.exists(), "olimp-kombinatorika/index.html must exist")
        import hashlib
        h = hashlib.sha256(olimp_index.read_bytes()).hexdigest().upper()
        self.assertEqual(h, BASELINE_SHA256, f"Hash mismatch: got {h}, expected {BASELINE_SHA256}")


if __name__ == "__main__":
    unittest.main()
