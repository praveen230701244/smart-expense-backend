"""
Financial Co-Pilot: goals, user feedback learning, what-if & budget simulations.
"""
from typing import Any, Dict, List

from flask import Blueprint, current_app, g, jsonify, request

from services.insights_service import category_breakdown
from services.vendor_normalizer import normalize_vendor

copilot_bp = Blueprint("copilot", __name__)


@copilot_bp.route("/goals", methods=["GET"])
def list_goals():
    repo = current_app.extensions["repo"]
    goals = repo.list_goals(g.user_id)
    return jsonify({"status": "ok", "goals": goals})


@copilot_bp.route("/goals", methods=["POST"])
def create_goal():
    repo = current_app.extensions["repo"]
    data = request.get_json(silent=True) or {}
    try:
        target = float(data.get("targetMonthlySave") or data.get("target_monthly_save") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "targetMonthlySave required"}), 400
    if target <= 0:
        return jsonify({"error": "targetMonthlySave must be positive"}), 400
    title = data.get("title")
    currency = str(data.get("currency") or "INR")
    gid = repo.add_goal(g.user_id, title, target, currency)
    return jsonify({"status": "ok", "id": gid})


@copilot_bp.route("/feedback/category", methods=["POST"])
def feedback_category():
    repo = current_app.extensions["repo"]
    data = request.get_json(silent=True) or {}
    vendor = data.get("vendor") or data.get("vendor_norm")
    category = data.get("category")
    if not vendor or not category:
        return jsonify({"error": "vendor and category required"}), 400
    vn = normalize_vendor(str(vendor))
    if str(category).strip().lower() == "uncategorized":
        category = "Others"
    repo.upsert_feedback(g.user_id, vn, str(category).strip())
    return jsonify({"status": "ok"})


@copilot_bp.route("/expenses/<int:expense_id>/category", methods=["PATCH"])
def patch_expense_category(expense_id: int):
    """Update a row and record vendor→category feedback for future uploads."""
    repo = current_app.extensions["repo"]
    data = request.get_json(silent=True) or {}
    category = data.get("category")
    if not category or not str(category).strip():
        return jsonify({"error": "category required"}), 400
    cat = str(category).strip()
    if len(cat) > 80:
        return jsonify({"error": "category too long"}), 400

    row = repo.get_expense(g.user_id, expense_id)
    if not row:
        return jsonify({"error": "Not found"}), 404

    ok = repo.update_expense_category(g.user_id, expense_id, cat)
    if not ok:
        return jsonify({"error": "Update failed"}), 500

    vn = normalize_vendor(str(row.get("vendor") or ""))
    if vn and vn.lower() != "unknown":
        repo.upsert_feedback(g.user_id, vn, cat)

    return jsonify({"status": "ok", "id": expense_id, "category": cat})


@copilot_bp.route("/simulate/what-if", methods=["POST"])
def what_if():
    """Reduce selected categories by % → estimated monthly savings."""
    repo = current_app.extensions["repo"]
    expenses = repo.list_expenses(g.user_id)
    data = request.get_json(silent=True) or {}
    cuts: Dict[str, float] = data.get("cuts") or {}
    if not cuts:
        return jsonify({"error": "cuts map required, e.g. {\"Food\": 15}"}), 400

    breakdown = {r["category"]: r["total"] for r in category_breakdown(expenses)}
    months = max(1, len(set(str(e.get("date") or "")[:7] for e in expenses if e.get("date"))))
    savings = 0.0
    lines: List[str] = []
    for cat, pct in cuts.items():
        try:
            p = float(pct)
        except (TypeError, ValueError):
            continue
        tot = float(breakdown.get(cat, 0.0))
        if tot <= 0:
            continue
        monthly = tot / months
        saved = monthly * (min(max(p, 0.0), 90.0) / 100.0)
        savings += saved
        lines.append(f"Cut {cat} by {p:.0f}% → ~₹{saved:.0f}/mo saved")

    return jsonify(
        {
            "status": "ok",
            "estimatedMonthlySavings": round(savings, 2),
            "lines": lines,
        }
    )


@copilot_bp.route("/simulate/budget", methods=["POST"])
def budget_sim():
    """Caps per category vs current monthly avg."""
    repo = current_app.extensions["repo"]
    expenses = repo.list_expenses(g.user_id)
    data = request.get_json(silent=True) or {}
    caps: Dict[str, float] = data.get("caps") or {}
    if not caps:
        return jsonify({"error": "caps map required"}), 400

    breakdown = category_breakdown(expenses)
    by_cat = {r["category"]: r["total"] for r in breakdown}
    months = max(1, len(set(str(e.get("date") or "")[:7] for e in expenses if e.get("date"))))
    out = []
    total_saved = 0.0
    for cat, cap in caps.items():
        try:
            c = float(cap)
        except (TypeError, ValueError):
            continue
        spent = float(by_cat.get(cat, 0.0))
        monthly = spent / months
        if monthly <= c:
            out.append({"category": cat, "cap": c, "avgSpend": round(monthly, 2), "surplus": round(c - monthly, 2)})
            continue
        save = monthly - c
        total_saved += save
        out.append(
            {
                "category": cat,
                "cap": c,
                "avgSpend": round(monthly, 2),
                "monthlyOverspend": round(save, 2),
            }
        )

    return jsonify({"status": "ok", "categories": out, "totalMonthlyOverspend": round(total_saved, 2)})
