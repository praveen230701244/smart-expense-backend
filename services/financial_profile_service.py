from typing import Any, Dict, List


def calculate_total_expense(expenses: List[Dict[str, Any]]) -> float:
    return round(sum(float(e.get("amount") or 0.0) for e in expenses), 2)


def calculate_savings(income: float, expenses: float) -> float:
    return round(float(income or 0.0) - float(expenses or 0.0), 2)


def calculate_savings_rate(savings: float, income: float) -> float:
    in_val = float(income or 0.0)
    if in_val <= 0:
        return 0.0
    return round(float(savings or 0.0) / in_val, 4)


def detect_risk(income: float, expenses: float, savings: float, savings_goal: float) -> Dict[str, Any]:
    notes: List[str] = []
    level = "stable"
    if float(expenses or 0.0) > float(income or 0.0):
        level = "high risk"
        notes.append("Expenses exceed income")
    if float(savings or 0.0) < float(savings_goal or 0.0):
        if level != "high risk":
            level = "needs improvement"
        notes.append("Savings are below goal")
    return {"level": level, "notes": notes}


def calculate_financial_health_score(income: float, expenses: float, savings_goal: float) -> int:
    income_val = max(float(income or 0.0), 0.0)
    if income_val <= 0:
        return 0
    expense_ratio = min(float(expenses or 0.0) / income_val, 2.0)
    savings = max(income_val - float(expenses or 0.0), 0.0)
    goal = max(float(savings_goal or 0.0), 0.0)
    goal_achievement = min((savings / goal), 1.0) if goal > 0 else 1.0
    score = (0.55 * (1 - min(expense_ratio, 1.0)) + 0.45 * goal_achievement) * 100
    return max(0, min(100, int(round(score))))
