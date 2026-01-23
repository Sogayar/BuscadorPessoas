# =========================
# Worker (thread) com exportação PDF (ReportLab)
# =========================

import os, time, threading, textwrap
from datetime import datetime

from src.utils.pickers import pick_news_urls, pick_organic_urls  # :contentReference[oaicite:4]{index=4}
from src.utils.extract import extract_main_text, safe_filename    # :contentReference[oaicite:5]{index=5}
from src.core.search_router import QuotaAwareRouter               # :contentReference[oaicite:6]{index=6}
from src.utils.identity import score_name_match, apply_hints_to_query, qualifies_by_hints
from src.utils.settings import load_settings                     # :contentReference[oaicite:7]{index=7}

# ---- ReportLab (PDF) ----
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm

# -------------------------
# Helpers de PDF
# -------------------------
def _pdf_wrapped_text(c, x, y, text, max_width_cm=17.5, leading=14, font="Helvetica", size=10):
    """
    Escreve 'text' no canvas com wrap automático, retornando a nova coordenada y.
    """
    c.setFont(font, size)
    width_pt = max_width_cm * cm
    wrapper = textwrap.TextWrapper(width=1000)  # base: quebramos por largura em pontos
    # Quebra manual por largura aproximada (heurística simples):
    lines = []
    for paragraph in (text or "").splitlines():
        # estima caracteres por linha considerando fonte 10 (≈ 0.5*size por char) — heurística:
        # 1 char ~ 5-6 pt nessa fonte/tamanho -> chars por linha ~ width_pt / 5.5
        approx = max(20, int(width_pt / 5.5))
        w = textwrap.TextWrapper(width=approx, break_long_words=True, replace_whitespace=False)
        lines.extend(w.wrap(paragraph) or [""])
    for line in lines:
        if y < 2*cm:
            c.showPage()
            c.setFont(font, size)
            y = A4[1] - 2*cm
        c.drawString(x, y, line)
        y -= leading
    return y

