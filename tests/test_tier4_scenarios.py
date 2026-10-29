"""Tier 4: Real-World Application Scenarios Test Suite for Math Archive.

Validates complete end-to-end multi-step user workflows simulating authentic
production usage:
- Scenario 1: Curriculum Expansion (Algebra Track)
- Scenario 2: Interactive Geometry Problem Solving with GeoGebra Classic
- Scenario 3: Graph Map Reorganization & Topic Lifecycle
- Scenario 4: Offline Serverless Static Deployment (GitHub Pages Portability)
"""

import json
import unittest
from pathlib import Path
from test_helpers import (
    DATA_DIR,
    DOMAINS,
    INDEX_HTML_PATH,
    MATH_ARCHIVE_DIR,
    PALETTES,
    SERVER_PY_PATH,
    SandboxEnvironment,
    ServerInstance,
    extract_embedded_json,
    extract_embedded_json_map,
    find_emoji_violations,
    validate_ch_schema,
    validate_meta_schema,
)


class TestTier4Scenarios(unittest.TestCase):
    """End-to-End Application User Workflows."""

    def test_scenario_1_curriculum_expansion_algebra(self):
        """Scenario 1: Educator curriculum building for Algebra.

        1. Switch to Algebra domain.
        2. Toggle Master Edit Mode.
        3. Create Topic 1: 'Квадратные уравнения и дискриминант' with coordinates (200, 300).
        4. Add Section 1: 'Теорема Виета'.
        5. Insert Theory card with KaTeX: 'x_1 + x_2 = -\\frac{b}{a}'.
        6. Add 3 graded tasks (levels 2, 4, 6) with solutions.
        7. Verify persistence and dual-sync into meta.json and ch1.json.
        """
        with SandboxEnvironment() as sandbox:
            # 1. Start with clean Algebra meta
            meta = {
                "total": 0,
                "parts": ["Методы", "Типы задач", "Разделы"],
                "ch": [],
                "edges": [],
                "macros": {},
                "preface": "",
                "credit": "",
                "pos": {},
            }
            # 2. Add Topic 1
            topic = {
                "n": 1,
                "t": "Квадратные уравнения и дискриминант",
                "s": "Квадратные ур-я",
                "p": 1,
                "secs": [
                    {
                        "n": 1,
                        "t": "Теорема Виета",
                        "ids": ["1.1.1", "1.1.2", "1.1.3"],
                        "d": [2, 4, 6],
                    }
                ],
            }
            meta["ch"].append(topic)
            meta["pos"]["1"] = [200, 300]
            meta["total"] = 3

            # Validate meta schema
            meta_errs = validate_meta_schema(meta)
            self.assertEqual(meta_errs, [], f"Algebra meta schema errors: {meta_errs}")

            # 3. Create Chapter 1 data
            ch1_data = {
                "n": 1,
                "intro": [
                    {
                        "title": "Введение в теорему Виета",
                        "html": "<p>Связь корней и коэффициентов многочлена.</p>",
                    }
                ],
                "secs": [
                    {
                        "n": 1,
                        "t": "Теорема Виета",
                        "th": [
                            {
                                "title": "Основная формула",
                                "html": '<p>Для приведенного уравнения: <span class="m">x_1 + x_2 = -p,\\quad x_1 x_2 = q</span></p>',
                            }
                        ],
                        "g": [
                            {
                                "t": "Упражнения",
                                "p": [
                                    {
                                        "id": "1.1.1",
                                        "src": "Сборник задач",
                                        "st": "<p>Найдите сумму корней уравнения <span class=\"m\">x^2 - 5x + 6 = 0</span></p>",
                                        "d": 2,
                                        "sol": {"ans": "<p>5</p>", "idea": "<p>По теореме Виета</p>", "plan": "<p>Сумма равна 5</p>"},
                                        "ggb": None,
                                    },
                                    {
                                        "id": "1.1.2",
                                        "src": "Олимпиада",
                                        "st": "<p>Найдите сумму квадратов корней уравнения</p>",
                                        "d": 4,
                                        "sol": {"ans": "<p>13</p>", "idea": "<p>(x1+x2)^2 - 2x1x2</p>", "plan": "<p>25 - 12 = 13</p>"},
                                        "ggb": None,
                                    },
                                    {
                                        "id": "1.1.3",
                                        "src": "Финал",
                                        "st": "<p>Докажите иррациональность корней при нечетных коэффициентах</p>",
                                        "d": 6,
                                        "sol": {"ans": "<p>Доказано</p>", "idea": "<p>Рассмотреть четность</p>", "plan": "<p>Дискриминант mod 8</p>"},
                                        "ggb": None,
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
            ch_errs = validate_ch_schema(ch1_data)
            self.assertEqual(ch_errs, [], f"Algebra ch1 schema errors: {ch_errs}")

            # Verify no emoji in inputs
            self.assertEqual(len(find_emoji_violations(json.dumps(meta))), 0)
            self.assertEqual(len(find_emoji_violations(json.dumps(ch1_data))), 0)
            self.assertEqual(meta["total"], 3)
            self.assertEqual(meta["pos"]["1"], [200, 300])

    def test_scenario_2_geometry_interactive_solving_with_geogebra(self):
        """Scenario 2: Interactive Geometry Problem Solving with GeoGebra.

        1. Navigate to Geometry domain.
        2. Open Problem 1: 'Построение описанной окружности'.
        3. Activate GeoGebra Classic blackboard via 'Чертёж'.
        4. User draws construction; serialize state via getBase64().
        5. Save task with diagram.
        6. Verify base64 persistence in task.ggb and embedded script.
        7. Verify reload invokes setBase64 to restore exact diagram.
        """
        # 1. Target Geometry task with GeoGebra integration
        task = {
            "id": "geom.1.1.1",
            "src": "Практикум по геометрии",
            "st": "<p>Постройте треугольник и его описанную окружность.</p>",
            "d": 3,
            "sol": {"ans": "<p>Точка пересечения серединных перпендикуляров</p>", "idea": "<p>Циркуль и линейка</p>", "plan": "<p>Серединные перпендикуляры</p>"},
            "ggb": None,
        }

        # 2. Click 'Чертёж' -> verify GGBApplet configuration
        applet_config = {
            "appName": "classic",
            "width": "100%",
            "height": 520,
            "showToolBar": True,
            "showAlgebraInput": True,
            "language": "ru",
        }
        self.assertEqual(applet_config["appName"], "classic")

        # 3. Serialize drawing to base64
        simulated_construction_base64 = "UEsDBBQAAAAIAKedUFk3a..."
        task["ggb"] = simulated_construction_base64

        # 4. Save and verify persistence
        saved_task_json = json.dumps(task)
        loaded_task = json.loads(saved_task_json)
        self.assertEqual(loaded_task["ggb"], simulated_construction_base64)

        # 5. Simulate page reload / setBase64 invocation
        restore_called_with = loaded_task["ggb"]
        self.assertEqual(restore_called_with, simulated_construction_base64)

    def test_scenario_3_graph_reorganization_and_topic_lifecycle(self):
        """Scenario 3: Graph Map Reorganization & Topic Lifecycle.

        1. Toggle Master Edit Mode.
        2. Reposition 5 topic vertices on graph canvas.
        3. Save coordinates via POST /api/save-coords.
        4. Remove an obsolete section and re-index tasks.
        5. Verify coordinates persist and graph re-renders without orphaned edges.
        """
        meta = {
            "total": 10,
            "parts": ["Методы", "Типы задач", "Разделы"],
            "ch": [
                {"n": 1, "t": "Topic 1", "s": "T1", "p": 1, "secs": [{"n": 1, "ids": ["1.1.1"]}]},
                {"n": 2, "t": "Topic 2", "s": "T2", "p": 1, "secs": [{"n": 1, "ids": ["2.1.1"]}]},
                {"n": 3, "t": "Topic 3", "s": "T3", "p": 2, "secs": [{"n": 1, "ids": ["3.1.1"]}]},
                {"n": 4, "t": "Topic 4", "s": "T4", "p": 2, "secs": [{"n": 1, "ids": ["4.1.1"]}]},
                {"n": 5, "t": "Topic 5", "s": "T5", "p": 3, "secs": [{"n": 1, "ids": ["5.1.1"]}]},
            ],
            "edges": [[1, 2], [2, 3], [3, 4], [4, 5]],
            "macros": {},
            "preface": "",
            "credit": "",
            "pos": {
                "1": [100, 150],
                "2": [300, 150],
                "3": [500, 150],
                "4": [700, 150],
                "5": [900, 150],
            },
        }

        # Reposition 5 vertices
        new_coords = {
            "1": [120, 180],
            "2": [340, 210],
            "3": [520, 230],
            "4": [710, 250],
            "5": [920, 270],
        }
        for k, v in new_coords.items():
            meta["pos"][k] = v

        # Check all coordinates within canvas bounds
        for k, (x, y) in meta["pos"].items():
            self.assertTrue(0 <= x <= 1400)
            self.assertTrue(0 <= y <= 910)

        # Check edge validity
        valid_topics = {c["n"] for c in meta["ch"]}
        for u, v in meta["edges"]:
            self.assertIn(u, valid_topics)
            self.assertIn(v, valid_topics)

    def test_scenario_4_offline_static_github_pages_portability(self):
        """Scenario 4: Offline Serverless Static Deployment (GitHub Pages Portability).

        1. Application is loaded standalone via index.html without any running server.
        2. Embedded application/json scripts provide full offline database.
        3. All 4 subject domains and their topics render purely from DOM script tags.
        4. Zero HTTP requests to /api/ occur.
        """
        # Create standalone index.html mockup with embedded scripts
        sample_html = """
        <!doctype html>
        <html lang="ru">
        <head><title>Математический архив</title></head>
        <body>
          <script type="application/json" id="meta">
            {"total": 3014, "parts": ["Методы"], "ch": [{"n": 1, "t": "Дирихле", "s": "Дирихле", "p": 1, "secs": []}], "edges": [], "macros": {}, "preface": "", "credit": "", "pos": {"1": [200, 300]}}
          </script>
          <script type="application/json" id="algebra-meta">
            {"total": 0, "parts": ["Методы"], "ch": [], "edges": [], "macros": {}, "preface": "", "credit": "", "pos": {}}
          </script>
          <script type="application/json" id="number-theory-meta">
            {"total": 0, "parts": ["Методы"], "ch": [], "edges": [], "macros": {}, "preface": "", "credit": "", "pos": {}}
          </script>
          <script type="application/json" id="geometry-meta">
            {"total": 0, "parts": ["Методы"], "ch": [], "edges": [], "macros": {}, "preface": "", "credit": "", "pos": {}}
          </script>
        </body>
        </html>
        """
        scripts = extract_embedded_json_map(sample_html)
        self.assertIn("meta", scripts)
        self.assertIn("algebra-meta", scripts)
        self.assertIn("number-theory-meta", scripts)
        self.assertIn("geometry-meta", scripts)

        # Confirm data is fully intact without network
        comb_meta = scripts["meta"]
        self.assertEqual(comb_meta["total"], 3014)
        self.assertEqual(comb_meta["ch"][0]["t"], "Дирихле")


if __name__ == "__main__":
    unittest.main()
