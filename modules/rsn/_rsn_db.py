"""
RSN Database Layer
Standalone SQLite interface for RSN module (modules/rsn/rsn.db)
"""

import sqlite3
from pathlib import Path
from typing import Optional, List
import time


class RsnDatabase:
    """
    Собственный слой SQLite для модуля RSN.
    БД: modules/rsn/rsn.db
    """

    def __init__(self, db_path: str = "modules/rsn/rsn.db"):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._initialize()

    def _initialize(self):
        """Инициализация БД и создание таблиц."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._create_tables()

    def _create_tables(self):
        """Создание таблиц при первом запуске."""
        cursor = self.conn.cursor()

        # Таблица рейтингов: баллы честности и статус активности
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rsn_score (
                user_id INTEGER PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 50,
                active INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Таблица записей о нарушениях (мут, бан)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rsn_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('mute', 'ban')),
                reason TEXT NOT NULL,
                duration_hours REAL,
                points_delta INTEGER NOT NULL DEFAULT 0,
                moderator_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)

        # Таблица активных наказаний
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rsn_active_punishments (
                user_id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('mute', 'ban')),
                expires_at INTEGER,
                record_id INTEGER NOT NULL REFERENCES rsn_records(id)
            )
        """)

        self.conn.commit()

    def close(self):
        """Закрытие БД."""
        if self.conn:
            self.conn.close()

    def get_score(self, user_id: int) -> Optional[dict]:
        """Получить запись о пользователе."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM rsn_score WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def ensure_score(self, user_id: int) -> dict:
        """Гарантировать, что пользователь есть в БД. Вернуть его скор."""
        existing = self.get_score(user_id)
        if existing:
            return existing

        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO rsn_score (user_id, points, active) VALUES (?, ?, ?)",
            (user_id, 50, 0)
        )
        self.conn.commit()
        return self.get_score(user_id)

    def set_points(self, user_id: int, points: int):
        """Установить баллы (с защитой диапазона [0; 50])."""
        self.ensure_score(user_id)
        
        # Защита от отрицательных и превышающих максимум значений
        if points < 0:
            points = 0
        elif points > 50:
            points = 50
        
        cursor = self.conn.cursor()
        cursor.execute("UPDATE rsn_score SET points = ? WHERE user_id = ?", (points, user_id))
        self.conn.commit()

    def add_points(self, user_id: int, amount: int):
        """Добавить баллы."""
        score = self.ensure_score(user_id)
        new_points = score['points'] + amount
        self.set_points(user_id, new_points)

    def set_active(self, user_id: int, active: int = 1):
        """Установить флаг активности (0 или 1)."""
        self.ensure_score(user_id)
        cursor = self.conn.cursor()
        cursor.execute("UPDATE rsn_score SET active = ? WHERE user_id = ?", (active, user_id))
        self.conn.commit()

    def get_all_active_users(self) -> List[dict]:
        """Получить всех пользователей с active=1."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM rsn_score WHERE active = 1")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def create_record(
        self,
        user_id: int,
        kind: str,
        reason: str,
        duration_hours: Optional[float],
        points_delta: int,
        moderator_id: int
    ) -> int:
        """Создать запись о нарушении. Вернуть id записи."""
        cursor = self.conn.cursor()
        created_at = int(time.time())
        cursor.execute("""
            INSERT INTO rsn_records
            (user_id, kind, reason, duration_hours, points_delta, moderator_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, kind, reason, duration_hours, points_delta, moderator_id, created_at))
        self.conn.commit()
        return cursor.lastrowid

    def get_record(self, record_id: int) -> Optional[dict]:
        """Получить запись по ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM rsn_records WHERE id = ?", (record_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def edit_record(
        self,
        record_id: int,
        reason: Optional[str] = None,
        points_delta: Optional[int] = None,
        duration_hours: Optional[float] = None
    ):
        """Отредактировать запись."""
        record = self.get_record(record_id)
        if not record:
            return

        cursor = self.conn.cursor()
        if reason is not None:
            cursor.execute("UPDATE rsn_records SET reason = ? WHERE id = ?", (reason, record_id))
        if points_delta is not None:
            cursor.execute(
                "UPDATE rsn_records SET points_delta = ? WHERE id = ?", (points_delta, record_id)
            )
        if duration_hours is not None:
            cursor.execute(
                "UPDATE rsn_records SET duration_hours = ? WHERE id = ?",
                (duration_hours, record_id)
            )
        self.conn.commit()

    def delete_record(self, record_id: int):
        """Удалить запись."""
        cursor = self.conn.cursor()
        # Сначала удалить активное наказание если оно связано с этой записью
        cursor.execute(
            "DELETE FROM rsn_active_punishments WHERE record_id = ?",
            (record_id,)
        )
        # Удалить саму запись
        cursor.execute("DELETE FROM rsn_records WHERE id = ?", (record_id,))
        self.conn.commit()

    def get_user_records(self, user_id: int) -> List[dict]:
        """Получить все записи пользователя."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM rsn_records WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_records_for_month(self, year: int, month: int) -> List[dict]:
        """Получить записи за конкретный месяц (unix timestamps)."""
        import calendar
        # Начало месяца
        start_ts = int(time.mktime((year, month, 1, 0, 0, 0, 0, 0, 0)))
        # Конец месяца
        _, last_day = calendar.monthrange(year, month)
        end_ts = int(
            time.mktime((year, month, last_day, 23, 59, 59, 0, 0, 0))
        )

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM rsn_records WHERE created_at BETWEEN ? AND ? ORDER BY created_at ASC",
            (start_ts, end_ts)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_all_records(self) -> List[dict]:
        """Получить все записи."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM rsn_records ORDER BY created_at ASC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_active_punishment(self, user_id: int) -> Optional[dict]:
        """Получить активное наказание пользователя."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM rsn_active_punishments WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_or_update_punishment(
        self,
        user_id: int,
        kind: str,
        expires_at: Optional[int],
        record_id: int
    ):
        """Создать или обновить активное наказание."""
        cursor = self.conn.cursor()
        existing = self.get_active_punishment(user_id)
        if existing:
            cursor.execute(
                "UPDATE rsn_active_punishments SET kind = ?, expires_at = ?, record_id = ? WHERE user_id = ?",
                (kind, expires_at, record_id, user_id)
            )
        else:
            cursor.execute(
                "INSERT INTO rsn_active_punishments (user_id, kind, expires_at, record_id) VALUES (?, ?, ?, ?)",
                (user_id, kind, expires_at, record_id)
            )
        self.conn.commit()

    def delete_active_punishment(self, user_id: int):
        """Удалить активное наказание."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM rsn_active_punishments WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def get_expired_punishments(self) -> List[dict]:
        """Получить наказания, срок которых истёк."""
        cursor = self.conn.cursor()
        now = int(time.time())
        cursor.execute(
            "SELECT * FROM rsn_active_punishments WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
