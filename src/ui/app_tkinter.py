import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src.core.search_router import init_db
from src.workers.worker import BuscadorWorker
from src.domain.dorks import get_reasons
from src.services.settings import load_settings, save_settings

APP_TITLE = "Buscador OSINT Avançado — Identidade, Dorks e Motivo da Pesquisa"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x860")
        self.minsize(1120, 760)

        self.log_q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None

        init_db()

        self.settings = load_settings()
        self.reason_map = {item["label"]: item["key"] for item in get_reasons()}
        self.reason_labels = list(self.reason_map.keys())

        self._build_variables()
        self._build_layout()
        self._load_settings_into_form()

        self.after(150, self._poll_log_queue)

    def _build_variables(self):
        self.var_out_dir = tk.StringVar()
        self.var_n_top = tk.StringVar(value="5")

        self.var_include_news = tk.BooleanVar(value=True)
        self.var_include_org = tk.BooleanVar(value=True)
        self.var_build_index_csv = tk.BooleanVar(value=True)
        self.var_export_pdf = tk.BooleanVar(value=False)

        self.var_reason_label = tk.StringVar()
        self.var_cpf = tk.StringVar()
        self.var_city = tk.StringVar()
        self.var_state = tk.StringVar()
        self.var_party = tk.StringVar()
        self.var_role = tk.StringVar()
        self.var_organization = tk.StringVar()
        self.var_company = tk.StringVar()
        self.var_aliases = tk.StringVar()

        self.var_progress_label = tk.StringVar(value="Pronto.")
        self.var_status = tk.StringVar(value="Aguardando início.")

    def _build_layout(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        root = ttk.Frame(self, padding=10)
        root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=2)
        root.columnconfigure(1, weight=3)
        root.rowconfigure(0, weight=1)
        root.rowconfigure(1, weight=0)

        left = ttk.LabelFrame(root, text="Configuração da Pesquisa", padding=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        left.columnconfigure(1, weight=1)

        right = ttk.LabelFrame(root, text="Execução e Log", padding=12)
        right.grid(row=0, column=1, sticky="nsew", pady=(0, 8))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        footer = ttk.Frame(root)
        footer.grid(row=1, column=0, columnspan=2, sticky="ew")
        footer.columnconfigure(0, weight=1)

        row = 0

        ttk.Label(left, text="Nomes (1 por linha):").grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        self.txt_names = tk.Text(left, height=12, wrap="word")
        self.txt_names.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(4, 10))
        row += 1

        ttk.Label(left, text="Pasta de saída:").grid(row=row, column=0, sticky="w", pady=2)
        out_frame = ttk.Frame(left)
        out_frame.grid(row=row, column=1, sticky="ew", pady=2)
        out_frame.columnconfigure(0, weight=1)

        ttk.Entry(out_frame, textvariable=self.var_out_dir).grid(row=0, column=0, sticky="ew")
        ttk.Button(out_frame, text="Selecionar...", command=self._pick_out_dir).grid(row=0, column=1, padx=(6, 0))
        row += 1

        ttk.Label(left, text="Motivo da pesquisa:").grid(row=row, column=0, sticky="w", pady=2)
        self.cmb_reason = ttk.Combobox(
            left,
            textvariable=self.var_reason_label,
            values=self.reason_labels,
            state="readonly",
        )
        self.cmb_reason.grid(row=row, column=1, sticky="ew", pady=2)
        row += 1

        ttk.Label(left, text="Máximo por seção (n_top):").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(left, textvariable=self.var_n_top).grid(row=row, column=1, sticky="ew", pady=2)
        row += 1

        options_box = ttk.LabelFrame(left, text="Opções", padding=8)
        options_box.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 10))
        options_box.columnconfigure(0, weight=1)
        options_box.columnconfigure(1, weight=1)

        ttk.Checkbutton(options_box, text="Incluir notícias", variable=self.var_include_news).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(options_box, text="Incluir orgânicos", variable=self.var_include_org).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(options_box, text="Gerar índice CSV", variable=self.var_build_index_csv).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(options_box, text="Exportar PDF", variable=self.var_export_pdf).grid(row=1, column=1, sticky="w")
        row += 1

        filters_box = ttk.LabelFrame(left, text="Filtros anti-homônimo / identidade", padding=8)
        filters_box.grid(row=row, column=0, columnspan=2, sticky="ew")
        filters_box.columnconfigure(1, weight=1)
        filters_box.columnconfigure(3, weight=1)

        r = 0
        ttk.Label(filters_box, text="CPF:").grid(row=r, column=0, sticky="w", pady=2)
        ttk.Entry(filters_box, textvariable=self.var_cpf).grid(row=r, column=1, sticky="ew", pady=2, padx=(4, 10))
        ttk.Label(filters_box, text="Cidade:").grid(row=r, column=2, sticky="w", pady=2)
        ttk.Entry(filters_box, textvariable=self.var_city).grid(row=r, column=3, sticky="ew", pady=2, padx=(4, 0))
        r += 1

        ttk.Label(filters_box, text="UF:").grid(row=r, column=0, sticky="w", pady=2)
        ttk.Entry(filters_box, textvariable=self.var_state).grid(row=r, column=1, sticky="ew", pady=2, padx=(4, 10))
        ttk.Label(filters_box, text="Partido:").grid(row=r, column=2, sticky="w", pady=2)
        ttk.Entry(filters_box, textvariable=self.var_party).grid(row=r, column=3, sticky="ew", pady=2, padx=(4, 0))
        r += 1

        ttk.Label(filters_box, text="Cargo:").grid(row=r, column=0, sticky="w", pady=2)
        ttk.Entry(filters_box, textvariable=self.var_role).grid(row=r, column=1, sticky="ew", pady=2, padx=(4, 10))
        ttk.Label(filters_box, text="Órgão:").grid(row=r, column=2, sticky="w", pady=2)
        ttk.Entry(filters_box, textvariable=self.var_organization).grid(row=r, column=3, sticky="ew", pady=2, padx=(4, 0))
        r += 1

        ttk.Label(filters_box, text="Empresa:").grid(row=r, column=0, sticky="w", pady=2)
        ttk.Entry(filters_box, textvariable=self.var_company).grid(row=r, column=1, sticky="ew", pady=2, padx=(4, 10))
        ttk.Label(filters_box, text="Aliases (separe por vírgula):").grid(row=r, column=2, sticky="w", pady=2)
        ttk.Entry(filters_box, textvariable=self.var_aliases).grid(row=r, column=3, sticky="ew", pady=2, padx=(4, 0))
        row += 1

        action_box = ttk.Frame(left)
        action_box.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        action_box.columnconfigure(0, weight=1)
        action_box.columnconfigure(1, weight=1)
        action_box.columnconfigure(2, weight=1)
        action_box.columnconfigure(3, weight=1)

        ttk.Button(action_box, text="Salvar configuração", command=self._save_form_settings).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(action_box, text="Recarregar", command=self._reload_settings).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(action_box, text="Iniciar busca", command=self._start_search).grid(row=0, column=2, sticky="ew", padx=4)
        ttk.Button(action_box, text="Parar", command=self._stop_search).grid(row=0, column=3, sticky="ew", padx=(4, 0))

        top_info = ttk.Frame(right)
        top_info.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top_info.columnconfigure(0, weight=1)

        ttk.Label(top_info, textvariable=self.var_status).grid(row=0, column=0, sticky="w")
        ttk.Label(top_info, textvariable=self.var_progress_label).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.progress = ttk.Progressbar(right, mode="determinate")
        self.progress.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        self.txt_log = tk.Text(right, wrap="word")
        self.txt_log.grid(row=1, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(right, orient="vertical", command=self.txt_log.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=scroll.set)

        ttk.Label(
            footer,
            text="Dica: preencha cidade, UF, cargo, partido, órgão ou empresa para reduzir homônimos e melhorar o score de identidade.",
        ).grid(row=0, column=0, sticky="w")

    def _pick_out_dir(self):
        selected = filedialog.askdirectory(title="Selecione a pasta de saída")
        if selected:
            self.var_out_dir.set(selected)

    def _append_log(self, msg: str):
        self.txt_log.insert("end", msg + "\n")
        self.txt_log.see("end")

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.after(150, self._poll_log_queue)

    def _progress_cb(self, done: int, total: int):
        total = max(total, 1)
        value = int((done / total) * 100)
        self.progress["value"] = value
        self.var_progress_label.set(f"Progresso: {done}/{total} ({value}%)")

    def _done_cb(self):
        self.var_status.set("Execução finalizada.")
        self.worker = None
        self.stop_event.clear()

    def _parse_aliases(self):
        raw = self.var_aliases.get().strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _collect_settings_from_form(self):
        reason_label = self.var_reason_label.get().strip()
        reason_key = self.reason_map.get(reason_label, "fraudes")

        try:
            n_top = int(self.var_n_top.get().strip() or "5")
        except ValueError:
            n_top = 5

        data = {
            "out_dir": self.var_out_dir.get().strip() or "./saidas",
            "n_top": max(1, n_top),
            "include_news": bool(self.var_include_news.get()),
            "include_org": bool(self.var_include_org.get()),
            "build_index_csv": bool(self.var_build_index_csv.get()),
            "export_pdf": bool(self.var_export_pdf.get()),
            "reason": reason_key,
            "filters": {
                "cpf": self.var_cpf.get().strip(),
                "city": self.var_city.get().strip(),
                "state": self.var_state.get().strip(),
                "uf": self.var_state.get().strip(),
                "party": self.var_party.get().strip(),
                "role": self.var_role.get().strip(),
                "organization": self.var_organization.get().strip(),
                "company": self.var_company.get().strip(),
                "aliases": self._parse_aliases(),
            },
        }
        return data

    def _save_form_settings(self):
        data = self._collect_settings_from_form()
        save_settings(data)
        self.settings = data
        messagebox.showinfo("Configuração", "Configurações salvas com sucesso.")

    def _reload_settings(self):
        self.settings = load_settings()
        self._load_settings_into_form()
        messagebox.showinfo("Configuração", "Configurações recarregadas.")

    def _load_settings_into_form(self):
        st = self.settings or {}
        filters = st.get("filters", {}) or {}

        self.var_out_dir.set(st.get("out_dir", "./saidas"))
        self.var_n_top.set(str(st.get("n_top", 5)))

        self.var_include_news.set(bool(st.get("include_news", True)))
        self.var_include_org.set(bool(st.get("include_org", True)))
        self.var_build_index_csv.set(bool(st.get("build_index_csv", True)))
        self.var_export_pdf.set(bool(st.get("export_pdf", False)))

        reason_key = st.get("reason", "fraudes")
        label = next((label for label, key in self.reason_map.items() if key == reason_key), None)
        self.var_reason_label.set(label or (self.reason_labels[0] if self.reason_labels else ""))

        self.var_cpf.set(filters.get("cpf", ""))
        self.var_city.set(filters.get("city", ""))
        self.var_state.set(filters.get("state", filters.get("uf", "")))
        self.var_party.set(filters.get("party", ""))
        self.var_role.set(filters.get("role", ""))
        self.var_organization.set(filters.get("organization", ""))
        self.var_company.set(filters.get("company", ""))

        aliases = filters.get("aliases", [])
        if isinstance(aliases, list):
            self.var_aliases.set(", ".join(aliases))
        else:
            self.var_aliases.set(str(aliases or ""))

    def _get_names(self):
        raw = self.txt_names.get("1.0", "end").strip()
        names = []
        for line in raw.splitlines():
            item = line.strip()
            if item:
                names.append(item)
        return names

    def _start_search(self):
        if self.worker is not None:
            messagebox.showwarning("Execução em andamento", "Já existe uma busca em execução.")
            return

        names = self._get_names()
        if not names:
            messagebox.showwarning("Nomes", "Informe ao menos um nome, um por linha.")
            return

        data = self._collect_settings_from_form()
        out_dir = data.get("out_dir") or "./saidas"

        if not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        save_settings(data)
        self.settings = data

        self.stop_event.clear()
        self.progress["value"] = 0
        self.var_progress_label.set("Progresso: 0/0 (0%)")
        self.var_status.set("Executando busca...")
        self._append_log("=" * 70)
        self._append_log("Iniciando varredura OSINT...")

        self.worker = BuscadorWorker(
            names=names,
            out_dir=out_dir,
            log_q=self.log_q,
            progress_cb=self._progress_cb,
            done_cb=self._done_cb,
            stop_event=self.stop_event,
        )
        self.worker.start()

    def _stop_search(self):
        if self.worker is None:
            messagebox.showinfo("Parar", "Não há busca em execução.")
            return
        self.stop_event.set()
        self.var_status.set("Solicitação de parada enviada...")
        self._append_log("⏹ Solicitação de parada enviada.")

def run_app():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    run_app()