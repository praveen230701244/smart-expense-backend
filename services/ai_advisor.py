from __future__ import annotations
from typing import Any, Dict, Optional

from services.context_builder import ADVISOR_SYSTEM_STRICT, format_analyze_user_prompt
from services.gemini_service import GeminiService


def _fallback_advice(context: Dict[str, Any]) -> str:
    total = float(context.get("total_expense") or context.get("total_expenses") or 0.0)
    cats = context.get("categories") or context.get("category_breakdown") or []
    prof = context.get("profile")

    lines = []

    if total <= 0:
        return (
            "No expense data yet. Add transactions to get ₹ insights and % breakdown."
        )

    if cats:
        top = cats[0]
        pct = round((float(top.get("total") or 0) / total) * 100.0, 0)
        lines.append(f"Top spend: {top.get('category')} (~{pct}% of ₹{total:.0f}).")

    if prof:
        income = float(prof.get("income") or prof.get("monthly_income") or 0.0)
        if income > 0:
            savings = income - total
            rate = round((savings / income) * 100, 0)
            lines.append(f"Savings: ₹{savings:.0f} ({rate}%).")
        goals = (prof.get("goals") or "").strip()
        if goals:
            lines.append(f"Tip: Cut 10% discretionary to reach {goals[:80]}.")
    else:
        lines.append("Add income in profile for better analysis.")

    if len(cats) > 1:
        lines.append(f"Watch {cats[1].get('category')} expenses.")

    return "\n".join(lines[:4])


def generate_ai_advice(
    context: Dict[str, Any],
    gemini: Optional[GeminiService] = None,
    timeout_seconds: float = 22.0,
) -> Dict[str, Any]:

    debug = {
        "gemini_attempted": gemini is not None and getattr(gemini, "client", None) is not None
    }

    # 🔥 FORCE Gemini to use data properly
    user_prompt = f"""
Use ONLY the financial DATA below.
Do NOT assume anything.

{format_analyze_user_prompt(context)}
"""

    text: Optional[str] = None

    try:
        if gemini is not None:
            text = gemini.generate_advice(
                ADVISOR_SYSTEM_STRICT,
                user_prompt,
                timeout=timeout_seconds
            )
    except Exception as e:
        print("Gemini error:", repr(e))
        debug["error"] = str(e)

    if text and text.strip():
        advice = text.strip()
        return {
            "advice": advice,
            "summary": advice,
            "source": "gemini",
            "confidence": "high",
            "debug": {**debug, "result": "gemini_ok"},
        }

    print("⚠ Gemini failed → fallback triggered")

    fallback = _fallback_advice(context)

    return {
        "advice": fallback,
        "summary": fallback,
        "source": "fallback",
        "confidence": "low",
        "debug": {**debug, "result": "fallback"},
    }