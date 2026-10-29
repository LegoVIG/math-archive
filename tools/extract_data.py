#!/usr/bin/env python3
"""Automated Data Extraction & Modular JSON Storage Generator for Math Archive.

Extracts Combinatorics metadata and 29 chapter JSONs from index.html,
merges WIDE.pos graph coordinates, normalizes problem solutions,
and creates clean empty starter templates for Algebra, Number Theory, and Geometry.
"""

import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

# Base paths
TOOLS_DIR = Path(__file__).resolve().parent
MATH_ARCHIVE_DIR = TOOLS_DIR.parent
INDEX_HTML = MATH_ARCHIVE_DIR / "index.html"
DATA_DIR = MATH_ARCHIVE_DIR / "data"

DOMAINS = ["combinatorics", "algebra", "number-theory", "geometry"]


class JSONScriptParser(HTMLParser):
    """Parses <script type="application/json" id="..."> tags from HTML."""

    def __init__(self):
        super().__init__()
        self.scripts = {}
        self._cur_id = None
        self._cur_type = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            d = {k.lower(): (v or "") for k, v in attrs}
            t = d.get("type", "").lower()
            i = d.get("id", "")
            if "application/json" in t and i:
                self._cur_id = i
                self._cur_type = t
                self._buf = []

    def handle_data(self, data):
        if self._cur_id:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._cur_id:
            self.scripts[self._cur_id] = "".join(self._buf).strip()
            self._cur_id = None
            self._cur_type = None
            self._buf = []


def extract_pos_coordinates(html_text: str) -> dict:
    """Extract WIDE.pos coordinates from JS source in index.html."""
    pos_match = re.search(r'pos:\s*\{([^}]+)\}', html_text)
    pos_dict = {}
    if pos_match:
        entries = re.findall(r'(\d+)\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]', pos_match.group(1))
        for topic_id, x, y in entries:
            pos_dict[str(topic_id)] = [int(x), int(y)]
    return pos_dict


