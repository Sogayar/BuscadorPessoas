import os, re, json, time, threading, queue, csv, sys
from datetime import datetime
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import trafilatura
from dotenv import load_dotenv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ====== NOVO: roteador de cotas ======
from search_router import init_db, QuotaAwareRouter

# =========================
# Config e Utilidades
# =========================
APP_TITLE = "Buscador Rápido — Notícias + Orgânicos por pessoa (quota-aware)"
SETTINGS_FILE = "settings.json"

load_dotenv()

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36")
}

def clean_text(txt: str) -> str:
    if not txt:
        return ""
    txt = re.sub(r"\s+\n", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    return txt.strip()

# =========================
# Filtros e ranqueamento de URLs (AUDIT-READY)
# =========================
BLOCKED_DOMAINS = {
    "instagram.com","www.instagram.com",
    "x.com","twitter.com","mobile.twitter.com",
    "youtube.com","www.youtube.com","m.youtube.com","youtu.be",
    "tiktok.com","www.tiktok.com",
    "facebook.com","m.facebook.com","www.facebook.com",
    "threads.net","linktr.ee","beacons.ai",
}
ALLOWED_TLDS_SUFFIX = (".gov.br",".jus.br",".leg.br")
ALLOWED_KEY_DOMAINS = {
    "g1.globo.com","oglobo.globo.com","www1.folha.uol.com.br","www.folha.uol.com.br",
    "www.estadao.com.br","istoe.com.br","veja.abril.com.br","www.bbc.com",
    "www.cnnbrasil.com.br","www.uol.com.br","www.metropoles.com","time.com"
}
KEYWORDS_ALLOWED_IN_PATH = {"biografia","biography","perfil","profile","quem-e","quem-é","sobre","about"}

def is_low_signal_url(url: str) -> bool:
    try:
        u = urlparse(url); host = (u.netloc or "").lower(); path = (u.path or "").lower()
        if host in BLOCKED_DOMAINS: return True
        if any(tok in host for tok in ("loj","shop","store")) or any(tok in path for tok in ("loj","shop","store")): return True
        if host.endswith(("x.com","twitter.com")) and ("/status/" not in path): return True
        if host.endswith("youtube.com") and not ("/watch" in path or "/channel" in path): return True
        if host in {"bit.ly","tinyurl.com","lnkd.in"}: return True
    except Exception:
        return True
    return False

def is_preferable_url(url: str) -> bool:
    u = urlparse(url); host = (u.netloc or "").lower(); path = (u.path or "").lower()
    return host.endswith(ALLOWED_TLDS_SUFFIX) or host in ALLOWED_KEY_DOMAINS or any(k in path for k in KEYWORDS_ALLOWED_IN_PATH)

RELATED_PATTERNS = (
    r"^\s*(veja também|leia também|relacionadas|relacionados|materiais relacionados)\b.*",
    r"^\s*(related|read more|more from|you might also like|newsletter|sign up)\b.*",
    r"^\s*(mais lidas|mais vistas|mais lidos)\b.*",
)
REL_RE = re.compile("|".join(RELATED_PATTERNS), flags=re.IGNORECASE)

def _trim_related_sections(text: str) -> str:
    lines = text.splitlines(); out = []
    for ln in lines:
        if REL_RE.match(ln.strip()): break
        if ln.strip().startswith(("-", "•")) and len(ln.strip()) < 80: continue
        out.append(ln)
    return "\n".join(out)

def _keep_person_paragraphs(text: str, person_name: str) -> str:
    name = person_name.lower()
    parts = [p for p in name.split() if p]
    last = parts[-1] if len(parts) >= 2 else None
    kept = []
    for p in re.split(r"\n{2,}", text):
        pl = p.lower()
        if name in pl or (last and f" {last} " in pl):
            kept.append(p.strip())
    return "\n\n".join(kept) if kept else text

def extract_main_text(url: str, person_name: str | None = None) -> str:
    # 1) Trafilatura com foco em precisão
    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=True)
        if downloaded:
            extracted = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
                favor_recall=False,
                output_format="txt",
                target_language="pt"
            )
            if extracted and len(extracted) > 300:
                extracted = clean_text(extracted)
                extracted = _trim_related_sections(extracted)
                if person_name:
                    extracted = _keep_person_paragraphs(extracted, person_name)
                return extracted
    except Exception:
        pass
    # 2) Fallback: BeautifulSoup + poda de seções
    try:
        r = requests.get(url, headers=UA, timeout=30); r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for bad in soup(["script","style","noscript","header","footer","nav","aside","form"]): bad.decompose()
        for cls in ["related","more","newsletter","most-read","mais-lidas"]:
            for el in soup.select(f".{cls}"): el.decompose()
        text = soup.get_text("\n", strip=True)
        if len(text) > 50000: text = text[:50000]
        text = clean_text(text); text = _trim_related_sections(text)
        if person_name: text = _keep_person_paragraphs(text, person_name)
        return text
    except Exception:
        return ""

