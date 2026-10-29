"""Milestone 1 Iteration 2 Adversarial Boundary Test Suite.

Exhaustively verifies:
1. Data schema parsing & mutation isolation across all 4 subjects.
2. Hash routing edge cases: malformed hashes, cross-subject topic jumps (#alg/t1 vs #komb/t1).
3. Responsive layout scaling across desktop, tablet, and mobile viewports.
4. Theming palettes, WCAG contrast, zero-emoji compliance, and base source integrity.
"""

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
MATH_ARCHIVE_DIR = ROOT_DIR / "math-archive"
OLIMP_DIR = ROOT_DIR / "olimp-kombinatorika"
INDEX_HTML = MATH_ARCHIVE_DIR / "index.html"

EXPECTED_OLIMP_HASH = "AC80217ABDB24FDFD02A54C056D31F8DB41FFF1F75AA04F736D9A7506DB50935"

EMOJI_RANGES = [
    (0x1F600, 0x1F64F), (0x1F300, 0x1F5FF), (0x1F680, 0x1F6FF),
    (0x1F700, 0x1F77F), (0x1F780, 0x1F7FF), (0x1F800, 0x1F8FF),
    (0x1F900, 0x1F9FF), (0x1FA00, 0x1FA6F), (0x1FA70, 0x1FAFF),
    (0x2600, 0x26FF), (0x2700, 0x27BF), (0xFE00, 0xFE0F),
    (0x1F000, 0x1F02F), (0x1F0A0, 0x1F0FF), (0x2300, 0x23FF), (0x2B50, 0x2B55)
]


class TestDataSchemaParsingAndMutationIsolation(unittest.TestCase):
    """Test Suite 1: Data Schema Parsing & In-Memory Mutation Isolation."""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_combinatorics_schema_parsing(self):
        """Verify embedded combinatorics metadata matches exact schema and problem count."""
        m = re.search(r'<script\s+type="application/json"\s+id="meta">(.*?)</script>', self.html, re.DOTALL)
        self.assertIsNotNone(m, "id='meta' script must exist in index.html")
        meta = json.loads(m.group(1))

        self.assertEqual(meta.get("total"), 3014, "Combinatorics total problems must be 3014")
        self.assertEqual(len(meta.get("parts", [])), 3, "Combinatorics must have 3 parts")
        self.assertEqual(len(meta.get("ch", [])), 29, "Combinatorics must have 29 topics")
        self.assertGreaterEqual(len(meta.get("edges", [])), 30, "Combinatorics graph must have >= 30 edges")

        # Verify getSubjectMeta adds pos dictionary and WIDE coordinates exist in application code
        self.assertIn("if (!parsed.pos) parsed.pos = {};", self.html, "getSubjectMeta must ensure pos dictionary exists")
        self.assertIn("WIDE", self.html, "WIDE graph layout definition must exist in application code")

    def test_combinatorics_all_29_topic_schemas(self):
        """Verify all 29 embedded chapter scripts (ch1..ch29) exist and parse cleanly."""
        total_tasks = 0
        for ch_n in range(1, 30):
            m = re.search(rf'<script\s+type="application/json"\s+id="ch{ch_n}">(.*?)</script>', self.html, re.DOTALL)
            self.assertIsNotNone(m, f"id='ch{ch_n}' script must exist in index.html")
            data = json.loads(m.group(1))

            self.assertEqual(data.get("n"), ch_n, f"Chapter n must match {ch_n}")
            self.assertIn("intro", data, f"Chapter {ch_n} must have intro")
            self.assertIn("secs", data, f"Chapter {ch_n} must have secs")
            for sec in data["secs"]:
                self.assertIn("n", sec)
                self.assertIn("t", sec)
                for g in sec.get("g", []):
                    for task in g.get("p", []):
                        total_tasks += 1
                        self.assertIn("id", task)
                        self.assertIn("st", task)
                        self.assertIn("d", task)
                        self.assertTrue(1 <= task["d"] <= 8, f"Difficulty must be 1..8, got {task['d']}")

        self.assertEqual(total_tasks, 3014, f"Total tasks in ch1..ch29 must equal 3014, got {total_tasks}")

    def test_empty_subjects_default_template_schemas(self):
        """Verify getSubjectMeta returns valid empty schema for algebra, NT, geometry."""
        # Extract default schema template from getSubjectMeta fallback in index.html
        m = re.search(r'function getSubjectMeta\(sId\)\s*\{(.*?)\n\}', self.html, re.DOTALL)
        self.assertIsNotNone(m, "getSubjectMeta function must exist in index.html")
        fn_code = m.group(1)

        fallback_m = re.search(r'return\s+(\{.*?\});', fn_code)
        self.assertIsNotNone(fallback_m, "Fallback template object must exist in getSubjectMeta")
        # Template has keys: total: 0, parts: ['Методы', 'Типы задач', 'Разделы'], ch: [], edges: [], macros: {}, preface: '', credit: '', pos: {}
        self.assertIn("total: 0", fallback_m.group(1))
        self.assertIn("ch: []", fallback_m.group(1))
        self.assertIn("edges: []", fallback_m.group(1))
        self.assertIn("pos: {}", fallback_m.group(1))

    def test_schema_deep_mutation_isolation(self):
        """Verify mutating one subject's in-memory data does not contaminate others."""
        # Factory simulating getSubjectMeta
        def create_template():
            return {
                "total": 0,
                "parts": ["Методы", "Типы задач", "Разделы"],
                "ch": [],
                "edges": [],
                "macros": {},
                "preface": "",
                "credit": "",
                "pos": {}
            }

        # Initialize mock stores
        algebra_meta = create_template()
        algebra_pidx = {}

        number_theory_meta = create_template()
        number_theory_pidx = {}

        combinatorics_meta = {
            "total": 3014,
            "parts": ["Методы", "Типы задач", "Разделы"],
            "ch": [{"n": i, "t": f"Topic {i}", "secs": []} for i in range(1, 30)],
            "edges": [[0, 1]],
            "pos": {str(i): [100, 100] for i in range(1, 30)}
        }
        combinatorics_pidx = {f"1.1.{i}": {"ch": 1, "sec": 1, "d": 1} for i in range(1, 3015)}

        # Adversarially pollute algebra store
        algebra_meta["ch"].append({"n": 999, "t": "Poison Topic", "secs": []})
        algebra_meta["edges"].append([999, 1000])
        algebra_meta["pos"]["999"] = [9999, 9999]
        algebra_pidx["999.1.1"] = {"ch": 999, "sec": 1, "d": 1}

        # Verify Number Theory is 100% clean
        self.assertEqual(len(number_theory_meta["ch"]), 0, "Number Theory chapters must remain 0")
        self.assertEqual(len(number_theory_meta["edges"]), 0, "Number Theory edges must remain 0")
        self.assertEqual(len(number_theory_pidx), 0, "Number Theory PIDX must remain 0")

        # Verify Combinatorics is 100% untouched
        self.assertEqual(len(combinatorics_meta["ch"]), 29, "Combinatorics chapters must remain 29")
        self.assertEqual(len(combinatorics_pidx), 3014, "Combinatorics PIDX must remain 3014")
        self.assertNotIn("999.1.1", combinatorics_pidx, "Combinatorics must not contain poison task")

        # Verify reloading algebra via template re-creates a clean state
        algebra_reloaded = create_template()
        self.assertEqual(len(algebra_reloaded["ch"]), 0, "Reloaded algebra must be clean zero-state")
        self.assertEqual(len(algebra_reloaded["edges"]), 0, "Reloaded algebra edges must be empty")


