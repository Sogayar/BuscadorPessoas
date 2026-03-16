import json
import os
from typing import Any, Dict

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "settings.json")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "out_dir": os.path.abspath("./saidas"),
    "n_top": 5,
    "include_news": True,
    "include_org": True,
    "build_index_csv": True,
    "export_pdf": False,
    "reason": "fraudes",
    "filters": {
        "cpf": "",
        "city": "",
        "state": "",
        "party": "",
        "role": "",
        "organization": "",
        "company": "",
        "aliases": [],
    },
}


def load_settings() -> Dict[str, Any]:
    if not os.path.exists(SETTINGS_FILE):
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        merged = DEFAULT_SETTINGS.copy()
        merged.update(raw)
        merged["filters"] = {**DEFAULT_SETTINGS["filters"], **(raw.get("filters") or {})}
        return merged
    except Exception:
        return DEFAULT_SETTINGS.copy()


def save_settings(data: Dict[str, Any]) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)