import json
import re
from pathlib import Path

index_html = Path("math-archive/index.html").read_text(encoding="utf-8")

# Find SUBJECTS definition
subj_match = re.search(r"var SUBJECTS = ({[\s\S]*?});", index_html)
if subj_match:
    print("SUBJECTS definition found:")
    print(subj_match.group(1)[:500])

# Find all script type="application/json"
scripts = re.findall(r'<script\s+type=["\']application/json["\']\s+id=["\']([^"\']+)["\']>([\s\S]*?)</script>', index_html)
print(f"Total application/json scripts found: {len(scripts)}")
for sid, content in scripts:
    if "meta" in sid:
        print(f"Meta script ID: {sid}, content length: {len(content)}, parsed successfully: {bool(json.loads(content))}")
