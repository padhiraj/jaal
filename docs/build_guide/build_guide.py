"""Assemble the JAAL Build Guide (HTML) from the narrative + every source file, then print it to PDF.

    python3 docs/build_guide/build_guide.py
"""
import html
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FILES = [
    ("run.py", "Entry point: generate data, serve, or evaluate."),
    ("jaal/datagen.py", "Step 1: synthetic multi-source dataset + hidden ground truth."),
    ("jaal/ingest.py", "Step 2: parsers, normalisers, FIR extraction, provenance, manifest."),
    ("jaal/resolve.py", "Step 3: entity resolution with explained scores and review queue."),
    ("jaal/algorithms.py", "Step 4: graph algorithms implemented from first principles."),
    ("jaal/engine.py", "Step 5: knowledge graph, analytics, alerts, roles, cross-case, evaluation."),
    ("jaal/audit.py", "Step 6: hash-chained audit log, roles, masking."),
    ("jaal/server.py", "Step 7: HTTP API + static file server with RBAC and auditing."),
    ("web/index.html", "Step 8: page skeleton."),
    ("web/style.css", "Step 8: styling (dark theme)."),
    ("web/app.js", "Step 8: front-end logic and hand-written graph engine."),
    ("tests/test_core.py", "Step 9: unit and end-to-end tests."),
]

CSS = """
@page { size: A4; margin: 15mm 14mm 15mm 14mm; }
body { font: 10.2pt/1.5 -apple-system, "Segoe UI", Inter, Helvetica, Arial, sans-serif; color: #17202e; margin: 0; }
h1 { font-size: 28pt; margin: 0; letter-spacing: 3px; }
h1.part { font-size: 20pt; color: #fff; background: #0b3d63; padding: 14px 16px; border-radius: 8px; letter-spacing: 1px; margin-bottom: 10px; }
h2 { font-size: 14.5pt; margin: 20px 0 8px; padding-bottom: 4px; border-bottom: 2px solid #0e7fc0; color: #0b3d63; page-break-after: avoid; }
h3 { font-size: 11.3pt; margin: 13px 0 5px; color: #0b3d63; page-break-after: avoid; }
p { margin: 5px 0 8px; }
code { font-family: Menlo, Consolas, monospace; font-size: 8.7pt; background: #eef3f8; padding: 0 3px; border-radius: 3px; }
pre { font: 8.4pt/1.45 Menlo, Consolas, monospace; background: #0f1724; color: #d7deea; padding: 10px 12px; border-radius: 6px; white-space: pre-wrap; page-break-inside: avoid; }
table { width: 100%; border-collapse: collapse; margin: 6px 0 12px; font-size: 9.1pt; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #cfd8e3; padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #e9f1f8; color: #0b3d63; }
.cover { height: 262mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always;
  background: linear-gradient(160deg, #0b0f17 0%, #11243c 100%); color: #e8eef7; margin: -15mm -14mm 0; padding: 0 22mm; }
.cover h1 { font-size: 44pt; letter-spacing: 6px; }
.cover .logo { font-size: 46pt; color: #4cc3ff; }
.cover .sub { font-size: 14pt; color: #8fb6d8; margin-top: 6px; }
.cover .tag { font-size: 15pt; margin-top: 24px; max-width: 150mm; line-height: 1.45; }
.cover .meta { margin-top: 40px; color: #9fb0c8; font-size: 11pt; line-height: 1.9; }
.box { background: #f3f8fc; border-left: 4px solid #0e7fc0; padding: 8px 12px; margin: 8px 0 12px; border-radius: 0 6px 6px 0; page-break-inside: avoid; }
.box.big { font-size: 12.5pt; }
.warn { background: #fff7e8; border-left-color: #e19b14; }
.check { background: #eaf8f1; border-left: 4px solid #0a8a55; padding: 6px 12px; margin: 8px 0 12px; border-radius: 0 6px 6px 0; page-break-inside: avoid; }
.example { background: #f6f2fd; border-left: 4px solid #7a4fd0; padding: 6px 12px; margin: 8px 0 12px; border-radius: 0 6px 6px 0; page-break-inside: avoid; }
.script { background: #f7f9fb; border: 1px dashed #b8c6d6; padding: 6px 14px; border-radius: 8px; }
.formula { font-family: Menlo, Consolas, monospace; font-size: 9pt; background: #eef3f8; padding: 8px 10px; border-radius: 6px; page-break-inside: avoid; }
.pb { page-break-before: always; }
ul, ol { margin: 4px 0 8px; padding-left: 22px; } li { margin: 2px 0; }
.toc li { margin: 4px 0; }
.qa td:first-child { width: 30%; font-weight: 600; }
.code { font: 7.2pt/1.36 Menlo, Consolas, monospace; background: #fbfcfe; border: 1px solid #dbe3ec; border-radius: 6px; padding: 6px 0; margin: 4px 0 10px; }
.code .l { display: flex; page-break-inside: avoid; }
.code .n { color: #9aa8b8; min-width: 34px; text-align: right; padding-right: 8px; user-select: none; border-right: 1px solid #e3e9f0; margin-right: 8px; flex-shrink: 0; }
.code .t { white-space: pre-wrap; word-break: break-all; color: #1d2735; }
.filehead { font: 600 10.5pt Menlo, Consolas, monospace; color: #0b3d63; margin-top: 4px; }
.filedesc { font-size: 9pt; color: #5b6b80; margin-bottom: 4px; }
"""


def code_block(path: Path) -> str:
    lines = path.read_text().splitlines()
    rows = "".join(f'<div class="l"><span class="n">{i}</span><span class="t">{html.escape(l) or " "}</span></div>'
                   for i, l in enumerate(lines, 1))
    return f'<div class="code">{rows}</div>', len(lines)


def main():
    body = (HERE / "guide_body.html").read_text()
    app = ['<h1 class="part pb">Appendix — Complete source code</h1>',
           "<p>Type (or copy) these files exactly, in this order, into the folder layout from C0. Line numbers "
           "match the references in Part C. After the last file: <code>python3 run.py</code>.</p><table>"
           "<tr><th>File</th><th>Lines</th><th>Purpose</th></tr>"]
    blocks, total = [], 0
    for rel, desc in FILES:
        blk, n = code_block(ROOT / rel)
        total += n
        app.append(f"<tr><td><code>{rel}</code></td><td>{n}</td><td>{desc}</td></tr>")
        blocks.append(f'<h2 class="pb">{rel}</h2><div class="filedesc">{desc} · {n} lines</div>{blk}')
    app.append(f"<tr><th>Total</th><th>{total}</th><th>+ empty <code>jaal/__init__.py</code></th></tr></table>")
    full = body.replace("<!--APPENDIX-->", "\n".join(app + blocks))
    doc = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>JAAL — Complete Build Guide</title><style>{CSS}</style></head><body>{full}</body></html>'
    out_html = HERE / "JAAL_Build_Guide.html"
    out_html.write_text(doc)
    pdf = ROOT / "docs" / "JAAL_Build_Guide.pdf"
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--user-data-dir={HERE / '.chrome'}", f"--print-to-pdf={pdf}", out_html.as_uri()],
                   check=True, stderr=subprocess.DEVNULL)
    print("wrote", pdf, "and", out_html, f"({total} source lines)")


if __name__ == "__main__":
    main()
