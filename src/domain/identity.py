import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

STOPWORDS_PT = {
    "de", "da", "do", "das", "dos", "e", "junior", "júnior", "filho", "neto"
}

SOCIAL_DOMAINS = {
    "linkedin.com", "www.linkedin.com",
    "facebook.com", "www.facebook.com", "m.facebook.com",
    "instagram.com", "www.instagram.com",
    "x.com", "twitter.com", "www.twitter.com",
    "threads.net", "www.threads.net",
    "tiktok.com", "www.tiktok.com",
    "youtube.com", "www.youtube.com",
}


@dataclass
class IdentityProfile:
    full_name: str
    aliases: List[str] = field(default_factory=list)
    cpf: str = ""
    city: str = ""
    state: str = ""
    party: str = ""
    role: str = ""
    organization: str = ""
    company: str = ""
    website: str = ""

    def all_names(self) -> List[str]:
        raw = [self.full_name, *self.aliases]
        out: List[str] = []
        seen = set()
        for item in raw:
            s = " ".join((item or "").split()).strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
        return out


def normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def clean_cpf(cpf: str) -> str:
    return re.sub(r"\D", "", cpf or "")


def tokenize_name(name: str) -> List[str]:
    parts = [p for p in re.split(r"\s+", normalize(name)) if p]
    return [p for p in parts if p not in STOPWORDS_PT]


def name_variants(name: str, aliases: Optional[Sequence[str]] = None) -> List[str]:
    base_names = [name, *(aliases or [])]
    variants: List[str] = []
    seen = set()

    for raw in base_names:
        cleaned = " ".join((raw or "").split()).strip()
        if not cleaned:
            continue
        tokens = cleaned.split()
        candidates = {
            cleaned,
            normalize(cleaned),
        }
        if len(tokens) >= 2:
            candidates.add(f"{tokens[0]} {tokens[-1]}")
        if len(tokens) >= 3:
            candidates.add(f"{tokens[0]} {' '.join(tokens[1:-1])} {tokens[-1]}")
        for c in candidates:
            key = normalize(c)
            if key and key not in seen:
                seen.add(key)
                variants.append(c)
    return variants


def apply_hints_to_query(profile: IdentityProfile) -> str:
    parts = [f'"{profile.full_name}"']
    if profile.cpf:
        parts.append(f'"{clean_cpf(profile.cpf)}"')
    if profile.city:
        parts.append(f'"{profile.city}"')
    if profile.state:
        parts.append(f'"{profile.state}"')
    if profile.party:
        parts.append(f'"{profile.party}"')
    if profile.role:
        parts.append(f'"{profile.role}"')
    if profile.organization:
        parts.append(f'"{profile.organization}"')
    if profile.company:
        parts.append(f'"{profile.company}"')
    return " ".join(parts)


def build_hint_terms(profile: IdentityProfile) -> List[str]:
    hints = []
    for value in [profile.city, profile.state, profile.party, profile.role, profile.organization, profile.company]:
        if value:
            hints.append(normalize(value))
    if profile.cpf:
        hints.append(clean_cpf(profile.cpf))
    return hints


def score_identity_match(profile: IdentityProfile, title: str = "", snippet: str = "", url: str = "") -> Tuple[float, Dict[str, float]]:
    text = normalize(" ".join([title or "", snippet or "", url or ""]))
    parts = tokenize_name(profile.full_name)
    hints = build_hint_terms(profile)
    breakdown: Dict[str, float] = {
        "full_name": 0.0,
        "partial_name": 0.0,
        "hints": 0.0,
        "social": 0.0,
    }

    all_names = name_variants(profile.full_name, profile.aliases)
    for variant in all_names:
        v = normalize(variant)
        if v and v in text:
            breakdown["full_name"] = 0.55
            break

    matched_parts = 0
    for p in parts:
        if p in text:
            matched_parts += 1
    if parts:
        ratio = matched_parts / max(1, len(parts))
        breakdown["partial_name"] = min(0.25, ratio * 0.25)

    matched_hints = 0
    for h in hints:
        if h and h in text:
            matched_hints += 1
    if hints:
        breakdown["hints"] = min(0.30, 0.10 * matched_hints)

    if any(dom in text for dom in SOCIAL_DOMAINS):
        breakdown["social"] = 0.05

    score = min(1.0, sum(breakdown.values()))
    return score, breakdown


def qualifies_result(profile: IdentityProfile, title: str = "", snippet: str = "", url: str = "", threshold: float = 0.45) -> bool:
    score, _ = score_identity_match(profile, title=title, snippet=snippet, url=url)
    return score >= threshold