"""Extended Co-Pilot insights (wasteful spend, MoM headline)."""
from typing import Any, Dict, List

from services.insights_service import growth_trends, spending_behavior


def wasteful_spending_summary(expenses: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not expenses:
        return {
            "discretionarySharePct": 0.0,
            "topWastefulCategories": [],
            "note": "Add transactions to analyze discretionary spend.",
        }

    total = sum(float(e.get("amount") or 0.0) for e in expenses)
    discretionary_labels = {"Food", "Shopping", "Transport", "Subscriptions", "Food & Drinks"}
    by_disc: Dict[str, float] = {}
    disc_total = 0.0
    for e in expenses:
        c = str(e.get("category") or "Others").strip()
        if c.lower() == "uncategorized":
            c = "Others"
        amt = float(e.get("amount") or 0.0)
        if c in discretionary_labels or any(x in c.lower() for x in ["food", "shop", "travel", "uber", "swiggy"]):
            disc_total += amt
            by_disc[c] = by_disc.get(c, 0.0) + amt

    pct = round((disc_total / total) * 100.0, 1) if total > 0 else 0.0
    top = sorted(by_disc.items(), key=lambda x: x[1], reverse=True)[:4]
    behavior = spending_behavior(expenses)

    note = "Discretionary-heavy" if pct >= 45 else "Balanced" if pct >= 25 else "Lean discretionary mix"
    if behavior.get("impulsiveSpendingScore", 0) >= 60:
        note += "; frequent small spends detected"

    return {
        "discretionarySharePct": pct,
        "topWastefulCategories": [{"category": k, "total": round(v, 2)} for k, v in top],
        "impulsiveSpendingScore": behavior.get("impulsiveSpendingScore"),
        "note": note,
    }


def mom_growth_headline(expenses: List[Dict[str, Any]]) -> str:
    g = growth_trends(expenses)
    pct = float(g.get("monthOverMonthGrowthPct") or 0.0)
    if abs(pct) < 1.0:
        return "Spending is flat month over month."
    if pct > 0:
        return f"Spending is up ~{pct:.0f}% vs last month."
    return f"Spending is down ~{abs(pct):.0f}% vs last month."
