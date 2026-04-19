from typing import Any, Dict, List, Tuple

from flask import Blueprint, current_app, g, jsonify

from services.ai_advisor import generate_ai_advice
from services.anomaly_service import detect_anomalies
from services.budget_engine import analyze_budget
from services.context_builder import build_context
from services.insights_extended import mom_growth_headline, wasteful_spending_summary
from services.insights_service import (
    category_breakdown,
    growth_trends,
    monthly_trend,
    risk_score,
    savings_suggestions,
    spending_behavior,
)
from services.prediction_service import forecast_next_month_cached


analysis_bp = Blueprint("analysis", __name__)


def _build_top_insights(summary: dict, expenses: list) -> list[str]:
    total = float(summary.get("totalExpenses") or 0.0)
    top_insights: list[str] = []

    breakdown = summary.get("categoryBreakdown") or []
    if breakdown and total > 0:
        top = breakdown[0]
        pct = round((float(top.get("total") or 0.0) / total) * 100.0, 0)
        top_insights.append(f"You spent {int(pct)}% on {top.get('category')}")

    top_insights.append(mom_growth_headline(expenses))

    behavior = summary.get("spendingBehavior") or {}
    wp = behavior.get("weekendSpendPct")
    wd = behavior.get("weekdaySpendPct")
    if wp is not None and wd is not None and total > 0:
        top_insights.append(f"Weekend vs weekday: {wp:.0f}% weekend · {wd:.0f}% weekday")

    subs = behavior.get("subscriptionCandidates") or []
    if subs:
        s0 = subs[0]
        top_insights.append(
            f"Possible subscription: {s0.get('vendor')} ~₹{s0.get('avgAmount')} every ~{s0.get('avgGapDays')}d"
        )

    waste = summary.get("wastefulSummary") or {}
    if waste.get("discretionarySharePct") is not None:
        top_insights.append(
            f"~{waste['discretionarySharePct']}% of spend looks discretionary ({waste.get('note', '')})"
        )

    anomalies = summary.get("anomalies") or []
    if anomalies:
        a = anomalies[0]
        top_insights.append(f"Unusual expense: ₹{a.get('amount')} at {a.get('vendor')}")

    risk = summary.get("riskScore")
    if isinstance(risk, (int, float)):
        top_insights.append(f"Risk score: {int(risk)}/100")

    return top_insights[:6]