def _score_url_for_person(url: str, person_name: str, extracted_preview: str = "") -> int:
    score = 0
    if is_preferable_url(url): score += 10
    txt = extracted_preview or ""
    if txt:
        score += min(len(txt)//100, 30)
        name = person_name.lower()
        mentions = txt.lower().count(name)
        parts = [p for p in name.split() if p]
        if len(parts) >= 2:
            last = parts[-1]
            mentions += txt.lower().count(f" {last} ")
        score += 3 * min(mentions, 5)
    return score

def filter_and_rank_urls(urls: list[str], person_name: str, n: int) -> list[str]:
    kept = []
    for u in urls:
        if is_low_signal_url(u):
            # opcional: logue descartes aqui
            continue
        kept.append(u)
    urls = kept
    previews = {}
    for u in urls[:12]:
        try:
            d = trafilatura.fetch_url(u, no_ssl=True)
            previews[u] = (trafilatura.extract(d, favor_precision=True) or "")[:2000] if d else ""
        except Exception:
            previews[u] = ""
    scored = sorted(urls, key=lambda u: _score_url_for_person(u, person_name, previews.get(u,"")), reverse=True)
    return scored[:n]

# =========================
# Normalização dos payloads por provedor
# =========================
def pick_news_urls(payload: dict, n=3):
    out = []
    if isinstance(payload.get("news"), list):
        for it in payload["news"]:
            link = it.get("link") or it.get("url")
            if link: out.append(link)
    for k in ("news_results", "top_stories"):
        if isinstance(payload.get(k), list):
            for it in payload[k]:
                link = it.get("url") or it.get("link")
                if link: out.append(link)
    seen, dedup = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); dedup.append(u)
    return dedup[:n]

def pick_organic_urls(payload: dict, n=3):
    out = []
    if isinstance(payload.get("items"), list):  # Google CSE
        for it in payload["items"]:
            link = it.get("link")
            if link: out.append(link)
    if isinstance(payload.get("organic"), list):  # Serper / Zenserp
        for it in payload["organic"]:
            link = it.get("link") or it.get("url")
            if link: out.append(link)
    if isinstance(payload.get("organic_results"), list):  # Serpstack
        for it in payload["organic_results"]:
            link = it.get("url") or it.get("link")
            if link: out.append(link)
    seen, dedup = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); dedup.append(u)
    return dedup[:n]

