from typing import Dict

from flask import Blueprint, current_app, g, jsonify, request

from models.user_profile import UserProfile

profile_bp = Blueprint("profile", __name__)

ALLOWED_RISK_LEVELS = {"low", "medium", "high"}


def _as_non_negative_float(payload, key: str):
    val = payload.get(key, 0)
    try:
        num = float(val)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a valid number")
    if num < 0:
        raise ValueError(f"{key} cannot be negative")
    return num


def _default_profile_payload(user_id: str) -> Dict:
    return {
        "user_id": user_id,
        "income": 0.0,
        "monthly_income": 0.0,
        "fixed_expenses": 0.0,
        "goals": "",
        "risk_level": "medium",
        "lifestyle": "",
        "savings_goal": 0.0,
        "currency": "INR",
        "financial_goal": "",
    }


@profile_bp.route("/profile", methods=["GET"])
def get_profile():
    repo = current_app.extensions["repo"]
    user_id = g.user_id
    profile = repo.get_user_profile(user_id)
    if not profile:
        return jsonify({"exists": False, "profile": _default_profile_payload(user_id)})
    return jsonify({"exists": True, "profile": profile})


@profile_bp.route("/profile", methods=["POST"])
def upsert_profile():
    repo = current_app.extensions["repo"]
    user_id = g.user_id
    payload = request.get_json(silent=True) or {}

    income_raw = payload.get("income", payload.get("monthly_income"))
    try:
        if income_raw is None:
            raise ValueError("income or monthly_income is required")
        monthly_income = float(income_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "income must be a valid number"}), 400
    if monthly_income <= 0:
        return jsonify({"error": "income must be greater than 0"}), 400

    try:
        fixed_expenses = _as_non_negative_float(payload, "fixed_expenses")
        savings_goal = _as_non_negative_float(payload, "savings_goal")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    risk_level = str(payload.get("risk_level") or "medium").strip().lower()
    if risk_level not in ALLOWED_RISK_LEVELS:
        return jsonify({"error": "risk_level must be one of: low, medium, high"}), 400

    goals = str(payload.get("goals") or "").strip()
    financial_goal = str(payload.get("financial_goal") or "").strip()
    if not goals and financial_goal:
        goals = financial_goal
    elif goals and financial_goal and financial_goal not in goals:
        goals = f"{goals}; {financial_goal}"

    lifestyle = str(payload.get("lifestyle") or "").strip()

    profile = UserProfile(
        user_id=user_id,
        income=monthly_income,
        fixed_expenses=fixed_expenses,
        goals=goals,
        risk_level=risk_level,
        lifestyle=lifestyle,
        savings_goal=savings_goal,
        currency=str(payload.get("currency") or "INR").strip() or "INR",
    )
    stored = repo.upsert_user_profile(profile)
    return jsonify({"status": "ok", "profile": stored})
