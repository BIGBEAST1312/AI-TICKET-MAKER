"""Loading of the sample data and the placeholder knowledge base.

Everything the tool knows about lives in data/. Swap those two files and the
rest of the application follows, without code changes.
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name):
    with open(DATA_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


def load_interactions():
    """Returns (texts, categories, escalate_flags, category_names)."""
    raw = _load("interactions.json")
    examples = raw["examples"]
    texts = [e["text"] for e in examples]
    categories = [e["category"] for e in examples]
    escalate = [bool(e["escalate"]) for e in examples]
    return texts, categories, escalate, raw["categories"]


def load_kb():
    """Returns (articles, platform_notes)."""
    raw = _load("kb_articles.json")
    return raw["articles"], raw["platform_notes"]


def synthetic_kb():
    """True while the knowledge base is still placeholder content."""
    articles, _ = load_kb()
    return any(a.get("synthetic") for a in articles)