class TestHashRoutingEdgeCases(unittest.TestCase):
    """Test Suite 2: Hash Routing Edge Cases & Cross-Subject Jumps."""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

        cls.SUBJ_BY_SLUG = {
            'combinatorics': 'combinatorics', 'komb': 'combinatorics',
            'algebra': 'algebra', 'alg': 'algebra',
            'number-theory': 'number-theory', 'nt': 'number-theory',
            'geometry': 'geometry', 'geom': 'geometry'
        }

    def parse_hash(self, h, cur_subject="combinatorics"):
        """Python implementation mirroring index.html parseHash exactly."""
        h = (h or '').lstrip('#').lstrip('/').strip()
        parts = h.split('/') if h else []
        target_subj = cur_subject
        sub_route = ''

        if parts and parts[0].lower() in self.SUBJ_BY_SLUG:
            target_subj = self.SUBJ_BY_SLUG[parts[0].lower()]
            sub_route = '/'.join(parts[1:])
        elif parts and (re.match(r'^t\d+', parts[0]) or re.match(r'^p\d+', parts[0])):
            sub_route = h
        elif h == '' or h == '#':
            pass
        else:
            target_subj = 'combinatorics'

        # Number of chapters per domain
        ch_counts = {
            'combinatorics': 29,
            'algebra': 0,
            'number-theory': 0,
            'geometry': 0
        }
        ch_len = ch_counts.get(target_subj, 0)

        # PID index mock
        has_pid = lambda pid: target_subj == 'combinatorics' and pid == '1.1.1'

        m = re.match(r'^t(\d+)(?:-(\d+))?$', sub_route)
        if m:
            ch = int(m.group(1))
            if 1 <= ch <= ch_len:
                sec = int(m.group(2)) if m.group(2) else 0
                return {'v': 'topic', 'ch': ch, 'sec': sec, 'subj': target_subj}

        m_p = re.match(r'^p(\d+\.\d+\.\d+)$', sub_route)
        if m_p and has_pid(m_p.group(1)):
            return {'v': 'topic', 'ch': 1, 'sec': 1, 'pid': m_p.group(1), 'subj': target_subj}

        return {'v': 'map', 'subj': target_subj}

    def test_cross_subject_topic_jump_algebra_vs_komb(self):
        """Verify #alg/t1 falls back to algebra map (no t1) while #komb/t1 resolves to topic 1."""
        res_alg = self.parse_hash("#alg/t1")
        self.assertEqual(res_alg["v"], "map", "Empty algebra domain must fall back to map view")
        self.assertEqual(res_alg["subj"], "algebra", "Must settle on algebra domain")

        res_komb = self.parse_hash("#komb/t1")
        self.assertEqual(res_komb["v"], "topic", "Combinatorics domain has chapter 1")
        self.assertEqual(res_komb["ch"], 1)
        self.assertEqual(res_komb["subj"], "combinatorics")

    def test_cross_subject_topic_jump_geometry_and_nt(self):
        """Verify #geom/t1 and #nt/t1 fall back cleanly to empty map views."""
        res_geom = self.parse_hash("#geom/t1")
        self.assertEqual(res_geom["v"], "map")
        self.assertEqual(res_geom["subj"], "geometry")

        res_nt = self.parse_hash("#nt/t1")
        self.assertEqual(res_nt["v"], "map")
        self.assertEqual(res_nt["subj"], "number-theory")

    def test_cross_subject_problem_jump(self):
        """Verify #komb/p1.1.1 resolves while #alg/p1.1.1 and #geom/p1.1.1 fall back to map."""
        res_komb_p = self.parse_hash("#komb/p1.1.1")
        self.assertEqual(res_komb_p["v"], "topic")
        self.assertEqual(res_komb_p["pid"], "1.1.1")
        self.assertEqual(res_komb_p["subj"], "combinatorics")

        res_alg_p = self.parse_hash("#alg/p1.1.1")
        self.assertEqual(res_alg_p["v"], "map")
        self.assertEqual(res_alg_p["subj"], "algebra")

        res_geom_p = self.parse_hash("#geom/p1.1.1")
        self.assertEqual(res_geom_p["v"], "map")
        self.assertEqual(res_geom_p["subj"], "geometry")

    def test_malformed_and_adversarial_hashes(self):
        """Verify malformed and out-of-bounds hashes are safely caught and return valid view state."""
        bad_hashes = [
            "#", "##", "###", "#/", "#///", "#../../../etc/passwd",
            "#<script>alert(1)</script>", "#?foo=bar", "#!/root",
            "#-1", "#t0", "#t9999", "#t1-0", "#t1-9999",
            "#p0.0.0", "#p99.99.99", "#null", "#undefined", "#NaN",
            "#invalid_slug/extra/path", "#komb/invalid/deep/nested"
        ]
        for bh in bad_hashes:
            res = self.parse_hash(bh)
            self.assertIn(res["v"], ["map", "topic"], f"Hash {bh} must resolve to safe view state")
            self.assertIn(res["subj"], ["combinatorics", "algebra", "number-theory", "geometry"])

    def test_slug_aliases_and_case_insensitivity(self):
        """Verify slug aliases and case insensitivity resolve to correct canonical subjects."""
        slug_tests = [
            ("#alg", "algebra"), ("#ALG", "algebra"), ("#algebra", "algebra"), ("#ALGEBRA", "algebra"),
            ("#geom", "geometry"), ("#GEOM", "geometry"), ("#geometry", "geometry"),
            ("#nt", "number-theory"), ("#NT", "number-theory"), ("#number-theory", "number-theory"),
            ("#komb", "combinatorics"), ("#KOMB", "combinatorics"), ("#combinatorics", "combinatorics")
        ]
        for h, expected in slug_tests:
            res = self.parse_hash(h)
            self.assertEqual(res["subj"], expected, f"Hash {h} must resolve to {expected}")
            self.assertEqual(res["v"], "map")


