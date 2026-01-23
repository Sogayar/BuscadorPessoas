# src/utils/identity.py
import re
import unicodedata
from typing import Iterable, List, Tuple

def _norm(s: str) -> str:
    if not s: return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()

def name_variants(name: str) -> List[str]:
    """
    Gera variantes simples do nome para matching:
    - com/sem acentos
    - com/sem middle names
    - primeiro + último
    """
    name = " ".join((name or "").split())
    if not name:
        return []
    parts = name.split()
    variants = {name, _norm(name)}
    if len(parts) >= 2:
        fl = f"{parts[0]} {parts[-1]}"
        variants.add(fl); variants.add(_norm(fl))
    # remove duplicados preservando ordem
    out, seen = [], set()
    for v in variants:
        if v not in seen:
            seen.add(v); out.append(v)
    return out

def qualify_news(person: str, title: str, desc: str = "") -> bool:
    """
    Aproximação leve: considera match quando qualquer variante do nome
    aparecer no título/descrição (normalizados).
    """
    pvars = name_variants(person)
    t = _norm(title); d = _norm(desc)
    for pv in pvars:
        if pv and (pv in t or pv in d):
            return True
    return False

def score_name_match(person: str, text: str) -> float:
    """
    Score 0..1 de quão provável o texto se referir à pessoa.
    Hoje: 1.0 se conter qualquer variante, senão 0.0 (simples e barato).
    """
    return 1.0 if qualify_news(person, text) else 0.0

def apply_hints_to_query(name: str, cpf: str = "", city: str = "", uf: str = "") -> str:
    """
    Injeta hints (CPF/cidade/UF) como operadores da query para estreitar a busca orgânica.
    """
    q = f"\"{name.strip()}\"" if name and not (name.startswith('"') and name.endswith('"')) else (name or "")
    hints = []
    if cpf:  hints.append(f'"{cpf}"')
    if city: hints.append(f'"{city}"')
    if uf:   hints.append(f'"{uf}"')
    if hints:
        q = f"{q} " + " ".join(hints)
    return q.strip()

def qualifies_by_hints(person: str, cpf: str = "", city: str = "", uf: str = "") -> Tuple[str, List[str]]:
    """
    Retorna um rótulo de qualificação e quais dicas foram aplicadas (para logging).
    """
    used = []
    if cpf:  used.append("cpf")
    if city: used.append("city")
    if uf:   used.append("uf")
    label = "qualified" if used else "raw"
    return label, used
