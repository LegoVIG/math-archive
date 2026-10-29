import re
from pathlib import Path

index_html = Path("math-archive/index.html").read_text(encoding="utf-8")
css_match = re.search(r"<style>([\s\S]*?)</style>", index_html)
if not css_match:
    print("No <style> block found!")
    exit(1)

css = css_match.group(1)
print(f"CSS size: {len(css)} bytes")

# Find media queries
media_queries = re.findall(r"(@media[^{]+)\{", css)
print("Media queries found:", len(media_queries))
for mq in media_queries:
    print(" -", mq.strip())

# Find rules for .subj-nav and .bar
for sel in [".bar", ".subj-nav", ".subj-btn", ".subj-label", ".empty-map"]:
    matches = re.findall(rf"({re.escape(sel)}[^{{]*\{{[^}}]*\}})", css)
    print(f"\nRules for {sel}: {len(matches)}")
    for m in matches[:3]:
        print(m[:200] + "...")