def _compute_full_summary(user_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Shared /expenses + /analyze summary (existing fields unchanged)."""
    repo = current_app.extensions["repo"]
    expenses = repo.list_expenses(user_id)
    contamination = float(current_app.config.get("ANOMALY_CONTAMINATION", 0.08))
    fc_store = current_app.extensions.get("forecast_cache") or {}
    fc_order = current_app.extensions.get("forecast_cache_order") or []
    an_store = current_app.extensions.get("anomaly_cache") or {}
    an_order = current_app.extensions.get("anomaly_cache_order") or []

    total = round(sum(float(e.get("amount") or 0.0) for e in expenses), 2)
    category_rows = category_breakdown(expenses)
    top_category = category_rows[0] if category_rows else {"category": "N/A", "total": 0.0}
    top_cat_total = float(top_category.get("total") or 0.0)
    top_category_pct = round((top_cat_total / total) * 100.0, 1) if total > 0 else 0.0
    monthly_rows = monthly_trend(expenses)
    prediction = forecast_next_month_cached(
        expenses, 1, fc_store, fc_order, max_entries=64
    )
    anomalies = detect_anomalies(
        expenses,
        contamination=contamination,
        cache_store=an_store,
        cache_order=an_order,
    )
    growth = growth_trends(expenses)
    risk = risk_score(expenses, anomalies, growth)
    suggestions = savings_suggestions(expenses, growth, risk)
    behavior = spending_behavior(expenses)
    wasteful = wasteful_spending_summary(expenses)
    mom_pct = float((growth or {}).get("monthOverMonthGrowthPct") or 0.0)

    summary = {
        "totalExpenses": total,
        "topCategory": top_category,
        "topCategorySharePct": top_category_pct,
        "categoryBreakdown": category_rows,
        "monthlyTrend": monthly_rows,
        "prediction": prediction,
        "anomalies": anomalies,
        "growthTrends": growth,
        "savingsSuggestions": suggestions,
        "riskScore": risk,
        "spendingBehavior": behavior,
        "wastefulSummary": wasteful,
        "momHeadline": mom_growth_headline(expenses),
        "insightsEngine": {
            "topCategoryPct": top_category_pct,
            "monthOverMonthGrowthPct": mom_pct,
            "weekendVsWeekday": {
                "weekendSpendPct": behavior.get("weekendSpendPct"),
                "weekdaySpendPct": behavior.get("weekdaySpendPct"),
            },
            "wasteful": wasteful,
            "subscriptionCandidates": behavior.get("subscriptionCandidates") or [],
            "anomalyCount": len(anomalies or []),
        },
    }
    return expenses, summary


@analysis_bp.route("/expenses", methods=["GET"])
def get_expenses():
    user_id = g.user_id
    expenses, summary = _compute_full_summary(user_id)
    return jsonify({"status": "ok", "expenses": expenses, "summary": summary})


@analysis_bp.route("/analyze", methods=["GET"])
def analyze():
    """
    Same payload as /expenses plus profile_used, ai_advice, budget_analysis.
    Safe when profile or AI is missing — always returns valid JSON.
    """
    user_id = g.user_id
    try:
        expenses, summary = _compute_full_summary(user_id)
    except Exception:
        expenses, summary = [], {}

    repo = current_app.extensions["repo"]
    profile_used = False
    try:
        profile_used = repo.get_user_profile(user_id) is not None
    except Exception:
        profile_used = False

    contamination = float(current_app.config.get("ANOMALY_CONTAMINATION", 0.08))
    context: dict = {}
    try:
        context = build_context(user_id, repo, contamination=contamination)
    except Exception:
        context = {
            "mode": "basic",
            "user_id": user_id,
            "profile": None,
            "expenses": expenses,
            "category_breakdown": summary.get("categoryBreakdown") or [],
            "categories": summary.get("categoryBreakdown") or [],
            "total_expenses": float(summary.get("totalExpenses") or 0.0),
            "total_expense": float(summary.get("totalExpenses") or 0.0),
            "trends": summary.get("monthlyTrend") or [],
            "goals": "unknown",
            "risk": "unknown",
        }

    gemini = current_app.extensions.get("ai_service") or current_app.extensions.get("openai_service")
    try:
        ai_advice = generate_ai_advice(context, gemini=gemini, timeout_seconds=22.0)
    except Exception as e:
        print("analyze: generate_ai_advice failed:", repr(e))
        fb = "Advice temporarily unavailable. Your expense summary is still shown above."
        ai_advice = {
            "advice": fb,
            "source": "fallback",
            "summary": fb,
            "debug": {"error": repr(e)},
        }

    income_val = 0.0
    fixed_val = 0.0
    try:
        prof = repo.get_user_profile(user_id) if profile_used else None
        if prof:
            income_val = float(prof.get("income") or prof.get("monthly_income") or 0.0)
            fixed_val = float(prof.get("fixed_expenses") or 0.0)
    except Exception:
        income_val = 0.0
        fixed_val = 0.0

    try:
        budget_analysis = analyze_budget(
            income_val,
            fixed_val,
            expenses,
            summary.get("categoryBreakdown") or [],
        )
    except Exception:
        budget_analysis = {
            "rule": "50-30-20",
            "profile_required": not profile_used,
            "error": "budget_unavailable",
            "improvement_suggestions": ["Budget analysis temporarily unavailable."],
        }

    return jsonify(
        {
            "status": "ok",
            "expenses": expenses,
            "summary": summary,
            "profile_used": profile_used,
            "ai_advice": ai_advice,
            "budget_analysis": budget_analysis,
        }
    )


@analysis_bp.route("/insights", methods=["GET"])
def get_insights():
    repo = current_app.extensions["repo"]
    user_id = g.user_id
    expenses = repo.list_expenses(user_id)
    contamination = float(current_app.config.get("ANOMALY_CONTAMINATION", 0.08))
    an_store = current_app.extensions.get("anomaly_cache") or {}
    an_order = current_app.extensions.get("anomaly_cache_order") or []

    total = round(sum(float(e.get("amount") or 0.0) for e in expenses), 2)
    category_rows = category_breakdown(expenses)
    anomalies = detect_anomalies(
        expenses,
        contamination=contamination,
        cache_store=an_store,
        cache_order=an_order,
    )
    growth = growth_trends(expenses)
    risk = risk_score(expenses, anomalies, growth)
    wasteful = wasteful_spending_summary(expenses)
    behavior = spending_behavior(expenses)

    summary = {
        "totalExpenses": total,
        "categoryBreakdown": category_rows,
        "anomalies": anomalies,
        "riskScore": risk,
        "wastefulSummary": wasteful,
        "spendingBehavior": behavior,
    }

    return jsonify({"topInsights": _build_top_insights(summary, expenses)})
