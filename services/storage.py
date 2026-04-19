import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from models.user_profile import UserProfile


@dataclass(frozen=True)
class Expense:
    user_id: str
    amount: float
    category: str
    expense_date: str
    vendor: str
    source: str
    upload_url: Optional[str] = None


class ExpenseRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        cur = conn.execute("PRAGMA table_info(expenses)")
        cols = {r[1] for r in cur.fetchall()}
        if "user_id" not in cols:
            conn.execute("ALTER TABLE expenses ADD COLUMN user_id TEXT DEFAULT 'legacy'")
        conn.execute("DROP INDEX IF EXISTS unique_expense")
        conn.execute("DROP INDEX IF EXISTS unique_expense_user")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS unique_expense_user
            ON expenses(user_id, amount, expense_date, vendor)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS category_feedback (
                user_id TEXT NOT NULL,
                vendor_norm TEXT NOT NULL,
                category TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, vendor_norm)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS financial_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                title TEXT,
                target_monthly_save REAL NOT NULL,
                currency TEXT DEFAULT 'INR',
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_messages(user_id, id DESC)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                income REAL NOT NULL DEFAULT 0,
                fixed_expenses REAL NOT NULL DEFAULT 0,
                goals TEXT NOT NULL DEFAULT '',
                risk_level TEXT NOT NULL DEFAULT 'medium',
                lifestyle TEXT NOT NULL DEFAULT '',
                savings_goal REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'INR'
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_profile_user ON user_profile(user_id)"
        )

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT 'legacy',
                amount REAL,
                category TEXT,
                expense_date TEXT,
                vendor TEXT,
                source TEXT,
                upload_url TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
            )
            self._migrate(conn)

    def count(self, user_id: str) -> int:
        with self._connect() as conn:
            result = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE user_id = ?", (user_id,)
            ).fetchone()
            return int(result[0]) if result else 0

    def count_all(self) -> int:
        with self._connect() as conn:
            result = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()
            return int(result[0]) if result else 0

    def add_expenses(self, user_id: str, expenses: Iterable[Expense]) -> int:
        inserted = 0
        with self._connect() as conn:
            for e in expenses:
                try:
                    conn.execute(
                        """
                    INSERT INTO expenses (user_id, amount, category, expense_date, vendor, source, upload_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            user_id,
                            e.amount,
                            e.category,
                            e.expense_date,
                            e.vendor,
                            e.source,
                            e.upload_url,
                        ),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    continue
        return inserted

    def get_expense(self, user_id: str, expense_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, amount, category, expense_date AS date, vendor, source, upload_url
                FROM expenses WHERE user_id = ? AND id = ?
                """,
                (user_id, expense_id),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["category"] = d.get("category") or "Others"
        if str(d.get("category", "")).strip().lower() == "uncategorized":
            d["category"] = "Others"
        return d

    def update_expense_category(self, user_id: str, expense_id: int, category: str) -> bool:
        cat = (category or "").strip() or "Others"
        if cat.lower() == "uncategorized":
            cat = "Others"
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE expenses SET category = ? WHERE id = ? AND user_id = ?",
                (cat, expense_id, user_id),
            )
            return (cur.rowcount or 0) > 0

    def list_expenses(self, user_id: str, limit: int = 5000) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, amount, category, expense_date AS date, vendor, source, upload_url
                FROM expenses WHERE user_id = ? ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["category"] = d.get("category") or "Others"
            if str(d.get("category", "")).strip().lower() == "uncategorized":
                d["category"] = "Others"
            out.append(d)
        return out

    def clear_all(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM expenses WHERE user_id = ?", (user_id,))

    def clear_user_data(self, user_id: str) -> None:
        """Full reset for one user (expenses + chat + goals optional)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM expenses WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM chat_messages WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM financial_goals WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM category_feedback WHERE user_id = ?", (user_id,))

    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, income, fixed_expenses, goals, risk_level, lifestyle,
                       savings_goal, currency
                FROM user_profile WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return None
        p = UserProfile.from_row(dict(row))
        return p.to_dict()

    def upsert_user_profile(self, profile: UserProfile) -> Dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_profile (
                    user_id, income, fixed_expenses, goals, risk_level, lifestyle,
                    savings_goal, currency
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    income = excluded.income,
                    fixed_expenses = excluded.fixed_expenses,
                    goals = excluded.goals,
                    risk_level = excluded.risk_level,
                    lifestyle = excluded.lifestyle,
                    savings_goal = excluded.savings_goal,
                    currency = excluded.currency
                """,
                (
                    profile.user_id,
                    float(profile.income or 0.0),
                    float(profile.fixed_expenses or 0.0),
                    str(profile.goals or ""),
                    str(profile.risk_level or "medium"),
                    str(profile.lifestyle or ""),
                    float(profile.savings_goal or 0.0),
                    str(profile.currency or "INR"),
                ),
            )
        stored = self.get_user_profile(profile.user_id)
        return stored or profile.to_dict()

    def get_feedback_category(self, user_id: str, vendor_norm: str) -> Optional[str]:
        vn = (vendor_norm or "").strip().lower()
        if not vn:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT category FROM category_feedback
                WHERE user_id = ? AND lower(vendor_norm) = ?
                """,
                (user_id, vn),
            ).fetchone()
        if not row:
            return None
        return str(row[0])

    def upsert_feedback(self, user_id: str, vendor_norm: str, category: str) -> None:
        vn = (vendor_norm or "").strip()
        cat = (category or "").strip() or "Others"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO category_feedback (user_id, vendor_norm, category)
                VALUES (?, ?, ?)
                """,
                (user_id, vn, cat),
            )

    def list_goals(self, user_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, target_monthly_save, currency, active
                FROM financial_goals WHERE user_id = ? AND active = 1 ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_goal(self, user_id: str, title: Optional[str], target_monthly_save: float, currency: str = "INR") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO financial_goals (user_id, title, target_monthly_save, currency)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, title, float(target_monthly_save), currency),
            )
            return int(cur.lastrowid or 0)

    def append_chat(self, user_id: str, role: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chat_messages (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )

    def recent_chat(self, user_id: str, limit: int = 12) -> List[Dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content FROM chat_messages
                WHERE user_id = ? ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        msgs = [{"role": r["role"], "content": r["content"]} for r in reversed(list(rows))]
        return msgs


class LocalStorageAdapter:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, file_bytes: bytes, filename: str) -> str:
        name = f"{uuid.uuid4().hex[:8]}-{filename}"
        path = self.base_dir / name
        path.write_bytes(file_bytes)
        return str(path)


class AzureBlobStorageAdapter:
    def __init__(self, connection_string: str, container_name: str):
        if not connection_string or not container_name:
            raise ValueError("Azure config missing")

        try:
            from azure.storage.blob import BlobServiceClient
        except Exception as e:
            raise RuntimeError("Install azure-storage-blob") from e

        self.client = BlobServiceClient.from_connection_string(connection_string)
        self.container = self.client.get_container_client(container_name)

        try:
            self.container.create_container()
        except Exception as e:
            print("Azure container creation error:", e)

    def save(self, file_bytes: bytes, filename: str) -> str:
        blob_name = f"{uuid.uuid4().hex[:8]}-{filename}"
        blob = self.container.get_blob_client(blob_name)
        blob.upload_blob(file_bytes, overwrite=False)
        return blob.url
