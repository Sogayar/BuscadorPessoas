import os, sys, time, json, re
from datetime import datetime
from typing import List, Tuple, Dict, Any

import requests
from bs4 import BeautifulSoup
import trafilatura

from search_router import init_db, QuotaAwareRouter  # usa nosso roteador
from urllib.parse import urlparse

# =========================
# Heurísticas de URLs (filtro/ranqueamento)
# =========================
BLOCKED_DOMAINS = {
    "instagram.com", "www.instagram.com",
    "x.com", "twitter.com", "mobile.twitter.com",
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "tiktok.com", "www.tiktok.com",
    "facebook.com", "m.facebook.com", "www.facebook.com",
    "threads.net", "linktr.ee", "beacons.ai",
    # notas: "loja/store/shop" NÃO são domínios; tratamos como heurística abaixo
}

ALLOWED_TLDS_SUFFIX = (".gov.br", ".jus.br", ".leg.br")  # órgãos oficiais
ALLOWED_KEY_DOMAINS = {
    "g1.globo.com", "oglobo.globo.com", "www1.folha.uol.com.br", "www.folha.uol.com.br",
    "www.estadao.com.br", "istoe.com.br", "veja.abril.com.br", "www.bbc.com",
    "www.cnnbrasil.com.br", "www.uol.com.br", "www.metropoles.com",
    "time.com"
}
KEYWORDS_ALLOWED_IN_PATH = {"biografia", "biography", "perfil", "profile", "quem-e", "quem-é", "sobre", "about"}

def is_low_signal_url(url: str) -> bool:
    try:
        u = urlparse(url)
        host = (u.netloc or "").lower()
        path = (u.path or "").lower()
        # bloco direto por host conhecido
        if host in BLOCKED_DOMAINS:
            return True
        # heurística de "loja" (no host OU no path)
        if any(tok in host for tok in ("loj", "shop", "store")) or any(tok in path for tok in ("loj", "shop", "store")):
            return True
        # páginas sociais genéricas
        if host.endswith(("x.com", "twitter.com")) and ("/status/" not in path):
            return True
        if host.endswith("youtube.com") and not ("/watch" in path or "/channel" in path):
            return True
        # encurtadores
        if host in {"bit.ly", "tinyurl.com", "lnkd.in"}:
            return True
    except Exception:
        return True
    return False

def is_preferable_url(url: str) -> bool:
    u = urlparse(url)
    host = (u.netloc or "").lower()
    path = (u.path or "").lower()
    if host.endswith(ALLOWED_TLDS_SUFFIX):
        return True
    if host in ALLOWED_KEY_DOMAINS:
        return True
    if any(k in path for k in KEYWORDS_ALLOWED_IN_PATH):
        return True
    return False

UA = {"User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")}

# =========================
# Utilidades de texto / extração
# =========================
RELATED_PATTERNS = (
    r"^\s*(veja também|leia também|relacionadas|relacionados|materiais relacionados)\b.*",
    r"^\s*(related|read more|more from|you might also like|newsletter|sign up)\b.*",
    r"^\s*(mais lidas|mais vistas|mais lidos)\b.*",
)
REL_RE = re.compile("|".join(RELATED_PATTERNS), flags=re.IGNORECASE)

