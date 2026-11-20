"""Build docs/solution.pdf from docs/solution.md (Markdown -> HTML -> PDF via headless Edge/Chrome).

    python docs/build_pdf.py            # needs: pip install markdown ; Edge or Chrome installed
Prints the page count, because the brief allows at most 2 pages.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parent
SRC, HTML, PDF = DOCS / "solution.md", DOCS / "solution.html", DOCS / "solution.pdf"

CSS = """
@page { size: A4; margin: 11mm 12mm 11mm 12mm; }
body { font-family: "Segoe UI", Calibri, Arial, sans-serif; font-size: 8.6pt; line-height: 1.32; color: #111; margin: 0; }
h1 { font-size: 14pt; margin: 0 0 1pt; } h1 + p { color: #444; margin-top: 0; }
h2 { font-size: 10pt; margin: 6pt 0 2pt; padding-bottom: 1pt; border-bottom: 1px solid #bbb; }
h3 { font-size: 9pt; margin: 4pt 0 1pt; }
p { margin: 2pt 0 3pt; } ul, ol { margin: 1pt 0 3pt; padding-left: 14pt; } li { margin: 0 0 1pt; }
table { border-collapse: collapse; width: 100%; margin: 2pt 0 4pt; font-size: 7.8pt; }
th, td { border: 1px solid #ccc; padding: 1.5pt 3pt; text-align: left; vertical-align: top; }
th { background: #f0f0f0; } code { font-family: Consolas, monospace; font-size: 7.8pt; background: #f4f4f4; padding: 0 2px; }
pre { font-size: 7.4pt; background: #f6f6f6; padding: 3pt 5pt; margin: 2pt 0 4pt; white-space: pre-wrap; }
a { color: #0b57d0; text-decoration: none; }
"""


def find_browser() -> str:
    for c in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if Path(c).exists():
            return c
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    sys.exit("No Edge/Chrome found to print the PDF.")


def main():
    body = markdown.markdown(SRC.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
    HTML.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>Solution</title><style>{CSS}</style>"
                    f"</head><body>{body}</body></html>", encoding="utf-8")
    subprocess.run([find_browser(), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={PDF}", HTML.as_uri()], check=True, capture_output=True, timeout=120)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", PDF.read_bytes()))
    print(f"{PDF.name}: {pages} page(s)" + ("  <-- over the 2-page limit!" if pages > 2 else ""))


if __name__ == "__main__":
    main()
