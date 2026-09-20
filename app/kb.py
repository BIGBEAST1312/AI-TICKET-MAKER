"""Finding the knowledge base article that covers a problem.

TF-IDF over the article text, cosine similarity against the client's words, with
a boost for articles in the predicted category. Small and explainable, which is
the point: a staff member can see why an article was suggested.
"""

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.data_store import load_kb

CATEGORY_BOOST = 0.15
MATCH_FLOOR = 0.05


class ArticleIndex:
    def __init__(self):
        self.articles, self.platform_notes = load_kb()
        corpus = [
            " ".join(
                [a["title"], a["category"]]
                + a.get("keywords", [])  # how people describe it, for articles with few steps
                + a.get("steps", [])
                + a.get("notes", [])
            )
            for a in self.articles
        ]
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english")
        self.matrix = self.vectorizer.fit_transform(corpus)

    def best_match(self, text, category=None):
        """Returns (article, score) or (None, 0.0) when nothing is close enough."""
        scores = cosine_similarity(self.vectorizer.transform([text]), self.matrix)[0]
        if category:
            scores = [
                score + (CATEGORY_BOOST if a["category"] == category else 0.0)
                for score, a in zip(scores, self.articles)
            ]
        best = max(range(len(scores)), key=lambda i: scores[i])
        if scores[best] < MATCH_FLOOR:
            return None, 0.0
        return self.articles[best], float(scores[best])