def clean_text(txt: str) -> str:
    if not txt:
        return ""
    txt = re.sub(r"\s+\n", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    return txt.strip()

def _trim_related_sections(text: str) -> str:
    lines = text.splitlines()
    out = []
    for ln in lines:
        if REL_RE.match(ln.strip()):
            break
        if ln.strip().startswith(("-", "•")) and len(ln.strip()) < 80:
            continue
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
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for bad in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
            bad.decompose()
        for cls in ["related", "more", "newsletter", "most-read", "mais-lidas"]:
            for el in soup.select(f".{cls}"):
                el.decompose()

        text = soup.get_text("\n", strip=True)
        if len(text) > 50000:
            text = text[:50000]
        text = clean_text(text)
        text = _trim_related_sections(text)
        if person_name:
            text = _keep_person_paragraphs(text, person_name)
        return text
    except Exception:
        return ""

# =========================
# Ranking
# =========================
def _score_url_for_person(url: str, person_name: str, extracted_preview: str = "") -> int:
    score = 0
    if is_preferable_url(url):
        score += 10
    txt = extracted_preview or ""
    if txt:
        score += min(len(txt) // 100, 30)
        name = person_name.lower()
        mentions = txt.lower().count(name)
        parts = [p for p in name.split() if p]
        if len(parts) >= 2:
            last = parts[-1]
            mentions += txt.lower().count(f" {last} ")
        score += 3 * min(mentions, 5)
    return score

def filter_and_rank_urls(urls: list[str], person_name: str, n: int) -> list[str]:
    # remove baixa qualidade
    urls = [u for u in urls if not is_low_signal_url(u)]
    # preview leve para ranquear
    previews = {}
    for u in urls[:12]:
        try:
            d = trafilatura.fetch_url(u, no_ssl=True)
            previews[u] = (trafilatura.extract(d, favor_precision=True) or "")[:2000] if d else ""
        except Exception:
            previews[u] = ""
    scored = sorted(urls, key=lambda u: _score_url_for_person(u, person_name, previews.get(u, "")), reverse=True)
    return scored[:n]

def _dedup(seq: List[str]) -> List[str]:
    seen, out = set(), []
    for u in seq:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out

# =========================
# Normalização de payloads por provedor
# =========================
def pick_news_urls(payload: Dict[str, Any], n: int = 3) -> List[str]:
    out = []
    if isinstance(payload.get("news"), list):
        for it in payload["news"]:
            link = it.get("link") or it.get("url")
            if link:
                out.append(link)
    if not out:
        if isinstance(payload.get("news_results"), list):
            for it in payload["news_results"]:
                link = it.get("url") or it.get("link")
                if link:
                    out.append(link)
        elif isinstance(payload.get("top_stories"), list):
            for it in payload["top_stories"]:
                link = it.get("url") or it.get("link")
                if link:
                    out.append(link)
    return _dedup(out)[:n]

def pick_organic_urls(payload: Dict[str, Any], n: int = 3) -> List[str]:
    out = []
    if isinstance(payload.get("items"), list):  # Google CSE
        for it in payload["items"]:
            link = it.get("link")
            if link:
                out.append(link)
    if isinstance(payload.get("organic"), list):  # Serper / Zenserp
        for it in payload["organic"]:
            link = it.get("link") or it.get("url")
            if link:
                out.append(link)
    if isinstance(payload.get("organic_results"), list):  # Serpstack
        for it in payload["organic_results"]:
            link = it.get("url") or it.get("link")
            if link:
                out.append(link)
    return _dedup(out)[:n]

# =========================
# Construção do relatório
# =========================
def build_output(query: str, news_urls: List[str], org_urls: List[str]) -> Tuple[str, str]:
    blocks = [f"# Buscador rápido — consulta: {query}\n"]

    # helper que extrai e descarta textos muito curtos ou sem menção
    def extract_valid(u: str) -> str:
        tx = extract_main_text(u, person_name=query)
        # descarta textos com < 300 chars OU sem menção ao nome
        if not tx or len(tx) < 300:
            return ""
        name = query.lower()
        parts = [p for p in name.split() if p]
        last = parts[-1] if len(parts) >= 2 else None
        if (name not in tx.lower()) and (not last or f" {last} " not in tx.lower()):
            return ""
        return tx

    if news_urls:
        blocks.append("## Notícias (top 3)\n")
        rank = 1
        for u in news_urls:
            texto = extract_valid(u)
            if not texto:
                continue
            blocks.append(f"### Notícia {rank}\nURL: {u}\n\n{texto}\n")
            rank += 1
            time.sleep(0.5)

    if org_urls:
        blocks.append("## Resultados orgânicos (top 3)\n")
        rank = 1
        for u in org_urls:
            texto = extract_valid(u)
            if not texto:
                continue
            blocks.append(f"### Link {rank}\nURL: {u}\n\n{texto}\n")
            rank += 1
            time.sleep(0.5)

    # Texto concatenado (somente os válidos)
    concatenado = [f"# Texto concatenado — {query}\n"]
    for u in news_urls + org_urls:
        txt = extract_valid(u)
        if txt:
            concatenado.append(txt)
    full_concat = "\n\n".join(concatenado).strip()

    return "\n".join(blocks).strip(), full_concat

# =========================
# Main
# =========================
def main():
    if len(sys.argv) < 2:
        print('Uso: python buscador_quota.py "Getúlio Vargas"')
        sys.exit(1)

    query = " ".join(sys.argv[1:]).strip()

    init_db()
    router = QuotaAwareRouter()
    result = router.search(query, user_id="cli")
    payload = result["response"]

    news_urls_raw = pick_news_urls(payload, n=10)
    org_urls_raw  = pick_organic_urls(payload, n=10)

    news_urls = filter_and_rank_urls(news_urls_raw, query, n=3)
    org_urls  = filter_and_rank_urls(org_urls_raw,  query, n=3)

    header_text, concatenated = build_output(query, news_urls, org_urls)
    print(header_text)
    print("\n" + "=" * 80 + "\n")
    print(concatenated)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", query)[:60]
    out_path = f"busca_{base}_{stamp}.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header_text)
        f.write("\n\n" + "=" * 80 + "\n\n")
        f.write(concatenated)
    print(f"\n[OK] Resultado salvo em: {out_path}")

if __name__ == "__main__":
    main()
