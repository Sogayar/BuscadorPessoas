from typing import Any, Dict, List


def pick_news_items(payload: Dict[str, Any], limit: int = 10) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for key in ("news", "news_results", "top_stories"):
        for it in payload.get(key, []) or []:
            url = it.get("link") or it.get("url") or ""
            if not url:
                continue
            out.append({
                "title": it.get("title") or "",
                "snippet": it.get("snippet") or it.get("description") or "",
                "url": url,
                "source": it.get("source") or "",
            })
    return _dedup(out)[:limit]


def pick_organic_items(payload: Dict[str, Any], limit: int = 10) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for it in payload.get("items", []) or []:
        if it.get("link"):
            out.append({
                "title": it.get("title") or "",
                "snippet": it.get("snippet") or "",
                "url": it["link"],
                "source": "",
            })
    for it in payload.get("organic", []) or []:
        url = it.get("link") or it.get("url") or ""
        if url:
            out.append({
                "title": it.get("title") or "",
                "snippet": it.get("snippet") or "",
                "url": url,
                "source": "",
            })
    for it in payload.get("organic_results", []) or []:
        url = it.get("url") or it.get("link") or ""
        if url:
            out.append({
                "title": it.get("title") or "",
                "snippet": it.get("snippet") or "",
                "url": url,
                "source": "",
            })
    return _dedup(out)[:limit]


def _dedup(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    out = []
    for item in items:
        url = item.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(item)
    return out