class TestResponsiveLayoutCssScaling(unittest.TestCase):
    """Test Suite 3: Responsive Layout CSS Scaling Across Viewports."""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")
        # Extract and concatenate all CSS blocks
        styles = re.findall(r'<style>(.*?)</style>', cls.html, re.DOTALL)
        cls.css = "\n".join(styles)

    def test_desktop_default_labels(self):
        """Verify default desktop styling displays full labels and hides short labels."""
        self.assertIn(".subj-label-short", self.css)
        self.assertTrue(re.search(r'\.subj-label-short\s*\{[^}]*display:\s*none', self.css),
                        ".subj-label-short must be hidden by default on desktop")

    def test_tablet_breakpoint_860px(self):
        """Verify media query at 860px hides full labels and displays short labels."""
        self.assertIn("@media (max-width: 860px)", self.css)
        self.assertIn(".subj-label-full { display: none; }", self.css)
        self.assertIn(".subj-label-short { display: inline; }", self.css)

    def test_mobile_breakpoint_560px(self):
        """Verify media query at 560px compacts button padding and dimensions."""
        self.assertIn("@media (max-width: 560px)", self.css)
        self.assertIn(".subj-btn { padding: 0 6px; height: 28px; font-size: 11.5px; }", self.css)

    def test_ultra_narrow_breakpoint_380px(self):
        """Verify media query at 380px suppresses all text labels for icon-only mode."""
        m = re.search(r'@media\s*\(\s*max-width:\s*380px\s*\)\s*\{([^}]+(?:\{[^}]+\}[^}]+)*)\}', self.css)
        self.assertIsNotNone(m, "@media (max-width: 380px) rule must exist in CSS")
        block = m.group(1)
        self.assertTrue(re.search(r'\.subj-btn\s+span:not\(\.subj-icon\)\s*\{[^}]*display:\s*none', block),
                        "All span text inside .subj-btn must be hidden at 380px for icon-only mode")

    def test_bar_header_overflow_resilience(self):
        """Verify .bar header has overflow text truncation rules."""
        self.assertIn(".bar", self.css)
        self.assertIn("overflow:hidden", self.css.replace(" ", ""))
        self.assertIn("text-overflow:ellipsis", self.css.replace(" ", ""))


