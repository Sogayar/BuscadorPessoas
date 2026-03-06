# src/utils/identity.py
import re
import unicodedata
from typing import List, Tuple, Dict, Any, Optional


def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


def _split_akas(akas: str) -> List[str]:
    if not akas:
        return []
    out = []
    for a in akas.split(","):
        a = a.strip()
        if a:
            out.append(a)
    return out


def name_variants(name: str, akas: str = "") -> List[str]:
    """
    Gera variantes do nome para matching:
    - com/sem acentos
    - primeiro + último
    - apelidos / AKA
    """
    name = " ".join((name or "").split())
    if not name:
        return []

    parts = name.split()
    variants = {name, _norm(name)}

    if len(parts) >= 2:
        fl = f"{parts[0]} {parts[-1]}"
        variants.add(fl)
        variants.add(_norm(fl))

    for aka in _split_akas(akas):
        variants.add(aka)
        variants.add(_norm(aka))

    out, seen = [], set()
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def qualify_news(
    person: str,
    title: str,
    desc: str = "",
    city: str = "",
    uf: str = "",
    role: str = "",
    akas: str = "",
    party: str = "",
    doc: str = "",
    min_score: int = 40
) -> bool:
    """
    Decide se uma notícia provavelmente se refere ao investigado.
    Usa score contextual anti-homônimo.
    """
    score, _ = score_name_match(
        person=person,
        text=f"{title or ''}\n{desc or ''}",
        city=city,
        uf=uf,
        role=role,
        akas=akas,
        party=party,
        doc=doc
    )
    return score >= min_score


def score_name_match(
    person: str,
    text: str,
    city: str = "",
    uf: str = "",
    role: str = "",
    akas: str = "",
    party: str = "",
    doc: str = ""
) -> Tuple[int, List[str]]:
    """
    Score auditável 0..100 com motivos.

    Regras sugeridas:
    - +40 nome/variante encontrada
    - +15 cidade
    - +10 UF
    - +15 cargo/função
    - +10 apelido
    - +10 partido/órgão
    - +30 documento (CPF/CNPJ)
    """
    t = _norm(text)
    score = 0
    reasons: List[str] = []

    # Nome / variantes
    matched_name = False
    matched_aka = False

    variants = name_variants(person, akas=akas)
    aka_variants = [_norm(a) for a in _split_akas(akas)]

    for pv in variants:
        pvn = _norm(pv)
        if pvn and pvn in t:
            matched_name = True
            break

    if matched_name:
        score += 40
        reasons.append("nome")

    # Cidade
    city_n = _norm(city)
    if city_n and city_n in t:
        score += 15
        reasons.append("cidade")

    # UF
    uf_n = _norm(uf)
    if uf_n:
        uf_tokens = {
            f" {uf_n} ",
            f"/{uf_n}",
            f"-{uf_n}",
            f"({uf_n})",
            f",{uf_n}",
        }
        txt_pad = f" {t} "
        if any(tok in txt_pad for tok in uf_tokens):
            score += 10
            reasons.append("uf")

    # Cargo / função
    role_n = _norm(role)
    if role_n and role_n in t:
        score += 15
        reasons.append("cargo")

    # Apelidos
    for aka in aka_variants:
        if aka and aka in t:
            matched_aka = True
            break

    if matched_aka:
        score += 10
        reasons.append("apelido")

    # Partido / órgão
    party_n = _norm(party)
    if party_n and party_n in t:
        score += 10
        reasons.append("partido_ou_orgao")

    # Documento
    doc_n = re.sub(r"\D+", "", doc or "")
    if doc_n:
        text_digits = re.sub(r"\D+", "", text or "")
        if doc_n and doc_n in text_digits:
            score += 30
            reasons.append("documento")

    if score > 100:
        score = 100

    return score, reasons


def apply_hints_to_query(
    name: str,
    cpf: str = "",
    city: str = "",
    uf: str = "",
    role: str = "",
    akas: str = "",
    party: str = "",
    doc: str = ""
) -> str:
    """
    Injeta hints como operadores da query para estreitar a busca.
    """
    q = f"\"{name.strip()}\"" if name and not (name.startswith('"') and name.endswith('"')) else (name or "")
    hints = []

    if cpf:
        hints.append(f'"{cpf}"')

    if doc and doc != cpf:
        hints.append(f'"{doc}"')

    if city:
        hints.append(f'"{city}"')

    if uf:
        hints.append(f'"{uf}"')

    if role:
        hints.append(f'"{role}"')

    if party:
        hints.append(f'"{party}"')

    aka_list = _split_akas(akas)
    if aka_list:
        hints.append("(" + " OR ".join([f'"{a}"' for a in aka_list]) + ")")

    if hints:
        q = f"{q} " + " ".join(hints)

    return q.strip()


def qualifies_by_hints(
    person: str,
    cpf: str = "",
    city: str = "",
    uf: str = "",
    role: str = "",
    akas: str = "",
    party: str = "",
    doc: str = ""
) -> Tuple[str, List[str]]:
    """
    Retorna um rótulo de qualificação e quais hints foram aplicados.
    """
    used = []

    if cpf:
        used.append("cpf")
    if doc:
        used.append("doc")
    if city:
        used.append("city")
    if uf:
        used.append("uf")
    if role:
        used.append("role")
    if akas:
        used.append("akas")
    if party:
        used.append("party")

    label = "qualified" if used else "raw"
    return label, used


def build_profile_from_settings(settings: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """
    Extrai o bloco 'filters' do settings.json de forma segura.
    """
    filters = (settings or {}).get("filters", {}) or {}
    return {
        "city": (filters.get("city") or "").strip(),
        "uf": (filters.get("uf") or "").strip(),
        "role": (filters.get("role") or "").strip(),
        "akas": (filters.get("akas") or "").strip(),
        "party": (filters.get("party") or "").strip(),
        "doc": (filters.get("doc") or "").strip(),
    }