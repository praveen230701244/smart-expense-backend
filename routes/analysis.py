from flask import Blueprint, current_app, g, jsonify

from services.anomaly_service import detect_anomalies
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


@analysis_bp.route("/expenses", methods=["GET"])
def get_expenses():
    repo = current_app.extensions["repo"]
    user_id = g.user_id
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

    return jsonify({"status": "ok", "expenses": expenses, "summary": summary})


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
