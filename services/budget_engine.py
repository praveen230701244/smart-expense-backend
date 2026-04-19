"""
50/30/20 budget comparison: needs / wants / savings vs actual spending.
Falls back to expense-only signals when income is unknown or zero.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Heuristic mapping from category name → bucket for rule-of-thumb budgeting.
_NEED_HINTS = (
    "food",
    "grocer",
    "rent",
    "util",
    "bill",
    "transport",
    "health",
    "medical",
    "insurance",
    "fuel",
    "gas",
    "home",
    "housing",
    "education",
    "school",
    "loan",
    "emi",
)
_WANT_HINTS = (
    "entertain",
    "shop",
    "travel",
    "movie",
    "game",
    "subscription",
    "dining",
    "restaurant",
    "luxury",
    "hobby",
    "gift",
    "fashion",
)


def _bucket_for_category(name: str) -> str:
    n = (name or "").lower()
    for h in _NEED_HINTS:
        if h in n:
            return "needs"
    for h in _WANT_HINTS:
        if h in n:
            return "wants"
    return "mixed"


def _split_expenses_by_bucket(expenses: List[Dict[str, Any]]) -> Tuple[float, float, float]:
    """Returns (needs_total, wants_total, mixed_total)."""
    needs = wants = mixed = 0.0
    for e in expenses:
        amt = float(e.get("amount") or 0.0)
        cat = str(e.get("category") or "Others")
        b = _bucket_for_category(cat)
        if b == "needs":
            needs += amt
        elif b == "wants":
            wants += amt
        else:
            mixed += amt
    return round(needs, 2), round(wants, 2), round(mixed, 2)


def analyze_budget(
    income: float,
    fixed_expenses: float,
    expenses: List[Dict[str, Any]],
    category_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Compare actual spending to 50/30/20 targets when income > 0.
    Always returns a JSON-serializable dict (never raises).
    """
    total_exp = round(sum(float(e.get("amount") or 0.0) for e in (expenses or [])), 2)
    inc = float(income or 0.0)
    fixed = float(fixed_expenses or 0.0)

    needs_spend, wants_spend, mixed_spend = _split_expenses_by_bucket(expenses or [])
    # Split "mixed" proportionally toward needs/wants for reporting (50/50).
    needs_spend += mixed_spend * 0.5
    wants_spend += mixed_spend * 0.5

    overspending: List[Dict[str, Any]] = []
    suggestions: List[str] = []

    if inc <= 0:
        rows = sorted(category_rows or [], key=lambda x: float(x.get("total") or 0.0), reverse=True)
        for r in rows[:5]:
            c = str(r.get("category") or "")
            t = float(r.get("total") or 0.0)
            if total_exp > 0 and t / total_exp >= 0.25:
                overspending.append(
                    {
                        "category": c,
                        "amount": round(t, 2),
                        "reason": "Large share of total spending",
                    }
                )
        if not overspending and rows:
            overspending.append(
                {
                    "category": rows[0].get("category"),
                    "amount": float(rows[0].get("total") or 0.0),
                    "reason": "Top spending category",
                }
            )
        suggestions.append("Add your monthly income in Profile to unlock 50/30/20 budget comparison.")
        if total_exp <= 0:
            suggestions.append("No expenses recorded — upload transactions to analyze spending patterns.")
        return {
            "rule": "50-30-20",
            "profile_required": True,
            "income": None,
            "fixed_expenses": round(fixed, 2) if fixed else None,
            "total_expenses": total_exp,
            "savings_rate": None,
            "ideal_allocation": None,
            "actual_allocation_pct": None,
            "variance_vs_ideal": None,
            "overspending_categories": overspending,
            "improvement_suggestions": suggestions,
        }

    disposable = max(inc - fixed, 0.0)
    ideal_needs = inc * 0.5
    ideal_wants = inc * 0.3
    ideal_savings = inc * 0.2

    savings_rate = round((inc - total_exp) / inc, 4) if inc > 0 else 0.0

    actual_needs_pct = needs_spend / inc if inc > 0 else 0.0
    actual_wants_pct = wants_spend / inc if inc > 0 else 0.0
    implied_savings_pct = max(0.0, (inc - total_exp) / inc) if inc > 0 else 0.0

    variance = {
        "needs_vs_ideal_pct": round((actual_needs_pct - 0.5) * 100.0, 1),
        "wants_vs_ideal_pct": round((actual_wants_pct - 0.3) * 100.0, 1),
        "savings_vs_ideal_pct": round((implied_savings_pct - 0.2) * 100.0, 1),
    }

    if actual_needs_pct > 0.55:
        suggestions.append(
            f"Needs-related spend is about {actual_needs_pct*100:.0f}% of income (target ~50%). "
            "Review housing, utilities, and recurring bills."
        )
    if actual_wants_pct > 0.35:
        suggestions.append(
            f"Discretionary-style spend is about {actual_wants_pct*100:.0f}% of income (target ~30%). "
            "Try a 30-day pause on the top 'want' categories."
        )
    if implied_savings_pct < 0.15 and ideal_savings > 0:
        suggestions.append(
            "Savings are below the 20% guideline — automate a transfer on payday, even if small."
        )
    if total_exp > inc:
        suggestions.append("Total spending exceeds stated income — verify income, or reduce large categories first.")
    if fixed > 0 and disposable < total_exp * 0.5:
        suggestions.append("Fixed costs consume most of income; trimming subscriptions or refinancing may help.")

    # Overspending categories: share of income or vs ideal slice
    for r in sorted(category_rows or [], key=lambda x: float(x.get("total") or 0.0), reverse=True)[:6]:
        cat = str(r.get("category") or "")
        t = float(r.get("total") or 0.0)
        if inc > 0 and t / inc > 0.2:
            overspending.append(
                {
                    "category": cat,
                    "amount": round(t, 2),
                    "share_of_income_pct": round((t / inc) * 100.0, 1),
                    "reason": "Over 20% of monthly income",
                }
            )

    if not suggestions and total_exp > 0:
        suggestions.append("Your split is close to the 50/30/20 guideline — keep tracking month over month.")

    return {
        "rule": "50-30-20",
        "profile_required": False,
        "income": round(inc, 2),
        "fixed_expenses": round(fixed, 2),
        "total_expenses": total_exp,
        "savings_rate": savings_rate,
        "ideal_allocation": {
            "needs": round(ideal_needs, 2),
            "wants": round(ideal_wants, 2),
            "savings": round(ideal_savings, 2),
        },
        "actual_allocation_pct": {
            "needs": round(actual_needs_pct * 100.0, 1),
            "wants": round(actual_wants_pct * 100.0, 1),
            "implied_savings": round(implied_savings_pct * 100.0, 1),
        },
        "variance_vs_ideal": variance,
        "overspending_categories": overspending[:8],
        "improvement_suggestions": suggestions[:8],
    }
