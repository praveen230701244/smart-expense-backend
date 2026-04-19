import os
from typing import Any, Dict, List

from flask import Blueprint, current_app, g, jsonify, request

from services.anomaly_service import detect_anomalies
from services.context_builder import ADVISOR_SYSTEM_STRICT, build_context, format_chat_user_prompt
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

_GREETINGS = frozenset({"hi", "hello", "hey", "hii", "hola", "yo", "sup", "greetings"})


def _is_greeting(message: str) -> bool:
    t = (message or "").lower().strip()
    if not t:
        return False
    t = t.rstrip("!.?,")
    if t in _GREETINGS:
        return True
    words = t.split()
    if not words:
        return False
    # "hi there", "hello friend" — short greeting-style openers
    if words[0] in _GREETINGS and len(words) <= 4:
        return True
    return False


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

    expenses = repo.list_expenses(user_id, limit=5000)
    goals = repo.list_goals(user_id)

    contamination = float(current_app.config.get("ANOMALY_CONTAMINATION", 0.08))
    context = build_context(user_id, repo, contamination=contamination)
    insights = _compute_insights(expenses)

    history = repo.recent_chat(user_id, limit=10)
    history_text = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history)

    user_prompt = format_chat_user_prompt(context, message, history_text)
    if _is_greeting(message):
        user_prompt = (
            "User greeted you. Respond briefly and warmly, then offer one way you can help with their money.\n\n"
            + user_prompt
        )

    repo.append_chat(user_id, "user", message)

    ai_service = current_app.extensions.get("ai_service") or current_app.extensions.get("openai_service")
    debug: Dict[str, Any] = {}

    if ai_service:
        try:
            print("Chat: calling Gemini…")
            raw = ai_service.generate_advice(ADVISOR_SYSTEM_STRICT, user_prompt, timeout=45.0)
            if raw and str(raw).strip():
                advice = str(raw).strip()
                repo.append_chat(user_id, "assistant", advice)
                return jsonify(
                    {
                        "advice": advice,
                        "source": "gemini",
                        "debug": {**debug, "result": "gemini_ok"},
                    }
                )
            print("⚠ Gemini failed → fallback triggered (empty or invalid response)")
            debug["reason"] = "empty_response"
        except Exception as e:
            print("Chat: Gemini error:", repr(e))
            print("⚠ Gemini failed → fallback triggered (exception)")
            debug["error"] = repr(e)
    else:
        print("Chat: ai_service extension missing — fallback")
        print("⚠ Gemini failed → fallback triggered (no ai_service)")
        debug["reason"] = "no_ai_service"

    if os.getenv("GEMINI_DEBUG_NO_FALLBACK", "").lower().strip() in ("1", "true", "yes"):
        print("GEMINI_DEBUG_NO_FALLBACK: returning error instead of fallback")
        return (
            jsonify(
                {
                    "error": "Gemini not responding",
                    "advice": None,
                    "source": "error",
                    "debug": debug,
                }
            ),
            503,
        )

    advice = _format_fallback_advice(message, insights, goals).strip()
    if not advice:
        advice = (
            "I'm having trouble reaching the AI model right now—try again in a moment. "
            "Your data is saved and I'll analyze it on the next request."
        )
    print("Chat: returning rule-based fallback")
    repo.append_chat(user_id, "assistant", advice)
    return jsonify(
        {
            "advice": advice,
            "source": "fallback",
            "debug": {**debug, "result": "fallback_rules"},
        }
    )
