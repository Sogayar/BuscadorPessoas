# src/utils/exporters.py
import os
from datetime import datetime
from typing import List, Tuple

# HTML opcional
def save_html_report(out_dir: str, person: str, sections: List[Tuple[str, str]]) -> str:
    """
    sections: lista de (titulo, conteudo_textual)
    """
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"report_{_safe_filename(person)}_{stamp}.html"
    fpath = os.path.join(out_dir, fname)

    html_parts = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>Relatório — {person}</title>",
        "<style>body{font:14px/1.5 system-ui,Segoe UI,Arial} h1{font-size:20px} h2{font-size:16px;margin-top:18px} pre{white-space:pre-wrap}</style>",
        f"<h1>Relatório — {person}</h1>",
    ]
    for title, content in sections:
        html_parts.append(f"<h2>{title}</h2><pre>{_escape(content)}</pre>")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))
    return fpath

def _escape(s: str) -> str:
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _safe_filename(name: str) -> str:
    import re
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
    return base or "relatorio"

# ========= PDF (ReportLab) =========
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.units import cm

def save_pdf_report(out_dir: str, person: str, sections: List[Tuple[str, str]]) -> str:
    """
    Gera um PDF simples (A4) com título e seções (quebra automática).
    sections: [(titulo, texto_mono)]
    """
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"report_{_safe_filename(person)}_{stamp}.pdf"
    fpath = os.path.join(out_dir, fname)

    doc = SimpleDocTemplate(
        fpath,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
        title=f"Relatório — {person}",
        author="Buscador de Pessoas"
    )
    styles = getSampleStyleSheet()
    flow = []

    flow.append(Paragraph(f"<b>Relatório — {person}</b>", styles["Title"]))
    flow.append(Spacer(1, 0.5*cm))

    for idx, (title, content) in enumerate(sections, 1):
        flow.append(Paragraph(f"<b>{title}</b>", styles["Heading2"]))
        # quebra o texto em parágrafos menores para evitar páginas gigantes
        for chunk in _chunk_text(content, 1800):
            flow.append(Paragraph(_escape(chunk).replace("\n", "<br/>"), styles["BodyText"]))
            flow.append(Spacer(1, 0.3*cm))
        if idx < len(sections):
            flow.append(PageBreak())

    doc.build(flow)
    return fpath

def _chunk_text(s: str, maxlen: int):
    s = s or ""
    if len(s) <= maxlen:
        yield s; return
    start = 0
    while start < len(s):
        yield s[start:start+maxlen]
        start += maxlen
