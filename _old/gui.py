import os, re, json, time, threading, queue, csv, sys
from datetime import datetime
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import trafilatura
from dotenv import load_dotenv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# =========================
# Config e Utilidades
# =========================
APP_TITLE = "Buscador Rápido — Notícias + Orgânicos por pessoa"
SETTINGS_FILE = "settings.json"

load_dotenv()
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SEARCH_ENDPOINT = "https://google.serper.dev/search"
HEADERS = {
    "X-API-KEY": SERPER_API_KEY or "",
    "Content-Type": "application/json",
}
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

def serper_search(query: str) -> dict:
    if not SERPER_API_KEY:
        raise RuntimeError("Defina SERPER_API_KEY no .env")
    payload = {"q": query, "num": 10, "gl": "br", "hl": "pt-BR"}
    resp = requests.post(SEARCH_ENDPOINT, headers=HEADERS, data=json.dumps(payload), timeout=30)
    resp.raise_for_status()
    return resp.json()

def extract_main_text(url: str) -> str:
    # 1) Tenta trafilatura (conteúdo principal)
    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=True)
        if downloaded:
            extracted = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                favor_recall=True,
            )
            if extracted and len(extracted) > 400:
                return clean_text(extracted)
    except Exception:
        pass
    # 2) Fallback: BeautifulSoup (texto visível)
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for bad in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
            bad.decompose()
        text = soup.get_text("\n", strip=True)
        if len(text) > 50000:
            text = text[:50000]
        return clean_text(text)
    except Exception:
        return ""

def top_n_urls(results: dict, n=3, use_news=True, use_org=True):
    urls_news, urls_org = [], []
    if use_news and isinstance(results.get("news"), list):
        for item in results["news"]:
            link = item.get("link")
            if link:
                urls_news.append(link)
    if use_org and isinstance(results.get("organic"), list):
        for item in results["organic"]:
            link = item.get("link")
            if link:
                urls_org.append(link)

    def dedup_keep_order(seq):
        seen = set(); out = []
        for u in seq:
            if u not in seen:
                seen.add(u); out.append(u)
        return out

    return dedup_keep_order(urls_news)[:n], dedup_keep_order(urls_org)[:n]

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


