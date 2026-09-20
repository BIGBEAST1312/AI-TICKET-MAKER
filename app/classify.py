"""The machine learning part: two small classifiers trained on sample data.

Both are TF-IDF features feeding a logistic regression. That pairing is chosen
on purpose rather than something larger:

  * it trains in under a second on a laptop, so the loop stays fast
  * every prediction can be explained by the words that drove it, which matters
    when a support ticket is wrong and someone asks why
  * with roughly a hundred examples, a larger model would memorise rather than
    generalise

Classifier one picks the category. Classifier two answers a separate question:
can the desk handle this, or does it go above desk level. They are separate
because a ticket can be any category and still be an escalation.
"""

from dataclasses import dataclass
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from app.data_store import load_interactions

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "classifier.joblib"

# A missed escalation costs far more than a needless one, so the escalation
# classifier fires below the usual half way mark. The number came from the
# sweep in scripts/evaluate.py.
ESCALATION_THRESHOLD = 0.40


def _pipeline():
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    sublinear_tf=True,
                    min_df=1,
                    stop_words="english",
                ),
            ),
            ("clf", LogisticRegression(max_iter=1000, C=5.0, class_weight="balanced")),
        ]
    )


def train(save=True):
    """Fits both classifiers on the whole sample set and saves them."""
    texts, categories, escalate, _ = load_interactions()

    category_model = _pipeline().fit(texts, categories)
    escalation_model = _pipeline().fit(texts, escalate)

    bundle = {"category": category_model, "escalation": escalation_model}
    if save:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(bundle, MODEL_PATH)
    return bundle


def load(train_if_missing=True):
    if MODEL_PATH.exists():
        return joblib.load(MODEL_PATH)
    if train_if_missing:
        return train()
    raise FileNotFoundError("No trained model. Run: python scripts/train.py")


@dataclass
class Prediction:
    category: str
    category_confidence: float
    escalate: bool
    escalate_confidence: float
    top_terms: list

    def as_dict(self):
        return {
            "category": self.category,
            "category_confidence": round(self.category_confidence, 3),
            "escalate": self.escalate,
            "escalate_confidence": round(self.escalate_confidence, 3),
            "top_terms": self.top_terms,
        }


def _driving_terms(model, text, label, limit=4):
    """The words in this text that pushed the model toward the label it chose.

    Useful when a prediction looks wrong: it shows what the model reacted to.
    """
    vectorizer = model.named_steps["tfidf"]
    clf = model.named_steps["clf"]
    features = vectorizer.transform([text])
    names = vectorizer.get_feature_names_out()

    classes = list(clf.classes_)
    if label not in classes:
        return []
    if len(classes) == 2:
        weights = clf.coef_[0] * (1 if classes.index(label) == 1 else -1)
    else:
        weights = clf.coef_[classes.index(label)]

    scored = []
    for index in features.nonzero()[1]:
        contribution = features[0, index] * weights[index]
        if contribution > 0:
            scored.append((names[index], float(contribution)))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [term for term, _ in scored[:limit]]


def predict(text, bundle=None):
    bundle = bundle or load()
    category_model = bundle["category"]
    escalation_model = bundle["escalation"]

    category = category_model.predict([text])[0]
    category_confidence = float(max(category_model.predict_proba([text])[0]))

    escalate_probabilities = dict(
        zip(escalation_model.classes_, escalation_model.predict_proba([text])[0])
    )
    escalate_confidence = float(escalate_probabilities.get(True, 0.0))

    return Prediction(
        category=category,
        category_confidence=category_confidence,
        escalate=escalate_confidence >= ESCALATION_THRESHOLD,
        escalate_confidence=escalate_confidence,
        top_terms=_driving_terms(category_model, text, category),
    )
