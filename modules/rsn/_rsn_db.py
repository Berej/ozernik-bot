"""
RSN Database Layer
Standalone SQLite interface for RSN module (modules/rsn/rsn.db)
"""

import sqlite3
from pathlib import Path
from typing import Optional, List
import time

# Дело (РСН 2.0, решение Alium 2026-11-07): номер, чат, статус. У дела участники — у каждого
# своя запись в rsn_records (причина, баллы, срок), т.к. в одном деле могут нарушить разные правила.
CASES_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS rsn_cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
        channel_id INTEGER,                        -- чат дела (NULL — без чата)
        manual_chat INTEGER NOT NULL DEFAULT 0,    -- чат без мута: закрывается командой
        created_by INTEGER,
        created_at INTEGER NOT NULL,
        closed_at INTEGER,
        closed_by INTEGER
    )
"""

# Запись участника дела. kind: mute — мут, ban — бан, warn — без мута (предупреждение/снятие баллов).
RECORDS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id INTEGER REFERENCES rsn_cases(id),
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('mute', 'ban', 'warn')),
        reason TEXT NOT NULL,
        duration_hours REAL,
        points_delta INTEGER NOT NULL DEFAULT 0,
        moderator_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        points_before INTEGER,                     -- баллы до записи (для логов: «35 → 25»)
        released_at INTEGER,                       -- когда освобождён из мута/бана
        released_by INTEGER                        -- кто освободил досрочно (NULL — по сроку)
    )
"""


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

        # Дела и записи участников дел
        cursor.execute(CASES_TABLE_SQL)
        cursor.execute(RECORDS_TABLE_SQL.format(name="rsn_records"))

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

        self._migrate_records_to_cases()

    def _migrate_records_to_cases(self):
        """
        Старые базы (до РСН 2.0): записи без дел и без вида 'warn'. Каждая старая запись
        становится делом с тем же номером (номера, которые видели люди, не меняются);
        дело открыто, если по записи ещё действует наказание.

        Таблицу приходится пересоздать: SQLite не меняет CHECK у существующей таблицы.
        Порядок важен: новая таблица под временным именем → копия → удалить старую →
        переименовать новую. Вариант «переименовать старую» в современных SQLite переписал бы
        внешний ключ rsn_active_punishments на переименованную старую — и после её удаления
        ссылки повисли бы. Внешние ключи на время выключены (иначе DROP откажет: на старую
        ссылаются), включаются обратно с проверкой целостности.
        """
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(rsn_records)")}
        if "case_id" in columns:
            return

        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = OFF")  # вне транзакции — внутри не действует
        try:
            self.conn.execute("BEGIN")
            try:
                self.conn.execute(RECORDS_TABLE_SQL.format(name="rsn_records_new"))
                self.conn.execute("""
                    INSERT INTO rsn_records_new
                        (id, case_id, user_id, kind, reason, duration_hours, points_delta, moderator_id, created_at)
                    SELECT id, id, user_id, kind, reason, duration_hours, points_delta, moderator_id, created_at
                    FROM rsn_records
                """)
                self.conn.execute("""
                    INSERT INTO rsn_cases (id, status, created_by, created_at, closed_at)
                    SELECT id, 'closed', moderator_id, created_at, created_at FROM rsn_records
                """)
                self.conn.execute("""
                    UPDATE rsn_cases SET status = 'open', closed_at = NULL
                    WHERE id IN (SELECT record_id FROM rsn_active_punishments)
                """)
                self.conn.execute("DROP TABLE rsn_records")
                self.conn.execute("ALTER TABLE rsn_records_new RENAME TO rsn_records")
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

        broken = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise RuntimeError(f"РСН: после переноса в дела нарушены ссылки: {broken}")

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

    def get_all_scores(self) -> List[dict]:
        """Очки всех, у кого есть запись (для обхода меток уровня 1–10)."""
        return [dict(row) for row in self.conn.execute("SELECT * FROM rsn_score")]

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
        moderator_id: int,
        case_id: Optional[int] = None,
        points_before: Optional[int] = None,
    ) -> int:
        """
        Создать запись участника дела. Вернуть id записи.
        Без case_id создаётся новое дело (закрытое — для записей без чата и наказания
        его закрывает вызывающий код; см. create_case).
        """
        cursor = self.conn.cursor()
        created_at = int(time.time())
        if case_id is None:
            case_id = self.create_case(created_by=moderator_id)
        cursor.execute("""
            INSERT INTO rsn_records
            (case_id, user_id, kind, reason, duration_hours, points_delta, moderator_id, created_at, points_before)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (case_id, user_id, kind, reason, duration_hours, points_delta, moderator_id, created_at, points_before))
        self.conn.commit()
        return cursor.lastrowid

    # ===== Дела =====

    def create_case(self, created_by: Optional[int] = None, manual_chat: bool = False) -> int:
        """Открыть дело. Вернуть его номер."""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO rsn_cases (status, manual_chat, created_by, created_at) VALUES ('open', ?, ?, ?)",
            (int(manual_chat), created_by, int(time.time()))
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_case(self, case_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM rsn_cases WHERE id = ?", (case_id,)).fetchone()
        return dict(row) if row else None

    def set_case_channel(self, case_id: int, channel_id: Optional[int]):
        self.conn.execute("UPDATE rsn_cases SET channel_id = ? WHERE id = ?", (channel_id, case_id))
        self.conn.commit()

    def close_case(self, case_id: int, closed_by: Optional[int] = None):
        self.conn.execute(
            "UPDATE rsn_cases SET status = 'closed', closed_at = ?, closed_by = ? WHERE id = ?",
            (int(time.time()), closed_by, case_id)
        )
        self.conn.commit()

    def get_case_records(self, case_id: int) -> List[dict]:
        """Записи участников дела, в порядке добавления."""
        rows = self.conn.execute(
            "SELECT * FROM rsn_records WHERE case_id = ? ORDER BY id", (case_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_case_active_users(self, case_id: int, kinds: tuple = ("mute", "ban")) -> List[int]:
        """Участники дела, чьё наказание по этому делу ещё действует (по умолчанию — мут или бан)."""
        marks = ",".join("?" * len(kinds))
        rows = self.conn.execute(f"""
            SELECT DISTINCT p.user_id
            FROM rsn_active_punishments p
            JOIN rsn_records r ON r.id = p.record_id
            WHERE r.case_id = ? AND p.kind IN ({marks})
        """, (case_id, *kinds)).fetchall()
        return [row["user_id"] for row in rows]

    def get_open_case_of_channel(self, channel_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM rsn_cases WHERE channel_id = ? AND status = 'open'", (channel_id,)
        ).fetchone()
        return dict(row) if row else None

    def release_record(self, record_id: int, released_by: Optional[int] = None):
        """Отметить освобождение участника (released_by — кто досрочно; None — по сроку)."""
        self.conn.execute(
            "UPDATE rsn_records SET released_at = ?, released_by = ? WHERE id = ? AND released_at IS NULL",
            (int(time.time()), released_by, record_id)
        )
        self.conn.commit()

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

    # ===== Твинки =====

    def get_all_user_ids(self) -> List[int]:
        """Все ID, на которые в базе что-то записано (баллы, дела, наказания)."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT user_id FROM rsn_score
            UNION SELECT user_id FROM rsn_records
            UNION SELECT user_id FROM rsn_active_punishments
        """)
        return [row["user_id"] for row in cursor.fetchall()]

    def get_active_punishments(self, kind: str) -> List[dict]:
        """Все действующие наказания данного типа ('mute' / 'ban')."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM rsn_active_punishments WHERE kind = ?", (kind,))
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _strictness(punishment: dict) -> tuple:
        """Чем больше, тем строже: бан строже мута, бессрочное строже срочного, позднее строже раннего."""
        return (
            punishment["kind"] == "ban",
            punishment["expires_at"] is None,
            punishment["expires_at"] or 0,
        )

    def merge_user(self, from_id: int, to_id: int) -> bool:
        """
        Перенести личное дело твинка (from_id) на основной аккаунт (to_id) — одной транзакцией.

        - баллы — меньшие из двух (худшая репутация), дело активно, если было активно у любого;
        - все записи о нарушениях переходят к основному;
        - действующее наказание — более строгое из двух.

        :return: True, если было что переносить.
        """
        source = self.get_score(from_id)
        source_punishment = self.get_active_punishment(from_id)
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM rsn_records WHERE user_id = ? LIMIT 1", (from_id,))
        has_records = cursor.fetchone() is not None

        if source is None and source_punishment is None and not has_records:
            return False

        target = self.get_score(to_id)
        target_punishment = self.get_active_punishment(to_id)

        # Внутри — только execute: методы выше с собственным commit() закоммитили бы посередине.
        with self.conn:
            if source is not None:
                if target is None:
                    self.conn.execute(
                        "INSERT INTO rsn_score (user_id, points, active) VALUES (?, ?, ?)",
                        (to_id, source["points"], source["active"])
                    )
                else:
                    self.conn.execute(
                        "UPDATE rsn_score SET points = ?, active = ? WHERE user_id = ?",
                        (min(source["points"], target["points"]), max(source["active"], target["active"]), to_id)
                    )
                self.conn.execute("DELETE FROM rsn_score WHERE user_id = ?", (from_id,))

            self.conn.execute("UPDATE rsn_records SET user_id = ? WHERE user_id = ?", (to_id, from_id))

            if source_punishment is not None:
                if target_punishment is None or self._strictness(source_punishment) > self._strictness(target_punishment):
                    self.conn.execute(
                        "INSERT OR REPLACE INTO rsn_active_punishments (user_id, kind, expires_at, record_id) "
                        "VALUES (?, ?, ?, ?)",
                        (to_id, source_punishment["kind"], source_punishment["expires_at"], source_punishment["record_id"])
                    )
                self.conn.execute("DELETE FROM rsn_active_punishments WHERE user_id = ?", (from_id,))

        return True
