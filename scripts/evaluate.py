"""Honest numbers for the README.

Cross validation rather than a score on the training data, because a model
scored on what it memorised tells you nothing. Escalation is reported as recall
and precision separately: recall is the number that matters, since a missed
escalation is the expensive mistake.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sklearn.metrics import precision_score, recall_score  # noqa: E402
from sklearn.model_selection import StratifiedKFold, cross_val_predict  # noqa: E402

from app.classify import ESCALATION_THRESHOLD, _pipeline  # noqa: E402
from app.data_store import load_interactions  # noqa: E402
from app.rules import escalation_check  # noqa: E402

FOLDS = 5


def main():
    texts, categories, escalate, names = load_interactions()
    folds = StratifiedKFold(n_splits=FOLDS, shuffle=True, random_state=7)

    predicted_category = cross_val_predict(_pipeline(), texts, categories, cv=folds)
    accuracy = sum(p == t for p, t in zip(predicted_category, categories)) / len(categories)

    probabilities = cross_val_predict(
        _pipeline(), texts, escalate, cv=folds, method="predict_proba"
    )[:, 1]
    predicted_escalation = [p >= ESCALATION_THRESHOLD for p in probabilities]
    escalation_recall = recall_score(escalate, predicted_escalation)
    escalation_precision = precision_score(escalate, predicted_escalation, zero_division=0)

    # The rule layer on its own, and the two together as the app runs them.
    rule_only = [escalation_check(t, False, 0.0)[0] for t in texts]
    combined = [r or m for r, m in zip(rule_only, predicted_escalation)]

    print(f"Examples: {len(texts)}   Categories: {len(names)}   Escalations: {sum(escalate)}")
    print(f"{FOLDS}-fold cross validation\n")
    print(f"Category accuracy            {accuracy:.1%}")
    print(f"Escalation recall (model)    {escalation_recall:.1%}")
    print(f"Escalation precision (model) {escalation_precision:.1%}")
    print(f"Escalation recall (rules)    {recall_score(escalate, rule_only):.1%}")
    print(f"Escalation recall (combined) {recall_score(escalate, combined):.1%}")
    print(f"Escalation precision (combined) {precision_score(escalate, combined, zero_division=0):.1%}")

    print("\nPer category accuracy")
    totals, correct = Counter(), Counter()
    for actual, predicted in zip(categories, predicted_category):
        totals[actual] += 1
        correct[actual] += int(actual == predicted)
    for name in names:
        if totals[name]:
            print(f"  {name:<14} {correct[name] / totals[name]:6.1%}  ({totals[name]} examples)")

    misses = [
        (text, actual, predicted)
        for text, actual, predicted in zip(texts, categories, predicted_category)
        if actual != predicted
    ]
    print(f"\nMisclassified: {len(misses)}")
    for text, actual, predicted in misses[:8]:
        print(f"  {actual} -> {predicted}: {text[:70]}")

    missed_escalations = [
        text for text, actual, got in zip(texts, escalate, combined) if actual and not got
    ]
    print(f"\nMissed escalations (combined): {len(missed_escalations)}")
    for text in missed_escalations:
        print(f"  {text[:78]}")

    print("\nEscalation threshold sweep (combined with rules)")
    for threshold in [0.25, 0.3, 0.35, 0.4, 0.5, 0.6]:
        swept = [r or (p >= threshold) for r, p in zip(rule_only, probabilities)]
        print(
            f"  {threshold:.2f}  recall {recall_score(escalate, swept):6.1%}"
            f"  precision {precision_score(escalate, swept, zero_division=0):6.1%}"
        )


if __name__ == "__main__":
    main()