# =========================
# Worker (thread)
# =========================
class BuscadorWorker(threading.Thread):
    def __init__(self, names, out_dir, log_q, progress_cb, done_cb, stop_event,
                 sleep_between=0.6, n_top=3, include_news=True, include_org=True,
                 build_index_csv=False, index_rows_acc=None):
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

    def log(self, msg: str):
        self.log_q.put(msg)

    def run(self):
        total = len(self.names)
        completed = 0
        try:
            for name in self.names:
                if self.stop_event.is_set():
                    self.log("➡ Execução cancelada pelo usuário.")
                    break
                name_disp = name.strip()
                if not name_disp:
                    completed += 1
                    self.progress_cb(completed, total)
                    continue

                self.log(f"\n🔎 Buscando: {name_disp}")
                try:
                    results = serper_search(name_disp)
                except Exception as e:
                    self.log(f"  ⚠ Erro na busca: {e}")
                    completed += 1
                    self.progress_cb(completed, total)
                    continue

                news_urls, org_urls = top_n_urls(
                    results, n=self.n_top,
                    use_news=self.include_news,
                    use_org=self.include_org
                )
                self.log(f"  • Notícias: {len(news_urls)} | Busca Orgânica: {len(org_urls)}")

                if self.build_index_csv:
                    for i, u in enumerate(news_urls, 1):
                        self.index_rows_acc.append([name_disp, "noticia", i, u])
                    for i, u in enumerate(org_urls, 1):
                        self.index_rows_acc.append([name_disp, "organico", i, u])

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

                concatenado = [f"# Texto concatenado — {name_disp}\n"]
                if not self.stop_event.is_set():
                    for u in news_urls + org_urls:
                        txt = extract_main_text(u)
                        if txt:
                            concatenado.append(txt)
                        time.sleep(self.sleep_between)

                header_text = "\n".join(blocks).strip()
                full_concat = "\n\n".join(concatenado).strip()

                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                fname = f"{safe_filename(name_disp)}_{stamp}.txt"
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

        # Inicia maximizado e redimensionável
        try:
            self.state("zoomed")  # Windows / Tk 8.6+
        except Exception:
            pass
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

    # -------- Helpers de tamanho --------
    def toggle_maximize(self):
        try:
            if self.state() == "zoomed":
                self.state("normal")
            else:
                self.state("zoomed")
        except Exception:
            # fallback: alterna pseudo-fullscreen
            self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def restore_window(self):
        try:
            self.state("normal")
            self.attributes("-fullscreen", False)
        except Exception:
            pass

    # ---------------- UI ----------------
    def _build_ui(self):
        # Cabeçalho
        header = ttk.Frame(self, padding=(12, 12, 12, 6))
        header.pack(fill="x")
        ttk.Label(header, text="Buscador rápido", font=("Segoe UI", 14, "bold")).pack(side="left")
        ttk.Label(header, text=" — cole nomes, escolha as opções e gere um arquivo por pessoa.").pack(side="left")

        # Seleção de pasta
        dir_frame = ttk.Frame(self, padding=(12, 6, 12, 6))
        dir_frame.pack(fill="x")
        ttk.Label(dir_frame, text="Pasta de saída:").pack(side="left")
        self.out_entry = ttk.Entry(dir_frame, textvariable=self.out_dir)
        self.out_entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(dir_frame, text="Escolher…", command=self.choose_dir).pack(side="left", padx=(0,8))
        ttk.Button(dir_frame, text="Abrir pasta", command=self.open_dir).pack(side="left")

        # Corpo (split)
        body = ttk.Frame(self, padding=(12, 6, 12, 12))
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0,6))

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(6,0))

        # Nomes
        ttk.Label(left, text="Nomes (um por linha):").pack(anchor="w")
        self.names_txt = tk.Text(left, height=16, wrap="none", undo=True)
        self.names_txt.pack(fill="both", expand=True)

        # Placeholder
        self.placeholder = "Ex.: Maria Silva\nJoão Pereira\n…"
        self._placeholder_active = True
        self._set_placeholder()
        self.names_txt.bind("<FocusIn>", self._on_focus_in)
        self.names_txt.bind("<FocusOut>", self._on_focus_out)

        # Ações
        actions = ttk.Frame(left)
        actions.pack(fill="x", pady=(6,0))
        ttk.Button(actions, text="Colar da área de transferência", command=self.paste_clip).pack(side="left")
        ttk.Button(actions, text="Importar .txt", command=self.import_txt).pack(side="left", padx=(8,0))
        ttk.Button(actions, text="Limpar", command=self.clear_names).pack(side="left", padx=(8,0))

        # Opções
        opts = ttk.LabelFrame(right, text="Opções", padding=10)
        opts.pack(fill="x")

        row1 = ttk.Frame(opts)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="Resultados por tipo (1–10):").pack(side="left")
        self.top_spin = ttk.Spinbox(row1, from_=1, to=10, textvariable=self.n_top, width=5)
        self.top_spin.pack(side="left", padx=(6,12))

        self.cb_news = ttk.Checkbutton(row1, text="Incluir Notícias", variable=self.include_news)
        self.cb_news.pack(side="left", padx=(0,12))

        self.cb_org = ttk.Checkbutton(row1, text="Incluir Buscas Orgânicas", variable=self.include_org)
        self.cb_org.pack(side="left")

        row2 = ttk.Frame(opts)
        row2.pack(fill="x", pady=2)
        self.cb_index = ttk.Checkbutton(row2, text="Gerar CSV-índice dos links", variable=self.build_index_csv)
        self.cb_index.pack(side="left")

        # Log
        log_box = ttk.LabelFrame(right, text="Log", padding=10)
        log_box.pack(fill="both", expand=True, pady=(8,0))
        self.log_txt = tk.Text(log_box, height=14, state="disabled", wrap="word")
        self.log_txt.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_box, command=self.log_txt.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_txt.config(yscrollcommand=log_scroll.set)

        # Rodapé
        bottom = ttk.Frame(self, padding=(12, 6, 12, 12))
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(fill="x", side="left", expand=True)
        self.progress_label = ttk.Label(bottom, text="0/0", width=6, anchor="e")
        self.progress_label.pack(side="left", padx=(6,12))
        self.btn_start = ttk.Button(bottom, text="Iniciar", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bottom, text="Cancelar", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(8,0))

    # -------- placeholder --------
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

    # -------- ações nomes --------
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
            filetypes=[("Texto", "*.txt"), ("Todos os arquivos", "*.*")]
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

    # -------- pasta --------
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

    # -------- execução --------
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
        if not SERPER_API_KEY:
            messagebox.showerror("API Key ausente", "Defina SERPER_API_KEY no arquivo .env.")
            return

        # Valida opções
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

        # Persist settings
        self._persist_settings()

        # Reset UI
        self.stop_event.clear()
        self.total_names = len(names)
        self.done_count = 0
        self.progress["value"] = 0
        self._set_progress_label(0, self.total_names)
        self._append_log("=== Iniciando ===")

        # Bloqueia/Libera botões
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")

        # CSV índice opcional
        index_rows_acc = [] if self.build_index_csv.get() else None

        # Inicia worker
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
            index_rows_acc=index_rows_acc
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
        # chamado pelo worker ao final
        def finish():
            # Gera CSV índice se habilitado
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
        # drena a fila de logs a cada 100ms
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
        save_settings(data)

if __name__ == "__main__":
    app = App()
    app.mainloop()
