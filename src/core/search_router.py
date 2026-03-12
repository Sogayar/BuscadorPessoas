import json
import hashlib
import os
import sqlite3
import time
import unicodedata
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from queue import SimpleQueue
from threading import Lock, Thread
from typing import Any, Dict, Generator, List, Optional, Tuple
from urllib.parse import quote_plus

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from zoneinfo import ZoneInfo

from src.utils.dorks import build_dorks
from src.utils.identity import IdentityProfile, qualifies_result

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for p in [os.path.join(ROOT, ".env"), os.path.join(ROOT, "config", ".env")]:
    if os.path.exists(p):
        load_dotenv(p)
        break
else:
    load_dotenv()

TZ = ZoneInfo("America/Fortaleza")
DB_PATH = os.getenv("SEARCH_DB_PATH", os.path.join(ROOT, "data", "search_quota.sqlite"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

GOOGLE_DAILY_LIMIT = int(os.getenv("GOOGLE_DAILY_LIMIT", "100"))
SERPSTACK_MONTHLY_LIMIT = int(os.getenv("SERPSTACK_MONTHLY_LIMIT", "100"))
ZENSERP_MONTHLY_LIMIT = int(os.getenv("ZENSERP_MONTHLY_LIMIT", "50"))
SERPER_FINITE_LIMIT = int(os.getenv("SERPER_FINITE_LIMIT", "2500"))

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CX = os.getenv("GOOGLE_CX", "")
SERPSTACK_KEY = os.getenv("SERPSTACK_KEY", "")
ZENSERP_KEY = os.getenv("ZENSERP_KEY", "")
SERPER_KEY = os.getenv("SERPER_KEY", "")

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "604800"))
ANTI_DUP_WINDOW_SECONDS = int(os.getenv("ANTI_DUP_WINDOW_SECONDS", "900"))

# SSL / CA
# Preferível: apontar para o certificado raiz/intermediário corporativo do TCU em PEM.
# Exemplo no .env:
# REQUESTS_CA_BUNDLE=C:/certs/tcu-ca-chain.pem
# ou
# CUSTOM_CA_BUNDLE=C:/certs/tcu-ca-chain.pem
CUSTOM_CA_BUNDLE = os.getenv("CUSTOM_CA_BUNDLE", "") or os.getenv("REQUESTS_CA_BUNDLE", "") or os.getenv("SSL_CERT_FILE", "")
ALLOW_INSECURE_SSL = os.getenv("ALLOW_INSECURE_SSL", "0").strip().lower() in {"1", "true", "yes"}

_write_queue: "SimpleQueue[Optional[Tuple[str, tuple]]]" = SimpleQueue()
_writer_lock = Lock()
_writer_started = False


@dataclass
class QuotaStatus:
    provider: str
    period: str
    count: int
    limit_value: int
    last_reset: str


class ProviderBase:
    name = "base"

    def search(self, query: str) -> Dict[str, Any]:
        raise NotImplementedError


def _build_session() -> requests.Session:
    sess = requests.Session()

    retries = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)

    if CUSTOM_CA_BUNDLE and os.path.exists(CUSTOM_CA_BUNDLE):
        sess.verify = CUSTOM_CA_BUNDLE
    elif ALLOW_INSECURE_SSL:
        sess.verify = False
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass

    sess.headers.update(UA)
    return sess


HTTP = _build_session()


class GoogleProvider(ProviderBase):
    name = "google"

    def search(self, query: str) -> Dict[str, Any]:
        if not GOOGLE_API_KEY or not GOOGLE_CX:
            raise RuntimeError("Google API KEY/CX ausentes")
        r = HTTP.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": GOOGLE_API_KEY, "cx": GOOGLE_CX, "q": query, "num": 10, "gl": "br", "hl": "pt"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