# =========================
# Worker (thread)
# =========================
class BuscadorWorker(threading.Thread):
    def __init__(self, names, out_dir, log_q, progress_cb, done_cb, stop_event,
                 sleep_between=0.6, n_top=3, include_news=True, include_org=True,
                 build_index_csv=False, index_rows_acc=None, router=None):
        super().__init__(daemon=True)
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
        self.router = router

    def log(self, msg: str):
        self.log_q.put(msg)

    def _extract_valid(self, url: str, person: str) -> str:
        tx = extract_main_text(url, person_name=person)
        if not tx or len(tx) < 300:
            return ""
        name = person.lower()
        parts = [p for p in name.split() if p]
        last = parts[-1] if len(parts) >= 2 else None
        if (name not in tx.lower()) and (not last or f" {last} " not in tx.lower()):
            return ""
        return tx

    def run(self):
        total = len(self.names); completed = 0
        try:
            for name in self.names:
                if self.stop_event.is_set():
                    self.log("➡ Execução cancelada pelo usuário.")
                    break
                person = name.strip()
                if not person:
                    completed += 1; self.progress_cb(completed, total); continue

                self.log(f"\n🔎 Buscando: {person}")
                try:
                    result = self.router.search(person, user_id="tk")
                    payload = result["response"]; provider = result["provider"]
                    self.log(f"  • Provedor utilizado: {provider}")
                except Exception as e:
                    self.log(f"  ⚠ Erro na busca: {e}")
                    completed += 1; self.progress_cb(completed, total); continue

                # pega mais bruto
                news_raw = pick_news_urls(payload, n=10) if self.include_news else []
                org_raw  = pick_organic_urls(payload, n=10) if self.include_org else []
                # filtra e ranqueia
                news_urls = filter_and_rank_urls(news_raw, person, n=self.n_top)
                org_urls  = filter_and_rank_urls(org_raw,  person, n=self.n_top)

                self.log(f"  • Notícias: {len(news_urls)} | Busca Orgânica: {len(org_urls)}")

                if self.build_index_csv:
                    for i, u in enumerate(news_urls, 1):
                        self.index_rows_acc.append([person, "noticia", i, u])
                    for i, u in enumerate(org_urls, 1):
                        self.index_rows_acc.append([person, "organico", i, u])

                blocks = [f"# Buscador rápido — consulta: {person}\n"]

                if news_urls:
                    blocks.append(f"## Notícias (top {self.n_top})\n")
                    rank = 1
                    for u in news_urls:
                        if self.stop_event.is_set(): break
                        self.log(f"    - Extraindo notícia {rank}: {u}")
                        txt = self._extract_valid(u, person)
                        if not txt:
                            self.log("      (descartado: pouco texto/sem menção)")
                            continue
                        blocks.append(f"### Notícia {rank}\nURL: {u}\n\n{txt}\n")
                        rank += 1
                        time.sleep(self.sleep_between)

                if org_urls and not self.stop_event.is_set():
                    blocks.append(f"## Resultados orgânicos (top {self.n_top})\n")
                    rank = 1
                    for u in org_urls:
                        if self.stop_event.is_set(): break
                        self.log(f"    - Extraindo link {rank}: {u}")
                        txt = self._extract_valid(u, person)
                        if not txt:
                            self.log("      (descartado: pouco texto/sem menção)")
                            continue
                        blocks.append(f"### Link {rank}\nURL: {u}\n\n{txt}\n")
                        rank += 1
                        time.sleep(self.sleep_between)

                concatenado = [f"# Texto concatenado — {person}\n"]
                if not self.stop_event.is_set():
                    for u in news_urls + org_urls:
                        txt = self._extract_valid(u, person)
                        if txt:
                            concatenado.append(txt)
                        time.sleep(self.sleep_between)

                header_text = "\n".join(blocks).strip()
                full_concat = "\n\n".join(concatenado).strip()

                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                fname = f"{safe_filename(person)}_{stamp}.txt"
                fpath = os.path.join(self.out_dir, fname)
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(header_text)
                        f.write("\n\n" + "="*80 + "\n\n")
                        f.write(full_concat)
                    self.log(f"  ✅ Salvo: {fpath}")
                except Exception as e:
                    self.log(f"  ⚠ Falha ao salvar '{fname}': {e}")

                completed += 1
                self.progress_cb(completed, total)
        finally:
            self.done_cb()

