"""
MiniLM-backed categorizer (all-MiniLM-L6-v2) with LogisticRegression head.
Falls back is handled at app factory if import fails.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
from sklearn.linear_model import LogisticRegression

from services.ml_model import DEFAULT_CATEGORY_RULES
from services.text_preprocess import ml_input_text
from services.vendor_normalizer import normalize_vendor

_LOCK = threading.Lock()
_MODEL = None


def _get_sentence_model():
    global _MODEL
    with _LOCK:
        if _MODEL is None:
            from sentence_transformers import SentenceTransformer

            _MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        return _MODEL


@dataclass
class MiniLMCategorizer:
    """
    embedding + logistic regression; same train/predict flow as AutoCategorizer.
    """

    rules: Dict[str, List[str]]
    confidence_threshold: float = 0.42
    min_training_rows: int = 12
    _classifier: Optional[LogisticRegression] = field(default=None, init=False, repr=False)
    _cache: Dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _cache_order: List[str] = field(default_factory=list, init=False, repr=False)
    _max_cache: int = 4000

    @staticmethod
    def default() -> "MiniLMCategorizer":
        return MiniLMCategorizer(rules=dict(DEFAULT_CATEGORY_RULES))

    def _cache_get(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def _cache_set(self, key: str, value: str) -> None:
        if key in self._cache:
            return
        self._cache[key] = value
        self._cache_order.append(key)
        while len(self._cache_order) > self._max_cache:
            old = self._cache_order.pop(0)
            self._cache.pop(old, None)

    def _rule(self, vendor: Optional[str]) -> str:
        v = ml_input_text(normalize_vendor(str(vendor or ""))).lower()
        if not v:
            return "Others"
        for cat, keywords in self.rules.items():
            if any(kw in v for kw in keywords):
                return cat
        return "Others"

    def _can_train(self, expenses: List[Dict[str, Any]]) -> bool:
        labeled = [
            e
            for e in expenses
            if str(e.get("category") or "").strip()
            and str(e.get("vendor") or "").strip()
            and str(e.get("category")).strip().lower() != "uncategorized"
        ]
        if len(labeled) < self.min_training_rows:
            return False
        labels = {str(e["category"]).strip() for e in labeled}
        return len(labels) >= 2

    def train_from_expenses(self, expenses: List[Dict[str, Any]]) -> bool:
        if not self._can_train(expenses):
            self._classifier = None
            return False
        labeled = [
            e
            for e in expenses
            if str(e.get("category") or "").strip()
            and str(e.get("vendor") or "").strip()
            and str(e.get("category")).strip().lower() not in ("uncategorized",)
        ]
        texts = [ml_input_text(normalize_vendor(str(e.get("vendor") or ""))) for e in labeled]
        y = [str(e["category"]).strip() for e in labeled]
        model = _get_sentence_model()
        try:
            X = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
            clf = LogisticRegression(max_iter=400, class_weight="balanced")
            clf.fit(X, y)
        except Exception:
            self._classifier = None
            return False
        self._classifier = clf
        return True

    def predict_category(self, vendor: Optional[str]) -> Optional[Dict[str, Any]]:
        if self._classifier is None:
            return None
        raw = normalize_vendor(str(vendor or ""))
        key = raw.strip().lower()
        hit = self._cache_get(key)
        if hit:
            return {"category": hit, "confidence": 0.99}

        text = ml_input_text(raw)
        if not text:
            return None
        try:
            model = _get_sentence_model()
            X = model.encode([text], show_progress_bar=False, normalize_embeddings=True)
            probs = self._classifier.predict_proba(X)[0]
            idx = int(np.argmax(probs))
            cat = str(self._classifier.classes_[idx])
            conf = float(probs[idx])
            if key:
                self._cache_set(key, cat)
            return {"category": cat, "confidence": conf}
        except Exception:
            return None

    def categorize(
        self,
        amount: float,
        category: Optional[str],
        vendor: Optional[str],
        historical_expenses: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        del amount
        if category and str(category).strip():
            c = str(category).strip()
            if c.lower() == "uncategorized":
                c = "Others"
            return c
        if historical_expenses is not None:
            self.train_from_expenses(historical_expenses)
        pred = self.predict_category(vendor=vendor)
        if pred and float(pred["confidence"]) >= self.confidence_threshold:
            chosen = str(pred["category"]).strip()
            if chosen.lower() == "uncategorized":
                return "Others"
            return chosen
        return self._rule(vendor)

    def reset_model(self) -> None:
        self._classifier = None
        self._cache.clear()
        self._cache_order.clear()