class SerpstackProvider(ProviderBase):
    name = "serpstack"

    def search(self, query: str) -> Dict[str, Any]:
        if not SERPSTACK_KEY:
            raise RuntimeError("SERPSTACK_KEY ausente")
        r = HTTP.get(
            "http://api.serpstack.com/search",
            params={"access_key": SERPSTACK_KEY, "query": query, "num": 10, "gl": "br", "hl": "pt"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


class ZenserpProvider(ProviderBase):
    name = "zenserp"

    def search(self, query: str) -> Dict[str, Any]:
        if not ZENSERP_KEY:
            raise RuntimeError("ZENSERP_KEY ausente")
        headers = dict(UA)
        headers["apikey"] = ZENSERP_KEY
        r = HTTP.get(
            "https://app.zenserp.com/api/v2/search",
            params={"q": query, "num": 10, "gl": "br", "hl": "pt"},
            timeout=30,
            headers=headers,
        )
        r.raise_for_status()
        return r.json()


class SerperProvider(ProviderBase):
    name = "serper"

    def search(self, query: str) -> Dict[str, Any]:
        if not SERPER_KEY:
            raise RuntimeError("SERPER_KEY ausente")
        headers = dict(UA)
        headers["X-API-KEY"] = SERPER_KEY
        headers["Content-Type"] = "application/json"
        r = HTTP.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": 10, "gl": "br", "hl": "pt"},
            timeout=30,
            headers=headers,
        )
        r.raise_for_status()
        return r.json()


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    try:
        yield conn
    finally:
        conn.close()


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def month_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m")


def init_db() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS quota_counters (
                provider TEXT PRIMARY KEY,
                period TEXT NOT NULL,
                count INTEGER NOT NULL,
                limit_value INTEGER NOT NULL,
                last_reset TEXT NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_search_logs_query_ts ON search_logs(query, ts);
            """
        )
        _seed_counter(conn, "google", "daily", 0, GOOGLE_DAILY_LIMIT, today_str())
        _seed_counter(conn, "serpstack", "monthly", 0, SERPSTACK_MONTHLY_LIMIT, month_str())
        _seed_counter(conn, "zenserp", "monthly", 0, ZENSERP_MONTHLY_LIMIT, month_str())
        _seed_counter(conn, "serper", "finite", 0, SERPER_FINITE_LIMIT, "start")
    _ensure_writer_thread()


def _seed_counter(conn: sqlite3.Connection, provider: str, period: str, count: int, limit_value: int, last_reset: str) -> None:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM quota_counters WHERE provider=?", (provider,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO quota_counters(provider, period, count, limit_value, last_reset) VALUES(?,?,?,?,?)",
            (provider, period, count, limit_value, last_reset),
        )


def _ensure_writer_thread() -> None:
    global _writer_started
    with _writer_lock:
        if _writer_started:
            return
        t = Thread(target=_writer_loop, daemon=True)
        t.start()
        _writer_started = True


def _writer_loop() -> None:
    while True:
        item = _write_queue.get()
        if item is None:
            break
        sql, params = item
        for attempt in range(5):
            try:
                with get_conn() as conn:
                    conn.execute(sql, params)
                break
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or attempt == 4:
                    break
                time.sleep(0.15 * (attempt + 1))


def queue_write(sql: str, params: tuple) -> None:
    _ensure_writer_thread()
    _write_queue.put((sql, params))


def log_event(provider: Optional[str], event: str, details: Dict[str, Any]) -> None:
    queue_write(
        "INSERT INTO audit_logs(ts, provider, event, details) VALUES(?,?,?,?)",
        (int(time.time()), provider, event, json.dumps(details, ensure_ascii=False)),
    )


def log_search(user_id: Optional[str], query: str, provider: str, cache_hit: bool) -> None:
    queue_write(
        "INSERT INTO search_logs(ts, user_id, query, provider, cache_hit) VALUES(?,?,?,?,?)",
        (int(time.time()), user_id or "", query, provider, 1 if cache_hit else 0),
    )


def normalize_query(q: str) -> str:
    return " ".join((q or "").strip().lower().split())


def hash_query(q: str) -> str:
    return hashlib.sha256(q.encode("utf-8")).hexdigest()


def cache_get(query: str) -> Optional[Dict[str, Any]]:
    qh = hash_query(normalize_query(query))
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT response_json, created_at FROM results_cache WHERE qhash=?", (qh,))
        row = cur.fetchone()
    if not row:
        return None
    response_json, created_at = row
    if int(time.time()) - int(created_at) > CACHE_TTL_SECONDS:
        queue_write("DELETE FROM results_cache WHERE qhash=?", (qh,))
        return None
    try:
        return json.loads(response_json)
    except Exception:
        return None


def cache_set(query: str, response: Dict[str, Any]) -> None:
    qh = hash_query(normalize_query(query))
    queue_write(
        "REPLACE INTO results_cache(qhash, query, response_json, created_at) VALUES(?,?,?,?)",
        (qh, query, json.dumps(response, ensure_ascii=False), int(time.time())),
    )


def dedup_recent(query: str) -> bool:
    since = int(time.time()) - ANTI_DUP_WINDOW_SECONDS
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM search_logs WHERE ts >= ? AND query = ? LIMIT 1", (since, query))
        return cur.fetchone() is not None


def _fetch_quota(cur: sqlite3.Cursor, provider: str) -> Tuple[str, int, int, str]:
    cur.execute("SELECT period, count, limit_value, last_reset FROM quota_counters WHERE provider=?", (provider,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Provider {provider} não configurado")
    return row


def try_consume(provider: str, n: int = 1) -> Tuple[bool, QuotaStatus]:
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        cur = conn.cursor()
        period, count, limit_value, last_reset = _fetch_quota(cur, provider)
        now_day = today_str()
        now_month = month_str()
        reset_payload = None

        if period == "daily" and last_reset != now_day:
            count = 0
            last_reset = now_day
            cur.execute("UPDATE quota_counters SET count=?, last_reset=? WHERE provider=?", (count, last_reset, provider))
            reset_payload = {"period": period, "last_reset": last_reset}

        if period == "monthly" and last_reset != now_month:
            count = 0
            last_reset = now_month
            cur.execute("UPDATE quota_counters SET count=?, last_reset=? WHERE provider=?", (count, last_reset, provider))
            reset_payload = {"period": period, "last_reset": last_reset}

        if count + n > limit_value:
            conn.rollback()
            return False, QuotaStatus(provider, period, count, limit_value, last_reset)

        count += n
        cur.execute("UPDATE quota_counters SET count=? WHERE provider=?", (count, provider))
        conn.commit()

    if reset_payload:
        log_event(provider, "reset_quota", reset_payload)
    return True, QuotaStatus(provider, period, count, limit_value, last_reset)


def _parse_gnews_rss_items(xml_bytes: bytes) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    items: List[Dict[str, Any]] = []
    for it in root.findall(".//item"):
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "description": (it.findtext("description") or "").strip(),
            "pubDate": (it.findtext("pubDate") or "").strip(),
            "source": (it.findtext("source") or "").strip(),
        })
    return items


def _normalize_news(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"news": [{
        "title": it.get("title", "").strip(),
        "link": it.get("link", "").strip(),
        "pubDate": it.get("pubDate", "").strip(),
        "source": it.get("source", "").strip(),
        "snippet": it.get("description", "").strip(),
    } for it in items]}


def _fetch_gnews_aggregated(query: str, max_n: int, domains: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    base = "https://news.google.com/rss/search"
    common = "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    seen = set()
    aggregated: List[Dict[str, Any]] = []
    errors: List[str] = []

    def add(url: str) -> None:
        try:
            r = HTTP.get(url, timeout=30)
            r.raise_for_status()
            for item in _parse_gnews_rss_items(r.content):
                key = item.get("link") or item.get("title")
                if key and key not in seen:
                    seen.add(key)
                    aggregated.append(item)
        except requests.exceptions.SSLError as e:
            errors.append(f"SSL Google News: {e}")
            log_event("google_news_rss", "ssl_error", {"url": url, "error": str(e)})
        except Exception as e:
            errors.append(f"Google News: {e}")
            log_event("google_news_rss", "error", {"url": url, "error": str(e)})

    if not domains:
        add(f"{base}?q={quote_plus(query)}{common}")
    else:
        for d in domains:
            add(f"{base}?q={quote_plus(f'{query} site:{d}')}{common}")

    meta = {
        "total_raw": len(aggregated),
        "domains_used": domains or [],
        "errors": errors,
    }
    return aggregated[: max_n * 4], meta


def _gdelt_news(query: str, max_n: int = 10) -> Dict[str, Any]:
    try:
        r = HTTP.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={"query": query, "mode": "ArtList", "format": "json", "maxrecords": max_n * 3, "timespan": "30d"},
            timeout=30,
        )
        r.raise_for_status()
        js = r.json()
        items = []
        for a in js.get("articles") or []:
            items.append({
                "title": a.get("title") or "",
                "link": a.get("url") or "",
                "description": a.get("excerpt") or a.get("title") or "",
                "pubDate": a.get("seendate") or "",
                "source": a.get("domain") or "",
            })
        return {"provider": "gdelt", "items": items}
    except requests.exceptions.SSLError as e:
        log_event("gdelt", "ssl_error", {"error": str(e)})
        return {"provider": "gdelt", "items": [], "error": f"SSL: {e}"}
    except Exception as e:
        log_event("gdelt", "error", {"error": str(e)})
        return {"provider": "gdelt", "items": [], "error": str(e)}


class QuotaAwareRouter:
    def __init__(self):
        self.providers: Dict[str, ProviderBase] = {
            "google": GoogleProvider(),
            "serpstack": SerpstackProvider(),
            "zenserp": ZenserpProvider(),
            "serper": SerperProvider(),
        }

    def _try_provider(self, provider_key: str, query: str) -> Optional[Dict[str, Any]]:
        ok, st = try_consume(provider_key, 1)
        if not ok:
            log_event(provider_key, "quota_exceeded", {"count": st.count, "limit": st.limit_value})
            return None
        try:
            payload = self.providers[provider_key].search(query)
            log_event(provider_key, "success", {"query": query})
            return {"provider": provider_key, "response": payload}
        except requests.exceptions.SSLError as e:
            log_event(provider_key, "ssl_error", {"query": query, "error": str(e)})
            return None
        except requests.HTTPError as e:
            log_event(provider_key, "http_error", {"status": getattr(e.response, "status_code", None), "text": getattr(e.response, "text", "")[:300]})
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
                return {"provider": "cache", "response": cached}

        cached = cache_get(q_norm)
        if cached is not None:
            log_search(user_id, q_norm, "cache", True)
            return {"provider": "cache", "response": cached}

        for provider in ["google", "serpstack", "zenserp", "serper"]:
            result = self._try_provider(provider, q_norm)
            if result is not None:
                cache_set(q_norm, result["response"])
                log_search(user_id, q_norm, result["provider"], False)
                return result

        # não explode a thread; devolve payload vazio para o worker seguir
        log_event("router", "all_providers_failed", {"query": q_norm})
        return {"provider": "none", "response": {}}

    def search_news_free(self, profile: IdentityProfile, reason_key: str, user_id: Optional[str] = None, max_n: int = 10) -> Dict[str, Any]:
        query = f'"{profile.full_name}"'
        q_norm = f"news:{normalize_query(profile.full_name)}:{reason_key}"
        cached = cache_get(q_norm)
        if cached is not None:
            log_search(user_id, q_norm, "cache_news", True)
            return {"provider": "cache_news", "response": cached}

        br_domains = [
            "g1.globo.com",
            "oglobo.globo.com",
            "uol.com.br",
            "folha.uol.com.br",
            "estadao.com.br",
            "metropoles.com",
            "migalhas.com.br",
        ]
        items, meta = _fetch_gnews_aggregated(query, max_n=max_n, domains=br_domains)
        if len(items) < max_n:
            more, more_meta = _fetch_gnews_aggregated(query, max_n=max_n, domains=None)
            items.extend(more)
            meta["errors"].extend(more_meta.get("errors", []))
            meta["total_raw"] = len(items)

        filtered = [
            it for it in items
            if qualifies_result(
                profile,
                title=it.get("title", ""),
                snippet=it.get("description", ""),
                url=it.get("link", ""),
            )
        ]

        provider_used = "google_news_rss"
        if not filtered:
            gd = _gdelt_news(query, max_n=max_n)
            provider_used = gd.get("provider", provider_used)
            filtered = [
                it for it in gd.get("items", [])
                if qualifies_result(
                    profile,
                    title=it.get("title", ""),
                    snippet=it.get("description", ""),
                    url=it.get("link", ""),
                )
            ]

        final = _normalize_news(filtered[:max_n])
        final["meta"] = {
            "total_raw": meta.get("total_raw", 0),
            "matched": len(filtered),
            "provider_primary": provider_used,
            "errors": meta.get("errors", []),
        }
        cache_set(q_norm, final)
        log_search(user_id, q_norm, provider_used, False)
        return {"provider": provider_used, "response": final}

    def search_reason_bundle(
        self,
        profile: IdentityProfile,
        reason_key: str,
        include_social: bool = True,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        blocks = []
        for dork in build_dorks(profile, reason_key, include_social=include_social):
            try:
                result = self.search(dork["query"], user_id=user_id)
                blocks.append({**dork, "provider": result.get("provider"), "response": result.get("response")})
            except Exception as e:
                blocks.append({**dork, "error": str(e)})
        return blocks