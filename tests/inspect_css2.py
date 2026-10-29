import re
from pathlib import Path

index_html = Path("math-archive/index.html").read_text(encoding="utf-8")
style_tags = re.findall(r"<style[^>]*>([\s\S]*?)</style>", index_html)
print(f"Total style tags: {len(style_tags)}")
for i, s in enumerate(style_tags):
    print(f"Style tag {i}: {len(s)} bytes")
    if len(s) > 1000:
        # Check media queries
        mqs = re.findall(r"(@media[^{]+)\{", s)
        print(f"  Media queries in tag {i}: {len(mqs)}")
        for m in mqs:
            print("   *", m.strip())
