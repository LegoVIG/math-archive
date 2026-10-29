import re
from pathlib import Path

index_html = Path("math-archive/index.html").read_text(encoding="utf-8")
style_tags = re.findall(r"<style[^>]*>([\s\S]*?)</style>", index_html)
s2 = style_tags[2]

for m in re.finditer(r"@media\s*\([^{]+\)\s*\{", s2):
    start = m.start()
    # Find matching closing brace
    depth = 0
    end = start
    for i in range(m.end() - 1, len(s2)):
        if s2[i] == '{':
            depth += 1
        elif s2[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    block = s2[start:end]
    if "max-width" in block:
        print("==================================")
        print(block)