def save_pdf(out_path, title, sections):
    """
    Gera um PDF simples:
      - title (string)
      - sections: lista de dicts [{"heading": str, "body": str}, ...]
    """
    c = canvas.Canvas(out_path, pagesize=A4)
    w, h = A4

    # Cabeçalho
    y = h - 2*cm
    c.setFont("Helvetica-Bold", 14)
    c.drawString(2*cm, y, title)
    y -= 0.8*cm
    c.setFont("Helvetica", 9)
    c.drawString(2*cm, y, datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    y -= 1.2*cm

    for sec in sections:
        heading = (sec.get("heading") or "").strip()
        body    = (sec.get("body") or "").strip()
        if heading:
            c.setFont("Helvetica-Bold", 12)
            if y < 2*cm:
                c.showPage()
                y = h - 2*cm
            c.drawString(2*cm, y, heading)
            y -= 0.6*cm
        if body:
            y = _pdf_wrapped_text(c, 2*cm, y, body, leading=14, size=10)
        y -= 0.4*cm

    c.showPage()
    c.save()

# -------------------------
# Worker
# -------------------------
class BuscadorWorker(threading.Thread):
    def __init__(self, names, out_dir, log_q, progress_cb, done_cb, stop_event,
                 sleep_between=0.6, n_top=3, include_news=True, include_org=True,
                 build_index_csv=False, index_rows_acc=None, router=None):
        super().__init__(daemon=True)
        self.settings = load_settings()  # filtros (cpf/city/uf), export_pdf, etc. :contentReference[oaicite:8]{index=8}
        self.names = names
        self.out_dir = out_dir
        self.log_q = log_q
        self.progress_cb = progress_cb
        self.done_cb = done_cb
        self.stop_event = stop_event
        self.sleep_between = sleep_between
        self.n_top = n_top
        self.include_news = include_news
        self.include_org = include_org
        self.build_index_csv = build_index_csv
        self.index_rows_acc = index_rows_acc if index_rows_acc is not None else []
        self.router = router or QuotaAwareRouter()  # router sempre presente

    def log(self, msg: str):
        self.log_q.put(msg)

    def run(self):
        total = len(self.names)
        completed = 0
        try:
            for raw_name in self.names:
                if self.stop_event.is_set():
                    self.log("➡ Execução cancelada pelo usuário.")
                    break

                name_disp = (raw_name or "").strip()
                if not name_disp:
                    completed += 1
                    self.progress_cb(completed, total)
                    continue

                filters = (self.settings or {}).get("filters", {}) or {}
                cpf  = (filters.get("cpf")  or "").strip()
                city = (filters.get("city") or "").strip()
                uf   = (filters.get("uf")   or "").strip()
                export_pdf = bool((self.settings or {}).get("export_pdf", False))

                # Query orgânica com hints
                query_for_org = apply_hints_to_query(name_disp, cpf=cpf, city=city, uf=uf)

                self.log(f"\n🔎 Buscando: {name_disp}  (raw; hints={','.join([x for x in [cpf, city, uf] if x]) or 'nenhum'})")

                news_urls, org_urls = [], []

                # 1) Orgânicos (providers pagos → router.search)
                if self.include_org and self.router:
                    try:
                        result = self.router.search(query_for_org, user_id="tk")
                        payload = result["response"]
                        prov = result.get("provider", "desconhecido")
                        org_urls = pick_organic_urls(payload, n=self.n_top)
                        self.log(f"  • Orgânico via {prov}: {len(org_urls)}")
                    except Exception as e:
                        self.log(f"  ⚠ Erro na busca orgânica: {e}")

                # 2) Notícias gratuitas (RSS → fallback GDELT) → router.search_news_free
                if self.include_news and self.router:
                    try:
                        nres = self.router.search_news_free(name_disp, user_id="tk", max_n=self.n_top*2)
                        news_payload = nres.get("response", {})
                        news_urls = pick_news_urls(news_payload, n=self.n_top)
                        self.log(f"  • Notícias via {nres.get('provider','none')}: {len(news_urls)}")
                    except Exception as e:
                        self.log(f"  ⚠ Erro na busca de notícias: {e}")

                # CSV-índice (opcional)
                if self.build_index_csv:
                    for i, u in enumerate(news_urls, 1):
                        self.index_rows_acc.append([name_disp, "noticia", i, u])
                    for i, u in enumerate(org_urls, 1):
                        self.index_rows_acc.append([name_disp, "organico", i, u])

                # Montagem dos blocos de TXT
                blocks = [f"# Buscador rápido — consulta: {name_disp}\n"]

                if news_urls:
                    blocks.append(f"## Notícias (top {self.n_top})\n")
                    for i, u in enumerate(news_urls, 1):
                        if self.stop_event.is_set(): break
                        self.log(f"    - Extraindo notícia {i}: {u}")
                        txt = extract_main_text(u)
                        blocks.append(f"### Notícia {i}\nURL: {u}\n\n{txt}\n")
                        time.sleep(self.sleep_between)

                if org_urls and not self.stop_event.is_set():
                    blocks.append(f"## Resultados orgânicos (top {self.n_top})\n")
                    for i, u in enumerate(org_urls, 1):
                        if self.stop_event.is_set(): break
                        self.log(f"    - Extraindo link {i}: {u}")
                        txt = extract_main_text(u)
                        blocks.append(f"### Link {i}\nURL: {u}\n\n{txt}\n")
                        time.sleep(self.sleep_between)

                # Texto concatenado (sem cabeçalhos)
                concatenado = [f"# Texto concatenado — {name_disp}\n"]
                if not self.stop_event.is_set():
                    for u in news_urls + org_urls:
                        txt = extract_main_text(u)
                        if txt:
                            concatenado.append(txt)
                        time.sleep(self.sleep_between)

                header_text = "\n".join(blocks).strip()
                full_concat  = "\n\n".join(concatenado).strip()

                # Salva TXT
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                base   = f"{safe_filename(name_disp)}_{stamp}"
                f_txt  = os.path.join(self.out_dir, base + ".txt")
                try:
                    with open(f_txt, "w", encoding="utf-8") as f:
                        f.write(header_text)
                        f.write("\n\n" + "="*80 + "\n\n")
                        f.write(full_concat)
                    self.log(f"  ✅ TXT salvo: {f_txt}")
                except Exception as e:
                    self.log(f"  ⚠ Falha ao salvar TXT: {e}")

                # Salva PDF (opcional)
                if export_pdf:
                    try:
                        sections = []
                        # Constrói seções do PDF com base nos blocos
                        if news_urls:
                            sections.append({"heading": f"Notícias (top {self.n_top})", "body": ""})
                            for i, u in enumerate(news_urls, 1):
                                txt = extract_main_text(u)
                                body = f"[{i}] {u}\n\n{txt or ''}"
                                sections.append({"heading": None, "body": body})
                        if org_urls:
                            sections.append({"heading": f"Resultados orgânicos (top {self.n_top})", "body": ""})
                            for i, u in enumerate(org_urls, 1):
                                txt = extract_main_text(u)
                                body = f"[{i}] {u}\n\n{txt or ''}"
                                sections.append({"heading": None, "body": body})

                        f_pdf = os.path.join(self.out_dir, base + ".pdf")
                        title = f"Relatório — {name_disp}"
                        save_pdf(f_pdf, title, sections)
                        self.log(f"  📄 PDF salvo: {f_pdf}")
                    except Exception as e:
                        self.log(f"  ⚠ Falha ao gerar PDF: {e}")

                completed += 1
                self.progress_cb(completed, total)

        finally:
            self.done_cb()
