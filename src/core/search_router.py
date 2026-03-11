import os, json, hashlib, sqlite3, time, unicodedata, requests, xml.etree.ElementTree as ET
from urllib.parse import quote_plus
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, List
from dotenv import load_dotenv

from src.utils.identity import qualify_news
from src.utils.dorks import get_dorks

# =========================
# CONFIG
# =========================
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOTENV_PATHS = [
    os.path.join(ROOT, ".env"),
    os.path.join(ROOT, "config", ".env"),
]
for p in DOTENV_PATHS:
    if os.path.exists(p):
        load_dotenv(p)
        break
else:
    load_dotenv()

TZ = ZoneInfo("America/Fortaleza")

DB_PATH = os.getenv("SEARCH_DB_PATH", "data/search_quota.sqlite")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

GOOGLE_DAILY_LIMIT      = int(os.getenv("GOOGLE_DAILY_LIMIT", "100"))
SERPSTACK_MONTHLY_LIMIT = int(os.getenv("SERPSTACK_MONTHLY_LIMIT", "100"))
ZENSERP_MONTHLY_LIMIT   = int(os.getenv("ZENSERP_MONTHLY_LIMIT", "50"))
SERPER_FINITE_LIMIT     = int(os.getenv("SERPER_FINITE_LIMIT", "2500"))

# Chaves API
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CX      = os.getenv("GOOGLE_CX", "")
SERPSTACK_KEY  = os.getenv("SERPSTACK_KEY", "")
ZENSERP_KEY    = os.getenv("ZENSERP_KEY", "")
SERPER_KEY     = os.getenv("SERPER_KEY", "")

# Headers HTTP genéricos
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# Cache e anti-duplicação
CACHE_TTL_SECONDS        = int(os.getenv("CACHE_TTL_SECONDS", "604800"))    # 7 dias
ANTI_DUP_WINDOW_SECONDS  = int(os.getenv("ANTI_DUP_WINDOW_SECONDS", "900")) # 15 min

# =========================
# HELPERS DE PERFIL/ESTRATÉGIA
# =========================
def normalize_strategy(strategy: str = "") -> str:
    s = (strategy or "").strip().lower()
    if "precis" in s:
        return "precision"
    if "amplo" in s or "wide" in s:
        return "wide"
    return "hybrid"

def build_profile(
    city: str = "",
    uf: str = "",
    role: str = "",
    akas: str = "",
    party: str = "",
    doc: str = ""
) -> Dict[str, str]:
    return {
        "city": (city or "").strip(),
        "uf": (uf or "").strip(),
        "role": (role or "").strip(),
        "akas": (akas or "").strip(),
        "party": (party or "").strip(),
        "doc": (doc or "").strip(),
    }

# =========================
# DB / LOG
# =========================
def get_conn():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        isolation_level=None,
        check_same_thread=False
    )

    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")

    return conn

def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")

def month_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m")

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS quota_counters (
        provider TEXT PRIMARY KEY,
        period TEXT NOT NULL,          -- daily | monthly | finite
        count INTEGER NOT NULL,
        limit_value INTEGER NOT NULL,
        last_reset TEXT NOT NULL       -- YYYY-MM-DD | YYYY-MM | 'start'
    );
    CREATE TABLE IF NOT EXISTS results_cache (
        qhash TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        response_json TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS audit_logs (
        ts INTEGER NOT NULL,
        provider TEXT,
        event TEXT,
        details TEXT
    );
    CREATE TABLE IF NOT EXISTS search_logs (
        ts INTEGER NOT NULL,
        user_id TEXT,
        query TEXT NOT NULL,
        provider TEXT,
        cache_hit INTEGER NOT NULL DEFAULT 0
    );
    """)
    conn.commit()

    seed_counter(conn, "google",    "daily",   0, GOOGLE_DAILY_LIMIT,      today_str())
    seed_counter(conn, "serpstack", "monthly", 0, SERPSTACK_MONTHLY_LIMIT, month_str())
    seed_counter(conn, "zenserp",   "monthly", 0, ZENSERP_MONTHLY_LIMIT,   month_str())
    seed_counter(conn, "serper",    "finite",  0, SERPER_FINITE_LIMIT,     "start")
    conn.close()

def seed_counter(conn, provider, period, count, limit_value, last_reset):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM quota_counters WHERE provider=?", (provider,))
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO quota_counters(provider, period, count, limit_value, last_reset)
            VALUES(?,?,?,?,?)
        """, (provider, period, count, limit_value, last_reset))
        conn.commit()

