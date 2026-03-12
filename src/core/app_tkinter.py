import os, re, json, time, threading, queue, csv, sys
from datetime import datetime
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import trafilatura
from dotenv import load_dotenv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from src.core.search_router import init_db, QuotaAwareRouter

APP_TITLE = "Sistema de Investigação OSINT — Auditoria (quota-aware)"
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
    if isinstance(payload.get("items"), list):
        for it in payload["items"]:
            link = it.get("link")
            if link: out.append(link)
    if isinstance(payload.get("organic"), list):
        for it in payload["organic"]:
            link = it.get("link") or it.get("url")
            if link: out.append(link)
    if isinstance(payload.get("organic_results"), list):
        for it in payload["organic_results"]:
            link = it.get("url") or it.get("link")
            if link: out.append(link)
    seen, dedup = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); dedup.append(u)
    return dedup[:n]

def safe_filename(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
    return base or "resultado"

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_settings(data: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

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
        if not tx or len(tx) < 100:
            return ""
        return tx

    def run(self):
        total = len(self.names)
        completed = 0

        try:
            settings = load_settings()
            filters = (settings or {}).get("filters", {}) or {}
            strategy = (settings or {}).get("strategy", {}) or {}

            profile = {
                "city": (filters.get("city") or "").strip(),
                "uf": (filters.get("uf") or "").strip(),
                "role": (filters.get("role") or "").strip(),
                "akas": (filters.get("akas") or "").strip(),
                "party": (filters.get("party") or "").strip(),
                "doc": (filters.get("doc") or "").strip(),
            }

            strategy_mode = (strategy.get("mode") or "Híbrido (recomendado)")
            min_score = int(strategy.get("min_score") or 40)
            enable_clusters = bool(strategy.get("enable_clusters", True))

            for name in self.names:
                if self.stop_event.is_set():
                    self.log("➡ Execução cancelada pelo usuário.")
                    break

                person = name.strip()
                if not person:
                    completed += 1
                    self.progress_cb(completed, total)
                    continue

                self.log(f"\n🔎 Iniciando varredura estratégica: {person}")
                self.log(
                    "   • Perfil aplicado: "
                    f"cidade={profile['city'] or '-'} | "
                    f"uf={profile['uf'] or '-'} | "
                    f"cargo={profile['role'] or '-'} | "
                    f"apelidos={profile['akas'] or '-'} | "
                    f"partido/órgão={profile['party'] or '-'} | "
                    f"doc={profile['doc'] or '-'}"
                )
                self.log(
                    "   • Estratégia: "
                    f"modo={strategy_mode} | min_score={min_score} | clusters={enable_clusters}"
                )





                try:
                    if self.router is None:
                        self.log("⚠ Router não inicializado")
                        blocks = []
                    else:
                        blocks = self.router.search_all_dorks(
                            person,
                            user_id="tk",
                            profile=profile,
                            strategy=strategy_mode
                        )
                except Exception as e:
                    self.log(f"⚠ Erro na execução dos dorks: {e}")
                    blocks = []

                # fallback: notícias gratuitas
                free_news_payload = None
                try:
                    if self.router is None:
                        self.log("⚠ Router não inicializado")
                        free_news = {}
                    else:
                        free_news = self.router.search_news_free(
                            person=person,
                            user_id="tk",
                            max_n=max(6, self.n_top * 2),
                            profile=profile,
                            min_score=min_score
                        )
                    free_news_payload = free_news.get("response", {})
                    self.log(
                        f"   • Fallback notícias gratuitas via "
                        f"{free_news.get('provider', 'desconhecido')}: "
                        f"{len((free_news_payload or {}).get('news', []))} itens"
                    )
                except Exception as e:
                    self.log(f"⚠ Erro no fallback de notícias gratuitas: {e}")






                all_text_blocks = []

                for block in blocks:
                    if self.stop_event.is_set():
                        break

                    category = block.get("category")
                    payload = block.get("response")
                    error = block.get("error")
                    provider = block.get("provider", "desconhecido")

                    self.log(f"\n🔹 Categoria: {category}")
                    self.log(f"Query: {block.get('query')}")
                    self.log(f"Provider: {provider}")

                    if error:
                        self.log(f"   ⚠ Erro: {error}")
                        continue

                    if not payload:
                        self.log("   (payload vazio)")
                        continue

                    news_raw = pick_news_urls(payload, n=10) if self.include_news else []
                    org_raw = pick_organic_urls(payload, n=10) if self.include_org else []

                    urls = news_raw + org_raw
                    urls = list(dict.fromkeys(urls))

                    ranked_urls = filter_and_rank_urls(urls, person, n=self.n_top)

                    self.log(f"   • Links encontrados: {len(ranked_urls)}")

                    rank = 1
                    for u in ranked_urls:
                        if self.stop_event.is_set():
                            break

                        self.log(f"     - Extraindo {rank}: {u}")
                        txt = self._extract_valid(u, person)

                        if not txt:
                            self.log("       (descartado: pouco texto/sem menção)")
                            continue

                        bloco_formatado = (
                            f"\n\n=== CATEGORIA: {str(category).upper()} ===\n"
                            f"URL: {u}\n\n"
                            f"{txt}\n"
                        )
                        all_text_blocks.append(bloco_formatado)

                        if self.build_index_csv:
                            self.index_rows_acc.append([person, str(category), rank, u])

                        rank += 1
                        time.sleep(self.sleep_between)

                final_text = (
                    f"# Relatório Estratégico — {person}\n"
                    f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
                    f"{'='*80}\n"
                    f"Perfil aplicado:\n"
                    f"- Cidade: {profile['city'] or '-'}\n"
                    f"- UF: {profile['uf'] or '-'}\n"
                    f"- Cargo/Função: {profile['role'] or '-'}\n"
                    f"- Apelidos: {profile['akas'] or '-'}\n"
                    f"- Partido/Órgão: {profile['party'] or '-'}\n"
                    f"- Documento: {profile['doc'] or '-'}\n"
                    f"- Estratégia: {strategy_mode}\n"
                    f"- Score mínimo: {min_score}\n"
                    f"- Clusters habilitados: {'sim' if enable_clusters else 'não'}\n"
                    f"{'='*80}\n"
                )

                final_text += "\n".join(all_text_blocks)

                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                fname = f"{safe_filename(person)}_{stamp}.txt"
                fpath = os.path.join(self.out_dir, fname)

                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(final_text)
                    self.log(f"\n✅ Relatório salvo: {fpath}")
                except Exception as e:
                    self.log(f"⚠ Falha ao salvar '{fname}': {e}")

                completed += 1
                self.progress_cb(completed, total)

        finally:
            self.done_cb()

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)

        init_db()
        self.router = QuotaAwareRouter()

        try:
            self.state("zoomed")
        except Exception:
            pass

        self.update_idletasks()
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.minsize(880, 560)

        self.bind("<F11>", lambda e: self.toggle_maximize())
        self.bind("<Escape>", lambda e: self.restore_window())

        try:
            self.style = ttk.Style(self)
            if "vista" in self.style.theme_names():
                self.style.theme_use("vista")
        except Exception:
            pass

        self.stop_event = threading.Event()
        self.worker = None
        self.log_q = queue.Queue()
        self.total_names = 0
        self.done_count = 0

        s = load_settings()
        default_out = s.get("out_dir", os.path.abspath("./saidas"))
        self.out_dir = tk.StringVar(value=default_out)
        self.n_top = tk.IntVar(value=int(s.get("n_top", 3)))
        self.include_news = tk.BooleanVar(value=bool(s.get("include_news", True)))
        self.include_org = tk.BooleanVar(value=bool(s.get("include_org", True)))
        self.build_index_csv = tk.BooleanVar(value=bool(s.get("build_index_csv", False)))

        filters = (s or {}).get("filters", {}) or {}
        strategy = (s or {}).get("strategy", {}) or {}

        self.filter_city = tk.StringVar(value=str(filters.get("city", "")))
        self.filter_uf = tk.StringVar(value=str(filters.get("uf", "")))
        self.filter_role = tk.StringVar(value=str(filters.get("role", "")))
        self.filter_akas = tk.StringVar(value=str(filters.get("akas", "")))
        self.filter_party = tk.StringVar(value=str(filters.get("party", "")))
        self.filter_doc = tk.StringVar(value=str(filters.get("doc", "")))

        self.search_mode = tk.StringVar(value=str(strategy.get("mode", "Híbrido (recomendado)")))
        self.min_score = tk.IntVar(value=int(strategy.get("min_score", 60)))
        self.enable_clusters = tk.BooleanVar(value=bool(strategy.get("enable_clusters", True)))

        os.makedirs(self.out_dir.get(), exist_ok=True)

        self._build_ui()
        self._poll_log()

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
            self.state("normal")
            self.attributes("-fullscreen", False)
        except Exception:
            pass

    def _build_ui(self):
        header = ttk.Frame(self, padding=(12, 12, 12, 6))
        header.pack(fill="x")

        ttk.Label(header, text="Sistema de Investigação OSINT", font=("Segoe UI", 14, "bold")).pack(side="left")
        ttk.Label(header, text=" — auditoria (anti-homônimo) com roteamento de cotas.").pack(side="left")

        dir_frame = ttk.Frame(self, padding=(12, 6, 12, 6))
        dir_frame.pack(fill="x")

        ttk.Label(dir_frame, text="Pasta de saída:").pack(side="left")
        self.out_entry = ttk.Entry(dir_frame, textvariable=self.out_dir)
        self.out_entry.pack(side="left", fill="x", expand=True, padx=8)

        ttk.Button(dir_frame, text="Escolher…", command=self.choose_dir).pack(side="left", padx=(0, 8))
        ttk.Button(dir_frame, text="Abrir pasta", command=self.open_dir).pack(side="left")

        body = ttk.Frame(self, padding=(12, 6, 12, 12))
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))

        ttk.Label(left, text="Investigados (um por linha):").pack(anchor="w")

        self.names_txt = tk.Text(left, height=16, wrap="none", undo=True)
        self.names_txt.pack(fill="both", expand=True)

        self.placeholder = "Ex.: João Silva\nMaria Pereira\n..."
        self._placeholder_active = True
        self._set_placeholder()
        self.names_txt.bind("<FocusIn>", self._on_focus_in)
        self.names_txt.bind("<FocusOut>", self._on_focus_out)

        actions = ttk.Frame(left)
        actions.pack(fill="x", pady=(6, 0))

        ttk.Button(actions, text="Colar da área de transferência", command=self.paste_clip).pack(side="left")
        ttk.Button(actions, text="Importar .txt", command=self.import_txt).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Limpar", command=self.clear_names).pack(side="left", padx=(8, 0))

        lf_profile = ttk.LabelFrame(right, text="Caracterização do investigado (anti-homônimo)", padding=10)
        lf_profile.pack(fill="x")

        r1 = ttk.Frame(lf_profile); r1.pack(fill="x", pady=2)
        ttk.Label(r1, text="Cidade:", width=10).pack(side="left")
        ttk.Entry(r1, textvariable=self.filter_city).pack(side="left", fill="x", expand=True, padx=(0, 10))
        ttk.Label(r1, text="UF:", width=4).pack(side="left")
        ttk.Entry(r1, textvariable=self.filter_uf, width=6).pack(side="left")

        r2 = ttk.Frame(lf_profile); r2.pack(fill="x", pady=2)
        ttk.Label(r2, text="Cargo:", width=10).pack(side="left")
        ttk.Entry(r2, textvariable=self.filter_role).pack(side="left", fill="x", expand=True)

        r3 = ttk.Frame(lf_profile); r3.pack(fill="x", pady=2)
        ttk.Label(r3, text="Apelidos:", width=10).pack(side="left")
        ttk.Entry(r3, textvariable=self.filter_akas).pack(side="left", fill="x", expand=True)

        r4 = ttk.Frame(lf_profile); r4.pack(fill="x", pady=2)
        ttk.Label(r4, text="Partido/Órgão:", width=14).pack(side="left")
        ttk.Entry(r4, textvariable=self.filter_party).pack(side="left", fill="x", expand=True)

        r5 = ttk.Frame(lf_profile); r5.pack(fill="x", pady=2)
        ttk.Label(r5, text="CPF/CNPJ:", width=10).pack(side="left")
        ttk.Entry(r5, textvariable=self.filter_doc).pack(side="left", fill="x", expand=True)

        ttk.Label(
            lf_profile,
            text="Dica: cidade/UF + cargo + apelidos reduzem homônimos. CPF/CNPJ (quando houver) aumenta a precisão.",
            foreground="#444"
        ).pack(anchor="w", pady=(6, 0))

        lf_strategy = ttk.LabelFrame(right, text="Estratégia de busca", padding=10)
        lf_strategy.pack(fill="x", pady=(8, 0))

        s1 = ttk.Frame(lf_strategy); s1.pack(fill="x", pady=2)
        ttk.Label(s1, text="Modo:", width=10).pack(side="left")
        ttk.Combobox(
            s1,
            textvariable=self.search_mode,
            values=["Híbrido (recomendado)", "Precisão (anti-homônimo)", "Amplo (maior cobertura)"],
            state="readonly"
        ).pack(side="left", fill="x", expand=True)

        s2 = ttk.Frame(lf_strategy); s2.pack(fill="x", pady=2)
        ttk.Label(s2, text="Score mín.:", width=10).pack(side="left")
        ttk.Spinbox(s2, from_=0, to=100, textvariable=self.min_score, width=6).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(
            s2,
            text="Agrupar possíveis homônimos (clusters)",
            variable=self.enable_clusters
        ).pack(side="left")

        lf_opts = ttk.LabelFrame(right, text="Opções", padding=10)
        lf_opts.pack(fill="x", pady=(8, 0))

        o1 = ttk.Frame(lf_opts); o1.pack(fill="x", pady=2)
        ttk.Label(o1, text="Resultados por tipo (1–10):").pack(side="left")
        ttk.Spinbox(o1, from_=1, to=10, textvariable=self.n_top, width=5).pack(side="left", padx=(6, 12))
        ttk.Checkbutton(o1, text="Incluir Notícias", variable=self.include_news).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(o1, text="Incluir Buscas Orgânicas", variable=self.include_org).pack(side="left")

        o2 = ttk.Frame(lf_opts); o2.pack(fill="x", pady=2)
        ttk.Checkbutton(o2, text="(Avançado) Gerar CSV-índice dos links", variable=self.build_index_csv).pack(side="left")

        log_box = ttk.LabelFrame(right, text="Log de auditoria", padding=10)
        log_box.pack(fill="both", expand=True, pady=(8, 0))

        self.log_txt = tk.Text(log_box, height=14, state="disabled", wrap="word")
        self.log_txt.pack(side="left", fill="both", expand=True)

        log_scroll = ttk.Scrollbar(log_box, command=self.log_txt.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_txt.config(yscrollcommand=log_scroll.set)

        bottom = ttk.Frame(self, padding=(12, 6, 12, 12))
        bottom.pack(fill="x")

        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(fill="x", side="left", expand=True)

        self.progress_label = ttk.Label(bottom, text="0/0", width=6, anchor="e")
        self.progress_label.pack(side="left", padx=(6, 12))

        self.btn_start = ttk.Button(bottom, text="Iniciar investigação", command=self.on_start)
        self.btn_start.pack(side="left")

        self.btn_stop = ttk.Button(bottom, text="Cancelar", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))

    def _read_profile_from_ui(self) -> dict:
        return {
            "filters": {
                "city": (self.filter_city.get() or "").strip(),
                "uf": (self.filter_uf.get() or "").strip().upper(),
                "role": (self.filter_role.get() or "").strip(),
                "akas": (self.filter_akas.get() or "").strip(),
                "party": (self.filter_party.get() or "").strip(),
                "doc": (self.filter_doc.get() or "").strip(),
            },
            "strategy": {
                "mode": (self.search_mode.get() or "").strip(),
                "min_score": int(self.min_score.get() or 0),
                "enable_clusters": bool(self.enable_clusters.get()),
            }
        }

    def _set_placeholder(self):
        self.names_txt.config(fg="#666")
        self.names_txt.delete("1.0", "end")
        self.names_txt.insert("1.0", self.placeholder)
        self._placeholder_active = True

    def _on_focus_in(self, _):
        if self._placeholder_active:
            self.names_txt.delete("1.0", "end")
            self.names_txt.config(fg="#000")
            self._placeholder_active = False

    def _on_focus_out(self, _):
        content = self.names_txt.get("1.0", "end").strip()
        if not content:
            self._set_placeholder()

    def paste_clip(self):
        try:
            text = self.clipboard_get()
            if self._placeholder_active:
                self.names_txt.delete("1.0", "end")
                self._placeholder_active = False
            self.names_txt.config(fg="#000")
            self.names_txt.insert("end", text)
        except Exception:
            messagebox.showwarning("Atenção", "Não foi possível ler a área de transferência.")

    def import_txt(self):
        path = filedialog.askopenfilename(
            title="Importar nomes de arquivo .txt",
            filetypes=[("Texto","*.txt"),("Todos os arquivos","*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = f.read()
            if self._placeholder_active:
                self.names_txt.delete("1.0", "end")
                self._placeholder_active = False
                self.names_txt.config(fg="#000")
            self.names_txt.insert("end", data if data.endswith("\n") else data + "\n")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao importar: {e}")

    def clear_names(self):
        self.names_txt.delete("1.0", "end")
        self._placeholder_active = False
        self.names_txt.config(fg="#000")

    def choose_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.out_dir.get(), title="Escolha a pasta de saída")
        if chosen:
            self.out_dir.set(chosen)
            self._persist_settings()

    def open_dir(self):
        folder = self.out_dir.get().strip()
        if not os.path.isdir(folder):
            messagebox.showwarning("Atenção", "A pasta configurada não existe.")
            return
        try:
            if os.name == "nt":
                os.startfile(folder)  # type: ignore
            elif sys.platform == "darwin":
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}"')
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível abrir a pasta: {e}")

    def on_start(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Em execução", "Já existe uma execução em andamento.")
            return

        names_raw = self.names_txt.get("1.0", "end")
        if self._placeholder_active:
            names_raw = ""
        names = [n.strip() for n in names_raw.splitlines() if n.strip()]

        if not names:
            messagebox.showwarning("Atenção", "Informe ao menos um nome (um por linha).")
            return

        try:
            n_top_val = int(self.n_top.get())
            if n_top_val < 1 or n_top_val > 10:
                raise ValueError
        except Exception:
            messagebox.showwarning("Atenção", "O número de resultados por tipo deve ser entre 1 e 10.")
            return

        include_news = bool(self.include_news.get())
        include_org = bool(self.include_org.get())
        if not include_news and not include_org:
            messagebox.showwarning("Atenção", "Selecione ao menos um tipo: Notícias ou Orgânicos.")
            return

        out_dir = self.out_dir.get().strip()
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível criar/acessar a pasta de saída: {e}")
            return

        self._persist_settings()

        self.stop_event.clear()
        self.total_names = len(names)
        self.done_count = 0

        self.progress["value"] = 0
        self._set_progress_label(0, self.total_names)
        self._append_log("=== Iniciando ===")

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")

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

            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")
            self._append_log("=== Finalizado ===")
        self.after(0, finish)

    def _append_log(self, msg: str):
        self.log_txt.config(state="normal")
        self.log_txt.insert("end", msg + "\n")
        self.log_txt.see("end")
        self.log_txt.config(state="disabled")

    def _poll_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self._append_log(msg)
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
        data.update(self._read_profile_from_ui())
        save_settings(data)

if __name__ == "__main__":
    app = App()
    app.mainloop()