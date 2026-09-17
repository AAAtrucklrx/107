# -*- coding: utf-8 -*-
"""解析第二课堂用户指南(2026版)PDF全文，按页输出到临时文本文件"""
import sys

PDF = r"e:\第二课堂用户指南（2026版）.pdf"
OUT = r"f:\小蜗\scripts\data\tmp_ek2026_full.txt"

engine = None
try:
    import fitz  # PyMuPDF
    engine = "fitz"
except ImportError:
    try:
        import pdfplumber
        engine = "pdfplumber"
    except ImportError:
        print("NO_ENGINE: need PyMuPDF(fitz) or pdfplumber")
        sys.exit(1)

pages = []
if engine == "fitz":
    doc = fitz.open(PDF)
    for i, page in enumerate(doc):
        pages.append(f"\n===== PAGE {i+1} =====\n" + page.get_text("text"))
    total = doc.page_count
    doc.close()
else:
    with pdfplumber.open(PDF) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            pages.append(f"\n===== PAGE {i+1} =====\n" + (page.extract_text() or ""))

text = "".join(pages)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(text)

print(f"engine={engine} pages={total} chars={len(text)} out={OUT}")
