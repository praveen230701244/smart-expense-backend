from typing import Any, Dict, List

from flask import Blueprint, current_app, g, jsonify, request

from services.anomaly_service import detect_anomalies
from services.insights_extended import mom_growth_headline
from services.insights_service import (
    category_breakdown,
    growth_trends,
    monthly_trend,
    risk_score,
    savings_suggestions,
    spending_behavior,
)
from services.prediction_service import forecast_next_month

chatbot_bp = Blueprint("chatbot", __name__)

SYSTEM_PROMPT = (
    "You are a smart financial advisor.\n"
    "Always respond to ANY message.\n"
    "Use the user's expense data and goals below.\n"
    "Give exactly:\n"
    "- 2–4 short lines\n"
    "- include ₹ amounts and % where relevant\n"
    "- 1 concrete actionable tip\n"
    "- conversational, warm tone\n"
    "If the user says hi/hello, greet and suggest one useful question."
)


def _compute_insights(expenses: List[Dict[str, Any]]) -> Dict[str, Any]:
    contamination = float(current_app.config.get("ANOMALY_CONTAMINATION", 0.08))
    categories = category_breakdown(expenses)
    trend = monthly_trend(expenses, months_back=12)
    anomalies = detect_anomalies(expenses, contamination=contamination)
    growth = growth_trends(expenses)
    risk = risk_score(expenses, anomalies, growth)
    suggestions = savings_suggestions(expenses, growth, risk)
    prediction = forecast_next_month(expenses)
    total = round(sum(float(e.get("amount") or 0.0) for e in expenses), 2)
    behavior = spending_behavior(expenses)
    return {
        "totalExpenses": total,
        "categoryTotals": categories,
        "overspendingCategories": categories[:3],
        "monthlyTrend": trend,
        "anomalies": anomalies,
        "growthTrends": growth,
        "riskScore": risk,
        "savingsSuggestions": suggestions,
        "prediction": prediction,
        "behavior": behavior,
        "momHeadline": mom_growth_headline(expenses),
    }


def _format_fallback_advice(message: str, insights: Dict[str, Any], goals: List[Dict[str, Any]]) -> str:
    overspending = insights.get("overspendingCategories") or []
    growth = insights.get("growthTrends") or {}
    risk = int(insights.get("riskScore") or 0)
    total = float(insights.get("totalExpenses") or 0.0)
    user_msg = (message or "").lower()

    lines: List[str] = []
    if overspending and total > 0:
        top = overspending[0]
        pct = round((float(top.get("total") or 0) / total) * 100.0, 0)
        lines.append(
            f"You're leaning on {top['category']} (₹{top['total']:.0f}, ~{pct:.0f}% of spend)."
        )
    lines.append(insights.get("momHeadline") or "")
    if growth.get("spendingSpikeDetected"):
        lines.append(f"Heads up: spend jumped ~{growth.get('monthOverMonthGrowthPct', 0):.0f}% vs last month.")
    lines.append(f"Risk score is {risk}/100—tighten the top category first.")
    if goals:
        g0 = goals[0]
        tgt = g0.get("target_monthly_save") or g0.get("targetMonthlySave") or 0
        lines.append(f"Goal: save ₹{tgt}/mo—trim discretionary 10–15% to get closer.")

    if "waste" in user_msg or "wasting" in user_msg:
        if overspending:
            lines.append(f"Biggest bucket: {overspending[0]['category']}.")
    if not lines or all(not str(x).strip() for x in lines):
        lines = ["Upload a few more weeks of data and I'll pinpoint where to cut first."]
    return "\n".join([str(x) for x in lines if str(x).strip()]).strip()


@chatbot_bp.route("/chat", methods=["POST"])
def chat():
    repo = current_app.extensions["repo"]
    user_id = g.user_id
    payload = request.get_json(silent=True) or {}

    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Missing `message` in request body."}), 400

    msg_low = message.lower().strip()
    if msg_low in {"hi", "hello", "hey", "hii", "hola"}:
        return jsonify(
            {
                "advice": "Hey! I’m your AI Financial Co-Pilot.\n"
                "I can tell you where money leaks and how much you could save.\n"
                "Try: “Where am I overspending?” or “Help me save ₹5000/month.”"
            }
        )

    expenses = repo.list_expenses(user_id, limit=5000)
    goals = repo.list_goals(user_id)

    if not expenses:
        advice = (
            "I don’t see any expenses yet—upload a CSV/PDF (or a receipt photo) "
            "and I’ll build your personalized plan."
        )
        repo.append_chat(user_id, "user", message)
        repo.append_chat(user_id, "assistant", advice)
        return jsonify({"advice": advice})

    insights = _compute_insights(expenses)

    history = repo.recent_chat(user_id, limit=10)
    history_text = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history)

    repo.append_chat(user_id, "user", message)

    try:
        openai_service = current_app.extensions.get("openai_service")
        if openai_service:
            overspending_str = "\n".join(
                [
                    f"- {i['category']} (total: {i['total']})"
                    for i in (insights.get("overspendingCategories") or [])
                ]
            )
            monthly_str = "\n".join(
                [f"- {m['month']}: {m['total']}" for m in (insights.get("monthlyTrend") or [])][-12:]
            )
            growth = insights.get("growthTrends") or {}
            risk = insights.get("riskScore")
            anomalies = insights.get("anomalies") or []
            suggestions = insights.get("savingsSuggestions") or []
            prediction = insights.get("prediction") or {}
            prediction_ci = prediction.get("confidenceInterval") or {}
            behavior = insights.get("behavior") or {}
            goals_str = "\n".join(
                f"- Save ₹{g.get('target_monthly_save') or g.get('targetMonthlySave')}/mo: {g.get('title') or 'Goal'}"
                for g in goals
            )

            user_prompt = (
                f"Recent chat:\n{history_text or '(none)'}\n\n"
                f"User message: {message}\n\n"
                f"Data:\n"
                f"- Total expenses: ₹{sum(float(e.get('amount') or 0.0) for e in expenses):.2f}\n"
                f"- Top categories:\n{overspending_str or '- N/A'}\n"
                f"- Monthly totals:\n{monthly_str or '- N/A'}\n"
                f"- Growth: {growth}\n"
                f"- Risk: {risk}/100\n"
                f"- Budget health: {behavior.get('budgetHealth')}\n"
                f"- Weekend/weekday %: {behavior.get('weekendSpendPct')}/{behavior.get('weekdaySpendPct')}\n"
                f"- Subscriptions guess: {behavior.get('subscriptionCandidates')[:3]}\n"
                f"- MoM headline: {insights.get('momHeadline')}\n"
                f"- Anomalies: {len(anomalies)}\n"
                f"- Savings ideas: {suggestions[:3]}\n"
                f"- Forecast: {prediction.get('predictedTotal')} (range {prediction_ci.get('lower')}-{prediction_ci.get('upper')})\n"
                f"- Goals:\n{goals_str or '- None set'}\n"
            )

            advice = openai_service.generate_advice(SYSTEM_PROMPT, user_prompt)
            if advice and str(advice).strip():
                advice = str(advice).strip()
                repo.append_chat(user_id, "assistant", advice)
                return jsonify({"advice": advice})
    except Exception:
        pass

    advice = _format_fallback_advice(message, insights, goals).strip()
    if not advice:
        advice = (
            "I’m having trouble reaching the AI model right now—try again in a moment. "
            "Your data is saved and I’ll analyze it on the next request."
        )
    repo.append_chat(user_id, "assistant", advice)
    return jsonify({"advice": advice})
