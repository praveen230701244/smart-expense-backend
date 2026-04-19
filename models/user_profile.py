from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class UserProfile:
    """User financial profile row (table: user_profile)."""

    user_id: str
    income: float
    fixed_expenses: float
    goals: str
    risk_level: str
    lifestyle: str
    savings_goal: float = 0.0
    currency: str = "INR"
    id: Optional[int] = None

    @staticmethod
    def from_row(row: Dict[str, Any]) -> "UserProfile":
        return UserProfile(
            id=int(row["id"]) if row.get("id") is not None else None,
            user_id=str(row.get("user_id") or ""),
            income=float(row.get("income") or 0.0),
            fixed_expenses=float(row.get("fixed_expenses") or 0.0),
            goals=str(row.get("goals") or ""),
            risk_level=str(row.get("risk_level") or "medium"),
            lifestyle=str(row.get("lifestyle") or ""),
            savings_goal=float(row.get("savings_goal") or 0.0),
            currency=str(row.get("currency") or "INR"),
        )

    def to_dict(self) -> Dict[str, Any]:
        inc = round(float(self.income or 0.0), 2)
        return {
            "id": self.id,
            "user_id": self.user_id,
            "income": inc,
            "monthly_income": inc,
            "fixed_expenses": round(float(self.fixed_expenses or 0.0), 2),
            "goals": self.goals or "",
            "risk_level": self.risk_level or "medium",
            "lifestyle": self.lifestyle or "",
            "savings_goal": round(float(self.savings_goal or 0.0), 2),
            "currency": self.currency or "INR",
            "financial_goal": self.goals or "",
        }