def log_event(provider: Optional[str], event: str, details: Dict[str, Any]):
    conn = get_conn()
    conn.execute(
        "INSERT INTO audit_logs(ts, provider, event, details) VALUES(?,?,?,?)",
        (int(time.time()), provider, event, json.dumps(details, ensure_ascii=False))
    )
    conn.close()

def log_search(user_id: Optional[str], query: str, provider: str, cache_hit: bool):
    conn = get_conn()
    conn.execute(
        "INSERT INTO search_logs(ts, user_id, query, provider, cache_hit) VALUES(?,?,?,?,?)",
        (int(time.time()), user_id or "", query, provider, 1 if cache_hit else 0)
    )
    conn.close()

# =========================
# CACHE & DEDUP
# =========================
def normalize_query(q: str) -> str:
    q = q.strip().lower()
    return " ".join(q.split())

def hash_query(q: str) -> str:
    return hashlib.sha256(q.encode("utf-8")).hexdigest()

def cache_get(query: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    qh = hash_query(normalize_query(query))
    cur.execute("SELECT response_json, created_at FROM results_cache WHERE qhash=?", (qh,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    response_json, created_at = row
    if int(time.time()) - int(created_at) > CACHE_TTL_SECONDS:
        conn2 = get_conn()
        conn2.execute("DELETE FROM results_cache WHERE qhash=?", (qh,))
        conn2.close()
        return None
    try:
        return json.loads(response_json)
    except Exception:
        return None

def cache_set(query: str, response: Dict[str, Any]):
    conn = get_conn()
    qh = hash_query(normalize_query(query))
    conn.execute(
        "REPLACE INTO results_cache(qhash, query, response_json, created_at) VALUES(?,?,?,?)",
        (qh, query, json.dumps(response, ensure_ascii=False), int(time.time()))
    )
    conn.close()

def dedup_recent(query: str) -> bool:
    since = int(time.time()) - ANTI_DUP_WINDOW_SECONDS
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM search_logs WHERE ts >= ? AND query = ? LIMIT 1",
        (since, query)
    )
    found = cur.fetchone() is not None
    conn.close()
    return found

# =========================
# QUOTA
# =========================
@dataclass
class QuotaStatus:
    provider: str
    period: str
    count: int
    limit_value: int
    last_reset: str

def _fetch_quota(cur, provider: str) -> Tuple[str, int, int, str]:
    cur.execute("SELECT period, count, limit_value, last_reset FROM quota_counters WHERE provider=?", (provider,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Provider {provider} não configurado.")
    return row

def try_consume(provider: str, n: int = 1) -> Tuple[bool, QuotaStatus]:
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        cur = conn.cursor()
        period, count, limit_value, last_reset = _fetch_quota(cur, provider)

        now_day = today_str()
        now_month = month_str()

        if period == "daily" and last_reset != now_day:
            count = 0
            last_reset = now_day
            cur.execute("UPDATE quota_counters SET count=?, last_reset=? WHERE provider=?", (count, last_reset, provider))
            log_event(provider, "reset_quota", {"period": period, "last_reset": last_reset})

        if period == "monthly" and last_reset != now_month:
            count = 0
            last_reset = now_month
            cur.execute("UPDATE quota_counters SET count=?, last_reset=? WHERE provider=?", (count, last_reset, provider))
            log_event(provider, "reset_quota", {"period": period, "last_reset": last_reset})

        if count + n > limit_value:
            conn.rollback()
            return False, QuotaStatus(provider, period, count, limit_value, last_reset)

        count += n
        cur.execute("UPDATE quota_counters SET count=? WHERE provider=?", (count, provider))
        conn.commit()
        return True, QuotaStatus(provider, period, count, limit_value, last_reset)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# =========================
# PROVIDERS (pagos)
# =========================
class ProviderBase:
    name = "base"
    def search(self, query: str) -> Dict[str, Any]:
        raise NotImplementedError

class GoogleProvider(ProviderBase):
    name = "google"
    def search(self, query: str) -> Dict[str, Any]:
        if not GOOGLE_API_KEY or not GOOGLE_CX:
            raise RuntimeError("Google API KEY ou CX ausentes (.env).")
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CX, "q": query, "num": 10, "lr": "lang_pt"}
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

class SerpstackProvider(ProviderBase):
    name = "serpstack"
    def search(self, query: str) -> Dict[str, Any]:
        if not SERPSTACK_KEY:
            raise RuntimeError("SERPSTACK_KEY ausente (.env).")
        url = "http://api.serpstack.com/search"
        params = {"access_key": SERPSTACK_KEY, "query": query, "num": 10, "gl": "br", "hl": "pt"}
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

class ZenserpProvider(ProviderBase):
    name = "zenserp"
    def search(self, query: str) -> Dict[str, Any]:
        if not ZENSERP_KEY:
            raise RuntimeError("ZENSERP_KEY ausente (.env).")
        url = "https://app.zenserp.com/api/v2/search"
        params = {"q": query, "hl": "pt", "gl": "br", "num": 10}
        headers = {"apikey": ZENSERP_KEY}
        r = requests.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()

class SerperProvider(ProviderBase):
    name = "serper"
    def search(self, query: str) -> Dict[str, Any]:
        if not SERPER_KEY:
            raise RuntimeError("SERPER_KEY ausente (.env).")
        url = "https://google.serper.dev/search"
        headers = {"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"}
        payload = {"q": query, "num": 10, "gl": "br", "hl": "pt"}
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

# =========================
# FREE NEWS SEARCH (Google News RSS + GDELT)
# =========================
def _norm(txt: str) -> str:
    if not txt:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFD", txt.casefold())
        if unicodedata.category(c) != "Mn"
    )

def _parse_gnews_rss_items(xml_bytes: bytes) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    items: List[Dict[str, Any]] = []

    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link  = (it.findtext("link") or "").strip()
        desc  = (it.findtext("description") or "").strip()
        pubd  = (it.findtext("pubDate") or "").strip()

        src = ""
        s1 = it.find("{http://www.w3.org/2005/Atom}source")
        if s1 is not None and s1.text is not None and s1.text.strip():
            src = s1.text.strip()
        s2 = it.find("source")
        if not src and s2 is not None and s2.text is not None and s2.text.strip():
            src = s2.text.strip()

        items.append({
            "title": title,
            "link": link,
            "description": desc,
            "pubDate": pubd,
            "source": src
        })
    return items

def _normalize_news(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    normed = []
    for it in items:
        normed.append({
            "title": it.get("title", "").strip(),
            "link": it.get("link", "").strip(),
            "pubDate": it.get("pubDate", "").strip(),
            "source": it.get("source", "").strip(),
        })
    return {"news": normed}

def _fetch_gnews_aggregated(person: str, max_n: int, domains: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    base = "https://news.google.com/rss/search"
    q_main = f"\"{person}\"" if not (person.startswith('"') and person.endswith('"')) else person
    common = "&hl=pt-BR&gl=BR&ceid=BR:pt-419"

    seen = set()
    aggregated: List[Dict[str, Any]] = []

    def add_from_url(url: str) -> None:
        r = requests.get(url, timeout=30, headers=UA)
        r.raise_for_status()
        for item in _parse_gnews_rss_items(r.content):
            key = item.get("link") or item.get("title")
            if key and key not in seen:
                seen.add(key)
                aggregated.append(item)

    if not domains:
        url = f"{base}?q={quote_plus(q_main)}{common}"
        try:
            add_from_url(url)
        except Exception as e:
            log_event("google_news_rss", "error", {"url": url, "error": str(e)})
    else:
        for d in domains:
            q = f'{q_main} site:{d}'
            url = f"{base}?q={quote_plus(q)}{common}"
            try:
                add_from_url(url)
            except Exception as e:
                log_event("google_news_rss", "error", {"url": url, "error": str(e)})

    meta = {
        "domains_used": domains or [],
        "total_raw": len(aggregated),
    }
    return aggregated[: max_n * 4], meta

def _gdelt_news(person: str, max_n: int = 10) -> Dict[str, Any]:
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {
        "query": f"\"{person}\"",
        "mode": "ArtList",
        "format": "json",
        "maxrecords": max_n * 3,
        "timespan": "30d",
    }
    r = requests.get(url, params=params, timeout=30, headers=UA)
    r.raise_for_status()
    js = r.json()
    items: List[Dict[str, Any]] = []
    for a in (js.get("articles") or []):
        items.append({
            "title": a.get("title") or "",
            "link": a.get("url", ""),
            "description": a.get("excerpt", "") or a.get("title", ""),
            "pubDate": a.get("seendate", ""),
            "source": a.get("domain", "")
        })
    return {"provider": "gdelt", "items": items}

# =========================
# ROUTER
# =========================
class QuotaAwareRouter:
    """
    Ordem de uso (busca paga geral):
      1) Google
      2) Serpstack
      3) Zenserp
      4) Serper

    Notícias gratuitas:
      - Google News RSS
      - Fallback GDELT
    """

    def __init__(self):
        self.providers: Dict[str, "ProviderBase"] = {
            "google":    GoogleProvider(),
            "serpstack": SerpstackProvider(),
            "zenserp":   ZenserpProvider(),
            "serper":    SerperProvider(),
        }

    def search_all_dorks(
        self,
        name: str,
        user_id: Optional[str] = None,
        profile: Optional[Dict[str, str]] = None,
        strategy: str = "hybrid"
    ):
        """
        Executa todos os dorks sequencialmente e retorna lista estruturada por categoria.
        """
        strategy_norm = normalize_strategy(strategy)
        dorks = get_dorks(name, profile=profile or {}, strategy=strategy_norm)
        results = []

        for d in dorks:
            category = d.get("category")
            query = d.get("query")

            if not query:
                results.append({
                    "category": category,
                    "query": query,
                    "error": "query is None or empty"
                })
                continue

            try:
                result = self.search(query, user_id=user_id)
                results.append({
                    "category": category,
                    "query": query,
                    "provider": result.get("provider"),
                    "response": result.get("response"),
                })
            except Exception as e:
                results.append({
                    "category": category,
                    "query": query,
                    "error": str(e)
                })

        return results

    # -------- Busca geral (paga) --------
    def _try_provider(self, provider_key: str, query: str) -> Optional[Dict[str, Any]]:
        ok, st = try_consume(provider_key, 1)
        if not ok:
            log_event(provider_key, "quota_exceeded", {"count": st.count, "limit": st.limit_value})
            return None
        try:
            payload = self.providers[provider_key].search(query)
            log_event(provider_key, "success", {"query": query})
            return {"provider": provider_key, "response": payload}
        except requests.HTTPError as e:
            text = ""
            try:
                text = e.response.text[:500]
            except Exception:
                pass
            log_event(provider_key, "http_error", {"status": getattr(e.response, "status_code", None), "text": text})
            return None
        except Exception as e:
            log_event(provider_key, "error", {"error": str(e)})
            return None

    def search(self, query: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        q_norm = normalize_query(query)

        if dedup_recent(q_norm):
            cached = cache_get(q_norm)
            if cached is not None:
                log_search(user_id, q_norm, "cache", True)
                log_event(None, "anti_dup_cache_hit", {"query": q_norm})
                return {"provider": "cache", "response": cached}
            log_event(None, "anti_dup_no_cache", {"query": q_norm})

        cached = cache_get(q_norm)
        if cached is not None:
            log_search(user_id, q_norm, "cache", True)
            log_event(None, "cache_hit", {"query": q_norm})
            return {"provider": "cache", "response": cached}

        for p in ["google", "serpstack", "zenserp", "serper"]:
            result = self._try_provider(p, q_norm)
            if result is not None:
                cache_set(q_norm, result["response"])
                log_search(user_id, q_norm, result["provider"], False)
                return result

        log_event(None, "all_failed_or_exhausted", {"query": q_norm})
        raise RuntimeError("Sem provedores disponíveis (cotas esgotadas e/ou falhas).")

    # -------- Notícias gratuitas (pessoa) --------
    def search_news_free(
        self,
        person: str,
        user_id: Optional[str] = None,
        max_n: int = 10,
        profile: Optional[Dict[str, str]] = None,
        min_score: int = 40
    ) -> Dict[str, Any]:
        person = (person or "").strip()
        if not person:
            empty = _normalize_news([])
            return {"provider": "none", "response": empty}

        profile = profile or {}
        max_n = max(1, min(20, int(max_n)))
        min_score = max(0, min(100, int(min_score)))

        # cache key contextual
        q_norm = normalize_query(
            f"news:{person}"
            f"|city={profile.get('city','')}"
            f"|uf={profile.get('uf','')}"
            f"|role={profile.get('role','')}"
            f"|akas={profile.get('akas','')}"
            f"|party={profile.get('party','')}"
            f"|doc={profile.get('doc','')}"
            f"|min_score={min_score}"
        )

        cached = cache_get(q_norm)
        if cached is not None:
            log_search(user_id, q_norm, "cache_news", True)
            log_event("news_free", "cache_hit", {"query": q_norm})
            return {"provider": "cache_news", "response": cached}

        br_domains = [
            "g1.globo.com", "oglobo.globo.com", "uol.com.br", "folha.uol.com.br",
            "estadao.com.br", "veja.abril.com.br", "metropoles.com",
            "terra.com.br", "r7.com", "band.uol.com.br", "migalhas.com.br"
        ]

        items, meta = _fetch_gnews_aggregated(person, max_n=max_n, domains=br_domains)

        if len(items) < max_n:
            items2, meta2 = _fetch_gnews_aggregated(person, max_n=max_n, domains=None)
            items = (items or []) + (items2 or [])
            meta["total_raw"] = len(items)
            meta["domains_used"] = list({
                *meta.get("domains_used", []),
                *(meta2.get("domains_used", []) if meta2 else [])
            })

        filtered: List[Dict[str, Any]] = []
        try:
            for it in items:
                if qualify_news(
                    person=person,
                    title=it.get("title", ""),
                    desc=it.get("description", ""),
                    city=profile.get("city", ""),
                    uf=profile.get("uf", ""),
                    role=profile.get("role", ""),
                    akas=profile.get("akas", ""),
                    party=profile.get("party", ""),
                    doc=profile.get("doc", ""),
                    min_score=min_score
                ):
                    filtered.append(it)
        except Exception as e:
            log_event("news_free", "filter_error", {"error": str(e)})

        provider_used = "google_news_rss"
        raw_items_for_debug = items[:10]

        if not filtered:
            try:
                gd = _gdelt_news(person, max_n=max_n)
                provider_used = gd.get("provider", provider_used)
                for it in gd.get("items", []):
                    if qualify_news(
                        person=person,
                        title=it.get("title", ""),
                        desc=it.get("description", ""),
                        city=profile.get("city", ""),
                        uf=profile.get("uf", ""),
                        role=profile.get("role", ""),
                        akas=profile.get("akas", ""),
                        party=profile.get("party", ""),
                        doc=profile.get("doc", ""),
                        min_score=min_score
                    ):
                        filtered.append(it)
                raw_items_for_debug = (gd.get("items", []) or [])[:10]
            except Exception as e:
                log_event("gdelt", "error", {"query": person, "error": str(e)})

        final = _normalize_news(filtered[:max_n])
        final["meta"] = {
            "total_raw": meta.get("total_raw", 0),
            "matched": len(filtered),
            "provider_primary": provider_used,
            "min_score": min_score,
            "profile_used": profile,
        }
        final["raw_items"] = [
            {
                "title": it.get("title", ""),
                "link": it.get("link", ""),
                "pubDate": it.get("pubDate", ""),
                "source": it.get("source", ""),
            }
            for it in raw_items_for_debug
        ]

        cache_set(q_norm, final)
        log_search(user_id, q_norm, provider_used, False)
        return {"provider": provider_used, "response": final}

# =========================
# CLI EXEMPLO
# =========================
def main():
    init_db()
    router = QuotaAwareRouter()

    # Exemplo de busca paga geral
    q = 'site:linkedin.com/in "engenheiro civil" São Paulo'
    try:
        out = router.search(q, user_id="userA")
        print(f"[search] via {out['provider']} -> keys: {list(out['response'].keys())[:5]}")
    except Exception as e:
        print("search error:", e)

    # Exemplo de busca com contexto
    try:
        profile = build_profile(
            city="Sorocaba",
            uf="SP",
            role="ex-prefeito",
            akas="Joãozinho",
            party="MDB",
            doc=""
        )

        blocks = router.search_all_dorks(
            name="João Silva",
            user_id="userA",
            profile=profile,
            strategy="hybrid"
        )
        print(f"[dorks] blocos: {len(blocks)}")
    except Exception as e:
        print("dorks error:", e)

    # Exemplo de notícias gratuitas por pessoa com contexto
    try:
        person = "Felipe Neto"
        news_out = router.search_news_free(
            person=person,
            user_id="userA",
            max_n=6,
            profile=build_profile(city="", uf="", role="", akas="", party="", doc=""),
            min_score=40
        )
        print(f"[news_free] via {news_out['provider']} -> {len(news_out['response'].get('news', []))} itens")
        for i, n in enumerate(news_out["response"].get("news", []), 1):
            print(f"{i}. {n['title']}  |  {n['source']}  |  {n['link']}")
    except Exception as e:
        print("news_free error:", e)

if __name__ == "__main__":
    main()