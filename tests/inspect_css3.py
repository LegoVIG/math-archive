import re
from pathlib import Path

index_html = Path("math-archive/index.html").read_text(encoding="utf-8")
style_tags = re.findall(r"<style[^>]*>([\s\S]*?)</style>", index_html)
s2 = style_tags[2]

for pattern in [r"\.bar\b[^{]*\{[^}]*\}", r"\.subj-[^{]*\{[^}]*\}", r"@media[^{]*\{[^}]*\}\s*\}"]:
    matches = re.findall(pattern, s2)
    print(f"Pattern {pattern} matches: {len(matches)}")
    for m in matches[:5]:
        print("---")
        print(m[:300])
