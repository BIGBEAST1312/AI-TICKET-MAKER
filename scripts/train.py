"""Train both classifiers and save them to models/classifier.joblib."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classify import MODEL_PATH, train  # noqa: E402
from app.data_store import load_interactions  # noqa: E402

if __name__ == "__main__":
    texts, categories, escalate, names = load_interactions()
    print(f"{len(texts)} examples, {len(names)} categories, {sum(escalate)} escalations")
    train()
    print(f"saved to {MODEL_PATH}")
