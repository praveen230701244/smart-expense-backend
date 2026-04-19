"""
Structured financial context for Gemini (expenses include PDF/OCR rows already in DB).
Never attaches raw PDF text — only aggregates, categories, trends, profile.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from services.anomaly_service import detect_anomalies
from services.insights_service import category_breakdown, growth_trends, monthly_trend, risk_score


def _source_counts(expenses: List[Dict[str, Any]]) -> Dict[str, int]:
    c: Counter = Counter()
    for e in expenses:
        s = str(e.get("source") or "unknown").strip().lower() or "unknown"
        c[s] += 1
    return dict(c)


def _merge_goals(profile: Optional[Dict[str, Any]], repo, user_id: str) -> str:
    parts: List[str] = []
    if profile:
        g = (profile.get("goals") or profile.get("financial_goal") or "").strip()
        if g:
            parts.append(g)
    try:
        for row in repo.list_goals(user_id):
            t = row.get("title") or "Goal"
            amt = row.get("target_monthly_save") or 0
            parts.append(f"Save ₹{amt}/mo toward {t}")
    except Exception:
        pass
    return "; ".join(parts) if parts else "none set"


def _risk_label(profile: Optional[Dict[str, Any]], score: Any) -> str:
    pr = ""
    if profile:
        pr = str(profile.get("risk_level") or "").strip()
    try:
        sc = int(round(float(score or 0)))
    except (TypeError, ValueError):
        sc = 0
    if pr:
        return f"profile={pr}, computed_score={sc}/100"
    return f"computed_score={sc}/100"


def build_context(user_id: str, repo, contamination: float = 0.08) -> Dict[str, Any]:
    """
    Returns a single structured dict for AI (and backward-compatible keys).

    Fields:
      income, total_expense, categories, trends, goals, risk (string),
      risk_score (int), source_counts, mode, profile, expenses, ...
    """
    profile: Optional[Dict[str, Any]] = None
    try:
        profile = repo.get_user_profile(user_id)
    except Exception:
        profile = None

    try:
        expenses: List[Dict[str, Any]] = list(repo.list_expenses(user_id) or [])
    except Exception:
        expenses = []

    total = round(sum(float(e.get("amount") or 0.0) for e in expenses), 2)
    cats = category_breakdown(expenses)
    trends = monthly_trend(expenses, months_back=12)

    income: Optional[float] = None
    if profile:
        try:
            income = float(profile.get("income") or profile.get("monthly_income") or 0.0) or None
        except (TypeError, ValueError):
            income = None
        if income is not None and income <= 0:
            income = None

    risk_sc: Any = 0
    try:
        an = detect_anomalies(expenses, contamination=contamination)
        gr = growth_trends(expenses)
        risk_sc = risk_score(expenses, an, gr)
    except Exception:
        risk_sc = 0

    goals_str = _merge_goals(profile, repo, user_id)
    risk_str = _risk_label(profile, risk_sc)
    src = _source_counts(expenses)

    base: Dict[str, Any] = {
        "user_id": user_id,
        "income": income,
        "total_expense": total,
        "total_expenses": total,
        "categories": cats,
        "category_breakdown": cats,
        "trends": trends,
        "monthly_trend": trends,
        "goals": goals_str,
        "risk": risk_str,
        "risk_score": int(risk_sc) if risk_sc is not None else 0,
        "profile_risk_level": (profile or {}).get("risk_level") if profile else None,
        "source_counts": src,
        "expenses": expenses,
        "profile": profile,
        "mode": "enriched" if profile else "basic",
    }
    return base


# --- Prompts: structured data only (no raw PDF/OCR text) ---

ADVISOR_SYSTEM_STRICT = """You are a financial AI advisor.

STRICT RULES:
- Maximum 4 lines only
- MUST include ₹ values
- MUST include % values
- MUST use provided DATA (no guessing)
- Give exactly 1 actionable tip

Do NOT give generic advice.
Use only numbers from DATA.
"""


def format_data_block(ctx: Dict[str, Any]) -> str:
    """Single structured block for Gemini (totals, categories, trends — no raw uploads)."""
    inc = ctx.get("income")
    inc_line = f"₹{float(inc):.2f}/month (profile)" if inc is not None else "not set in profile"

    total = float(ctx.get("total_expense") or ctx.get("total_expenses") or 0.0)
    cats = ctx.get("categories") or ctx.get("category_breakdown") or []
    cat_parts = [f"{c.get('category')}: ₹{float(c.get('total') or 0):.2f}" for c in cats[:10]]
    cat_line = "; ".join(cat_parts) if cat_parts else "none"

    trends = ctx.get("trends") or ctx.get("monthly_trend") or []
    trend_parts = [f"{t.get('month')}: ₹{float(t.get('total') or 0):.2f}" for t in trends[-8:]]
    trend_line = "; ".join(trend_parts) if trend_parts else "not enough months"

    goals = ctx.get("goals") or "none"
    risk = ctx.get("risk") or "unknown"

    src = ctx.get("source_counts") or {}
    src_line = ", ".join(f"{k}={v} rows" for k, v in sorted(src.items())) or "unknown"

    lifestyle = ""
    if ctx.get("profile") and isinstance(ctx["profile"], dict):
        lifestyle = str(ctx["profile"].get("lifestyle") or "").strip()

    lines = [
        f"Income: {inc_line}",
        f"Total spend (sum of stored transactions): ₹{total:.2f}",
        f"Categories (structured): {cat_line}",
        f"Monthly trend (recent): {trend_line}",
        f"Goals: {goals}",
        f"Risk: {risk}",
        f"Rows by import source (PDF/OCR/manual/csv counts — not raw text): {src_line}",
    ]
    if lifestyle:
        lines.append(f"Lifestyle note: {lifestyle}")

    return "\n".join(lines)


def format_analyze_user_prompt(ctx: Dict[str, Any]) -> str:
    """User prompt for /analyze advice — DATA only."""
    return (
        "DATA:\n"
        + format_data_block(ctx)
        + "\n\nGive your STRICT FORMAT response for this user."
    )


def format_chat_user_prompt(
    ctx: Dict[str, Any],
    user_message: str,
    history_text: str,
) -> str:
    """Chat: structured DATA + conversation — never raw PDF text."""
    return (
        format_data_block(ctx)
        + "\n\nRecent chat:\n"
        + (history_text or "(none)")
        + "\n\nUser message:\n"
        + (user_message or "").strip()
        + "\n\nAnswer using DATA above. Follow STRICT FORMAT."
    )