# =========================
# Interface Tkinter
# =========================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)

        # Inicializa DB e Router
        init_db()
        self.router = QuotaAwareRouter()

        # Janela
        try: self.state("zoomed")
        except Exception: pass
        self.update_idletasks()
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.minsize(880, 560)

        # Atalhos
        self.bind("<F11>", lambda e: self.toggle_maximize())
        self.bind("<Escape>", lambda e: self.restore_window())

        # Estilo
        try:
            self.style = ttk.Style(self)
            if "vista" in self.style.theme_names():
                self.style.theme_use("vista")
        except Exception:
            pass

        # Estados
        self.stop_event = threading.Event()
        self.worker = None
        self.log_q = queue.Queue()
        self.total_names = 0
        self.done_count = 0

        # Settings
        s = load_settings()
        default_out = s.get("out_dir", os.path.abspath("./saidas"))
        self.out_dir = tk.StringVar(value=default_out)
        self.n_top = tk.IntVar(value=int(s.get("n_top", 3)))
        self.include_news = tk.BooleanVar(value=bool(s.get("include_news", True)))
        self.include_org = tk.BooleanVar(value=bool(s.get("include_org", True)))
        self.build_index_csv = tk.BooleanVar(value=bool(s.get("build_index_csv", False)))

        os.makedirs(self.out_dir.get(), exist_ok=True)

        self._build_ui()
        self._poll_log()

    # Helpers de tamanho
    def toggle_maximize(self):
        try:
            if self.state() == "zoomed":
                self.state("normal")
            else:
                self.state("zoomed")
        except Exception:
            self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def restore_window(self):
        try:
            self.state("normal"); self.attributes("-fullscreen", False)
        except Exception:
            pass

    # UI
    def _build_ui(self):
        header = ttk.Frame(self, padding=(12, 12, 12, 6)); header.pack(fill="x")
        ttk.Label(header, text="Buscador rápido", font=("Segoe UI", 14, "bold")).pack(side="left")
        ttk.Label(header, text=" — cole nomes, escolha as opções e gere um arquivo por pessoa.").pack(side="left")

        dir_frame = ttk.Frame(self, padding=(12, 6, 12, 6)); dir_frame.pack(fill="x")
        ttk.Label(dir_frame, text="Pasta de saída:").pack(side="left")
        self.out_entry = ttk.Entry(dir_frame, textvariable=self.out_dir); self.out_entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(dir_frame, text="Escolher…", command=self.choose_dir).pack(side="left", padx=(0,8))
        ttk.Button(dir_frame, text="Abrir pasta", command=self.open_dir).pack(side="left")

        body = ttk.Frame(self, padding=(12, 6, 12, 12)); body.pack(fill="both", expand=True)
        left = ttk.Frame(body); left.pack(side="left", fill="both", expand=True, padx=(0,6))
        right = ttk.Frame(body); right.pack(side="left", fill="both", expand=True, padx=(6,0))

        ttk.Label(left, text="Nomes (um por linha):").pack(anchor="w")
        self.names_txt = tk.Text(left, height=16, wrap="none", undo=True); self.names_txt.pack(fill="both", expand=True)
        self.placeholder = "Ex.: Maria Silva\nJoão Pereira\n…"
        self._placeholder_active = True
        self._set_placeholder()
        self.names_txt.bind("<FocusIn>", self._on_focus_in)
        self.names_txt.bind("<FocusOut>", self._on_focus_out)

        actions = ttk.Frame(left); actions.pack(fill="x", pady=(6,0))
        ttk.Button(actions, text="Colar da área de transferência", command=self.paste_clip).pack(side="left")
        ttk.Button(actions, text="Importar .txt", command=self.import_txt).pack(side="left", padx=(8,0))
        ttk.Button(actions, text="Limpar", command=self.clear_names).pack(side="left", padx=(8,0))

        opts = ttk.LabelFrame(right, text="Opções", padding=10); opts.pack(fill="x")
        row1 = ttk.Frame(opts); row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="Resultados por tipo (1–10):").pack(side="left")
        self.top_spin = ttk.Spinbox(row1, from_=1, to=10, textvariable=self.n_top, width=5); self.top_spin.pack(side="left", padx=(6,12))
        self.cb_news = ttk.Checkbutton(row1, text="Incluir Notícias", variable=self.include_news); self.cb_news.pack(side="left", padx=(0,12))
        self.cb_org = ttk.Checkbutton(row1, text="Incluir Buscas Orgânicas", variable=self.include_org); self.cb_org.pack(side="left")

        row2 = ttk.Frame(opts); row2.pack(fill="x", pady=2)
        self.cb_index = ttk.Checkbutton(row2, text="Gerar CSV-índice dos links", variable=self.build_index_csv); self.cb_index.pack(side="left")

        log_box = ttk.LabelFrame(right, text="Log", padding=10); log_box.pack(fill="both", expand=True, pady=(8,0))
        self.log_txt = tk.Text(log_box, height=14, state="disabled", wrap="word"); self.log_txt.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_box, command=self.log_txt.yview); log_scroll.pack(side="right", fill="y")
        self.log_txt.config(yscrollcommand=log_scroll.set)

        bottom = ttk.Frame(self, padding=(12, 6, 12, 12)); bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100); self.progress.pack(fill="x", side="left", expand=True)
        self.progress_label = ttk.Label(bottom, text="0/0", width=6, anchor="e"); self.progress_label.pack(side="left", padx=(6,12))
        self.btn_start = ttk.Button(bottom, text="Iniciar", command=self.on_start); self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bottom, text="Cancelar", command=self.on_stop, state="disabled"); self.btn_stop.pack(side="left", padx=(8,0))

    # placeholder
    def _set_placeholder(self):
        self.names_txt.config(fg="#666"); self.names_txt.delete("1.0", "end"); self.names_txt.insert("1.0", self.placeholder); self._placeholder_active = True
    def _on_focus_in(self, _):
        if self._placeholder_active:
            self.names_txt.delete("1.0", "end"); self.names_txt.config(fg="#000"); self._placeholder_active = False
    def _on_focus_out(self, _):
        content = self.names_txt.get("1.0", "end").strip()
        if not content: self._set_placeholder()

    # ações nomes
    def paste_clip(self):
        try:
            text = self.clipboard_get()
            if self._placeholder_active:
                self.names_txt.delete("1.0", "end"); self._placeholder_active = False
            self.names_txt.config(fg="#000"); self.names_txt.insert("end", text)
        except Exception:
            messagebox.showwarning("Atenção", "Não foi possível ler a área de transferência.")

    def import_txt(self):
        path = filedialog.askopenfilename(title="Importar nomes de arquivo .txt", filetypes=[("Texto","*.txt"),("Todos os arquivos","*.*")])
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f: data = f.read()
            if self._placeholder_active:
                self.names_txt.delete("1.0", "end"); self._placeholder_active = False; self.names_txt.config(fg="#000")
            self.names_txt.insert("end", data if data.endswith("\n") else data + "\n")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao importar: {e}")

    def clear_names(self):
        self.names_txt.delete("1.0", "end"); self._placeholder_active = False; self.names_txt.config(fg="#000")

    # pasta
    def choose_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.out_dir.get(), title="Escolha a pasta de saída")
        if chosen:
            self.out_dir.set(chosen); self._persist_settings()

    def open_dir(self):
        folder = self.out_dir.get().strip()
        if not os.path.isdir(folder):
            messagebox.showwarning("Atenção", "A pasta configurada não existe."); return
        try:
            if os.name == "nt": os.startfile(folder)  # type: ignore
            elif sys.platform == "darwin": os.system(f'open "{folder}"')
            else: os.system(f'xdg-open "{folder}"')
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível abrir a pasta: {e}")

    # execução
    def on_start(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Em execução", "Já existe uma execução em andamento."); return

        names_raw = self.names_txt.get("1.0", "end")
        if self._placeholder_active: names_raw = ""
        names = [n.strip() for n in names_raw.splitlines() if n.strip()]
        if not names:
            messagebox.showwarning("Atenção", "Informe ao menos um nome (um por linha)."); return

        try:
            n_top_val = int(self.n_top.get())
            if n_top_val < 1 or n_top_val > 10: raise ValueError
        except Exception:
            messagebox.showwarning("Atenção", "O número de resultados por tipo deve ser entre 1 e 10."); return

        include_news = bool(self.include_news.get()); include_org = bool(self.include_org.get())
        if not include_news and not include_org:
            messagebox.showwarning("Atenção", "Selecione ao menos um tipo: Notícias ou Orgânicos."); return

        out_dir = self.out_dir.get().strip()
        try: os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível criar/acessar a pasta de saída: {e}"); return

        self._persist_settings()

        self.stop_event.clear(); self.total_names = len(names); self.done_count = 0
        self.progress["value"] = 0; self._set_progress_label(0, self.total_names)
        self._append_log("=== Iniciando ===")

        self.btn_start.config(state="disabled"); self.btn_stop.config(state="normal")

        index_rows_acc = [] if self.build_index_csv.get() else None

        self.worker = BuscadorWorker(
            names=names,
            out_dir=out_dir,
            log_q=self.log_q,
            progress_cb=self._update_progress,
            done_cb=lambda: self._on_done(index_rows_acc),
            stop_event=self.stop_event,
            sleep_between=0.6,
            n_top=n_top_val,
            include_news=include_news,
            include_org=include_org,
            build_index_csv=self.build_index_csv.get(),
            index_rows_acc=index_rows_acc,
            router=self.router,
        )
        self.worker.start()

    def on_stop(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self._append_log("Solicitada a parada. Aguardando o término seguro…")

    def _update_progress(self, completed, total):
        self.done_count = completed
        pct = 0 if total == 0 else int((completed / total) * 100)
        self.after(0, lambda: (self.progress.config(value=pct), self._set_progress_label(completed, total)))

    def _set_progress_label(self, done, total):
        self.progress_label.config(text=f"{done}/{total}")

    def _on_done(self, index_rows_acc):
        def finish():
            if self.build_index_csv.get() and index_rows_acc:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                csv_path = os.path.join(self.out_dir.get(), f"indice_links_{stamp}.csv")
                try:
                    with open(csv_path, "w", encoding="utf-8", newline="") as f:
                        w = csv.writer(f, delimiter=";")
                        w.writerow(["pessoa", "tipo", "ordem", "url"])
                        w.writerows(index_rows_acc)
                    self._append_log(f"📄 CSV-índice gerado: {csv_path}")
                except Exception as e:
                    self._append_log(f"⚠ Falha ao gerar CSV-índice: {e}")
            self.btn_start.config(state="normal"); self.btn_stop.config(state="disabled")
            self._append_log("=== Finalizado ===")
        self.after(0, finish)

    def _append_log(self, msg: str):
        self.log_txt.config(state="normal"); self.log_txt.insert("end", msg + "\n"); self.log_txt.see("end"); self.log_txt.config(state="disabled")

    def _poll_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait(); self._append_log(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _persist_settings(self):
        data = {
            "out_dir": self.out_dir.get(),
            "n_top": int(self.n_top.get()),
            "include_news": bool(self.include_news.get()),
            "include_org": bool(self.include_org.get()),
            "build_index_csv": bool(self.build_index_csv.get()),
        }
        save_settings(data)

def safe_filename(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
    return base or "resultado"

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: return {}
    return {}

def save_settings(data: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

if __name__ == "__main__":
    app = App()
    app.mainloop()