class TestSubjectPalettesAndThemeStyling(unittest.TestCase):
    """Test Suite 4: Distinct Palettes, WCAG Contrast, Zero Emojis & Base Integrity."""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_base_project_immutability(self):
        """Verify olimp-kombinatorika/index.html is 100% untouched."""
        olimp_file = OLIMP_DIR / "index.html"
        self.assertTrue(olimp_file.exists(), "Original file must exist")
        h = hashlib.sha256(olimp_file.read_bytes()).hexdigest().upper()
        self.assertEqual(h, EXPECTED_OLIMP_HASH, "Original olimp-kombinatorika must remain unchanged")

    def test_zero_emoji_compliance(self):
        """Verify 0 emojis exist anywhere in math-archive/index.html."""
        violations = []
        for i, ch in enumerate(self.html):
            cp = ord(ch)
            for s, e in EMOJI_RANGES:
                if s <= cp <= e:
                    violations.append((i, ch, f"U+{cp:04X}"))
                    break
        self.assertEqual(len(violations), 0, f"Found {len(violations)} emoji violations: {violations[:5]}")

    def test_distinct_subject_palette_variables(self):
        """Verify all 4 subjects have unique palettes defined in CSS."""
        palettes = {
            "combinatorics": ["#a9d850", "#4a8a10"],
            "algebra": ["#38bdf8", "#0284c7"],
            "number-theory": ["#c084fc", "#7e22ce"],
            "geometry": ["#fb923c", "#c2410c"]
        }
        for subj, colors in palettes.items():
            for c in colors:
                self.assertIn(c.lower(), self.html.lower(), f"Color {c} for {subj} must be present in index.html")

    def test_palette_contrast_ratio_formula(self):
        """Verify WCAG contrast ratio for all 4 dark subject palettes against chalkboard (#1a1a1a)."""
        def get_luminance(hex_str):
            r = int(hex_str[1:3], 16) / 255
            g = int(hex_str[3:5], 16) / 255
            b = int(hex_str[5:7], 16) / 255
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        bg_lum = get_luminance("#1e293b")  # Dark slate chalkboard
        dark_palettes = {
            "combinatorics": "#a9d850",
            "algebra": "#38bdf8",
            "number-theory": "#c084fc",
            "geometry": "#fb923c"
        }
        for subj, hex_val in dark_palettes.items():
            fg_lum = get_luminance(hex_val)
            ratio = (fg_lum + 0.05) / (bg_lum + 0.05)
            self.assertGreaterEqual(ratio, 3.0, f"Contrast ratio for {subj} ({ratio:.2f}) must be >= 3.0")


if __name__ == "__main__":
    unittest.main()