def atomic_write_json(file_path: Path, data: dict, indent: int = 2) -> None:
    """Write JSON atomically via temp file with fsync and retry on Windows."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_suffix(".tmp")
    serialized = json.dumps(data, ensure_ascii=False, indent=indent)
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(serialized)
        f.flush()
        os.fsync(f.fileno())
    for _ in range(3):
        try:
            os.replace(temp_path, file_path)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(temp_path, file_path)


def atomic_write_text(file_path: Path, content_str: str) -> None:
    """Write text content to file atomically with fsync and retry on Windows."""
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


def escape_script_tag(raw_json: str) -> str:
    """Safely escape </script> sequences to <\/script> for HTML embedding without altering casing."""
    return re.sub(r'</(?=script)', r'<\\/', raw_json, flags=re.IGNORECASE)


def update_embedded_script_tag(html_text: str, script_id: str, data: dict) -> str:
    """Replace or insert an embedded <script type="application/json" id="..."> tag within HTML."""
    raw_json = json.dumps(data, ensure_ascii=False)
    escaped_json = escape_script_tag(raw_json)
    pattern = rf'<script\s+[^>]*id=[\x22\x27]{re.escape(script_id)}[\x22\x27][^>]*>(.*?)</script>'
    match = re.search(pattern, html_text, flags=re.DOTALL)
    if match:
        return html_text[:match.start(1)] + escaped_json + html_text[match.end(1):]
    else:
        tag = f'\n  <script type="application/json" id="{script_id}">{escaped_json}</script>\n'
        if "</body>" in html_text:
            return html_text.replace("</body>", f"{tag}</body>")
        else:
            return html_text + tag


def normalize_chapter_data(ch_data: dict) -> dict:
    """Normalize chapter data to satisfy validate_ch_schema and renderer."""
    ch_data.setdefault("n", 1)
    ch_data.setdefault("intro", [])
    ch_data.setdefault("secs", [])

    for sec in ch_data.get("secs", []):
        sec.setdefault("th", [])
        sec.setdefault("g", [])
        for grp in sec.get("g", []):
            grp.setdefault("t", "")
            grp.setdefault("p", [])
            for p in grp.get("p", []):
                p.setdefault("ggb", None)
                p.setdefault("src", "")
                p.setdefault("st", "")
                p.setdefault("d", 1)
                if "sol" not in p or not isinstance(p["sol"], dict):
                    p["sol"] = {"ans": "", "idea": "", "plan": ""}
                else:
                    p["sol"].setdefault("ans", "")
                    p["sol"].setdefault("idea", "")
                    p["sol"].setdefault("plan", "")
    return ch_data


def get_clean_empty_meta(domain: str) -> dict:
    """Create clean empty meta schema template for newly initialized subject."""
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


def extract_all(target_data_dir: Path = None, index_html_path: Path = None, sync_to_html: bool = True) -> dict:
    """Run full extraction process, populate data directory, and sync normalized JSONs to index.html."""
    target_data_dir = target_data_dir or DATA_DIR
    index_html_path = index_html_path or INDEX_HTML

    if not index_html_path.exists():
        raise FileNotFoundError(f"index.html not found at {index_html_path}")

    html_text = index_html_path.read_text(encoding="utf-8")
    parser = JSONScriptParser()
    parser.feed(html_text)

    stats = {
        "chapters_extracted": 0,
        "meta_extracted": 0,
        "templates_created": 0,
        "total_tasks": 0,
        "html_synced": False,
    }

    # 1. Combinatorics meta
    comb_meta_raw = parser.scripts.get("meta")
    if not comb_meta_raw:
        raise ValueError("Script tag id='meta' not found in index.html")
    comb_meta = json.loads(comb_meta_raw)

    # Merge WIDE.pos coordinates and ensure topic 1 is reset to baseline [200, 300]
    pos_coords = extract_pos_coordinates(html_text)
    pos_coords["1"] = [200, 300]
    comb_meta["pos"] = pos_coords

    comb_dir = target_data_dir / "combinatorics"
    atomic_write_json(comb_dir / "meta.json", comb_meta)
    stats["meta_extracted"] += 1

    if sync_to_html:
        html_text = update_embedded_script_tag(html_text, "meta", comb_meta)

    # 2. Extract and normalize 29 Combinatorics chapters
    for n in range(1, 30):
        ch_id = f"ch{n}"
        ch_raw = parser.scripts.get(ch_id)
        if not ch_raw:
            raise ValueError(f"Script tag id='{ch_id}' not found in index.html")
        ch_data = json.loads(ch_raw)
        ch_data = normalize_chapter_data(ch_data)

        # Count problems
        for sec in ch_data.get("secs", []):
            for grp in sec.get("g", []):
                stats["total_tasks"] += len(grp.get("p", []))

        atomic_write_json(comb_dir / f"ch{n}.json", ch_data)
        stats["chapters_extracted"] += 1

        if sync_to_html:
            html_text = update_embedded_script_tag(html_text, ch_id, ch_data)

    # 3. Create starter templates for algebra, number-theory, geometry
    for dom in ["algebra", "number-theory", "geometry"]:
        dom_meta = get_clean_empty_meta(dom)
        dom_dir = target_data_dir / dom
        dom_dir.mkdir(parents=True, exist_ok=True)
        # Remove any stale chapter files
        for stale_ch in dom_dir.glob("ch*.json"):
            try:
                stale_ch.unlink()
            except Exception:
                pass
        atomic_write_json(dom_dir / "meta.json", dom_meta)
        stats["templates_created"] += 1

        if sync_to_html:
            html_text = update_embedded_script_tag(html_text, f"meta-{dom}", dom_meta)

    # 4. Atomically persist synchronized index.html
    if sync_to_html:
        atomic_write_text(index_html_path, html_text)
        stats["html_synced"] = True

    return stats


if __name__ == "__main__":
    print(f"Extracting modular JSON data into: {DATA_DIR}...")
    stats = extract_all()
    print(f"Extraction successful: {stats}")
