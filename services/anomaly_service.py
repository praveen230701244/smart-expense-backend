from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sklearn.ensemble import IsolationForest


def _anomaly_key(expenses: List[Dict[str, Any]], contamination: float) -> Tuple[Any, ...]:
    amounts = tuple(round(float(e.get("amount") or 0.0), 2) for e in expenses)
    ids = tuple(e.get("id") for e in expenses)
    return (amounts, ids, round(float(contamination), 4))


def detect_anomalies(
    expenses: List[Dict[str, Any]],
    contamination: float = 0.08,
    cache_store: Optional[Dict[Any, List[Dict[str, Any]]]] = None,
    cache_order: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Detect unusual transactions using Isolation Forest on amount values.
    Returns frontend-friendly anomaly objects.
    """
    if len(expenses) < 8:
        return []

    if cache_store is not None and cache_order is not None:
        from services.cache_utils import bounded_cache_get

        k = _anomaly_key(expenses, contamination)

        def _compute() -> List[Dict[str, Any]]:
            return _detect_anomalies_compute(expenses, contamination)

        return bounded_cache_get(cache_store, cache_order, k, _compute, max_entries=48)

    return _detect_anomalies_compute(expenses, contamination)


def _detect_anomalies_compute(expenses: List[Dict[str, Any]], contamination: float) -> List[Dict[str, Any]]:
    amounts = np.array([[float(e.get("amount") or 0.0)] for e in expenses], dtype=float)
    if np.allclose(amounts, amounts[0]):
        return []

    model = IsolationForest(
        n_estimators=120,
        contamination=min(max(contamination, 0.01), 0.25),
        random_state=42,
    )
    labels = model.fit_predict(amounts)  # -1 anomaly, 1 normal
    scores = model.decision_function(amounts)

    anomalies: List[Dict[str, Any]] = []
    for i, (label, score) in enumerate(zip(labels, scores)):
        if int(label) != -1:
            continue
        exp = expenses[i]
        anomalies.append(
            {
                "expenseId": exp.get("id"),
                "amount": round(float(exp.get("amount") or 0.0), 2),
                "date": exp.get("date"),
                "vendor": exp.get("vendor") or "Unknown Vendor",
                "category": exp.get("category") or "Others",
                "severity": round(float(max(0.0, -score)), 4),
            }
        )

    anomalies.sort(key=lambda x: (x["severity"], x["amount"]), reverse=True)
    return anomalies[:20]


