import csv
import os
import threading
import time
from dataclasses import asdict
from datetime import datetime
from typing import Callable, Dict, List, Optional

from src.core.search_router import QuotaAwareRouter
from src.utils.extract import extract_main_text, safe_filename
from src.domain.identity import IdentityProfile, score_identity_match
from src.utils.pickers import pick_news_items, pick_organic_items
from src.services.settings import load_settings


class BuscadorWorker(threading.Thread):
    def __init__(
        self,
        names: List[str],
        out_dir: str,
        log_q,
        progress_cb: Callable[[int, int], None],
        done_cb: Callable[[], None],
        stop_event,
        router: Optional[QuotaAwareRouter] = None,
    ):
        super().__init__(daemon=True)
        self.settings = load_settings()
        self.names = names
        self.out_dir = out_dir
        self.log_q = log_q
        self.progress_cb = progress_cb
        self.done_cb = done_cb
        self.stop_event = stop_event
        self.router = router or QuotaAwareRouter()

    def log(self, msg: str) -> None:
        self.log_q.put(msg)

    def _build_profile(self, raw_name: str) -> IdentityProfile:
        filters = (self.settings or {}).get("filters", {}) or {}
        aliases = filters.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [a.strip() for a in aliases.split(",") if a.strip()]
        return IdentityProfile(
            full_name=raw_name.strip(),
            aliases=aliases,
            cpf=(filters.get("cpf") or "").strip(),
            city=(filters.get("city") or "").strip(),
            state=(filters.get("state") or filters.get("uf") or "").strip(),
            party=(filters.get("party") or "").strip(),
            role=(filters.get("role") or "").strip(),
            organization=(filters.get("organization") or "").strip(),
            company=(filters.get("company") or "").strip(),
        )

    def _rank_items(self, profile: IdentityProfile, items: List[Dict[str, str]], limit: int) -> List[Dict[str, str]]:
        scored = []
        for item in items:
            score, breakdown = score_identity_match(
                profile,
                item.get("title", ""),
                item.get("snippet", ""),
                item.get("url", ""),
            )
            if score < 0.45:
                continue
            item = {**item, "identity_score": round(score, 3), "identity_breakdown": breakdown}
            scored.append(item)
        scored.sort(key=lambda it: it.get("identity_score", 0), reverse=True)
        return scored[:limit]

    def _save_report(self, profile: IdentityProfile, reason_key: str, report: Dict[str, List[Dict[str, str]]]) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fpath = os.path.join(self.out_dir, f"{safe_filename(profile.full_name)}_{reason_key}_{stamp}.md")

        with open(fpath, "w", encoding="utf-8") as f:
            f.write(f"# Relatório OSINT — {profile.full_name}\n\n")
            f.write(f"Motivo da pesquisa: **{reason_key}**\n\n")
            f.write(f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n\n")
            f.write("## Perfil de identidade\n")
            for k, v in asdict(profile).items():
                if v:
                    f.write(f"- **{k}**: {v}\n")
            f.write("\n")

            for section, items in report.items():
                f.write(f"## {section}\n\n")
                if not items:
                    f.write("Nenhum resultado qualificado.\n\n")
                    continue
                for idx, item in enumerate(items, 1):
                    f.write(f"### {idx}. {item.get('title') or item.get('url')}\n")
                    f.write(f"- URL: {item.get('url','')}\n")
                    f.write(f"- Score de identidade: {item.get('identity_score','')}\n")
                    f.write(f"- Snippet: {item.get('snippet','')}\n\n")
                    if item.get("extracted_text"):
                        f.write(item["extracted_text"][:4000] + "\n\n")
        return fpath

    def _save_index_csv(self, rows: List[List[str]]) -> None:
        fpath = os.path.join(self.out_dir, "indice_buscas.csv")
        write_header = not os.path.exists(fpath)
        with open(fpath, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["nome", "motivo", "tipo", "score", "url"])
            w.writerows(rows)

    def run(self) -> None:
        total = len(self.names)
        reason_key = (self.settings or {}).get("reason", "fraudes")
        n_top = int((self.settings or {}).get("n_top", 5))
        build_index_csv = bool((self.settings or {}).get("build_index_csv", True))
        csv_rows: List[List[str]] = []

        try:
            for idx, raw_name in enumerate(self.names, 1):
                if self.stop_event.is_set():
                    self.log("Execução cancelada.")
                    break

                profile = self._build_profile(raw_name)
                self.log(f"\n🔎 Iniciando varredura estratégica: {profile.full_name}")
                self.log(f"   • Motivo: {reason_key}")

                try:
                    blocks = self.router.search_reason_bundle(profile, reason_key, include_social=True, user_id="tk")
                except Exception as e:
                    self.log(f"   ⚠ Falha no bloco principal de busca: {e}")
                    blocks = []

                try:
                    news = self.router.search_news_free(profile, reason_key, user_id="tk", max_n=n_top * 2)
                    news_items = self._rank_items(profile, pick_news_items(news.get("response", {}), limit=n_top * 2), limit=n_top)
                    news_meta = (news.get("response") or {}).get("meta", {}) or {}
                    if news_meta.get("errors"):
                        for err in news_meta["errors"]:
                            self.log(f"   ⚠ Notícias: {err}")
                except Exception as e:
                    self.log(f"   ⚠ Falha na coleta de notícias: {e}")
                    news_items = []

                organics: List[Dict[str, str]] = []
                socials: List[Dict[str, str]] = []

                for block in blocks:
                    payload = block.get("response") or {}
                    items = self._rank_items(profile, pick_organic_items(payload, limit=n_top * 2), limit=n_top)
                    if block.get("category") == "midia_social":
                        socials.extend(items)
                    else:
                        organics.extend(items)

                report = {
                    "Notícias qualificadas": news_items[:n_top],
                    "Resultados orgânicos qualificados": organics[:n_top],
                    "Redes sociais/perfis públicos": socials[:n_top],
                }

                for group_name, group in report.items():
                    self.log(f"   • {group_name}: {len(group)} item(ns)")
                    for item in group:
                        self.log(f"   • Extraindo: {item.get('url','')}")
                        try:
                            item["extracted_text"] = extract_main_text(item.get("url", ""))
                        except Exception as e:
                            item["extracted_text"] = ""
                            self.log(f"     ⚠ Falha na extração: {e}")

                        if build_index_csv:
                            csv_rows.append([
                                profile.full_name,
                                reason_key,
                                group_name,
                                str(item.get("identity_score", "")),
                                item.get("url", ""),
                            ])
                        time.sleep(0.2)

                fpath = self._save_report(profile, reason_key, report)
                self.log(f"✅ Relatório salvo: {fpath}")
                self.progress_cb(idx, total)

            if build_index_csv and csv_rows:
                self._save_index_csv(csv_rows)
        finally:
            self.done_cb()