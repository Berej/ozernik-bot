"""
База кармы: схемы таблиц, миграции старых баз и KarmaDatabase.

Общее (пользователи, мост, помощники миграций _rebuild_table/_table_columns/_table_sql)
живёт в utilities.Database.
"""

import sqlite3
from typing import Any, Literal

from utilities import Database, DataTypes, now_ms

# Аппендиксы, вычищенные по решению Alium (2026-09-29), — механик не будет:
# недельная Связь (weekly_bind_karma), Связь за ответы в чате (причина 'reply'),
# подарочная карма и Связь (gift_karma, gift_bind_karma, причина 'gift' —
# «подарками» Габ называл начисления от администрации, их делает /karma_add).
# Старые базы пересобираются миграцией Database._migrate_appendices.

KARMA_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS karma (
        user_id INTEGER PRIMARY KEY,
        status TEXT DEFAULT NULL,
        karma INTEGER NOT NULL DEFAULT 0,
        weekly_karma INTEGER NOT NULL DEFAULT 0,
        karma_updated_at INTEGER NOT NULL DEFAULT 0,

        CHECK (
            status IS NULL
            OR status IN ('asur', 'deva')
        ),

        FOREIGN KEY (user_id)
            REFERENCES users(id)
            ON DELETE CASCADE
    );
"""

KARMA_LOGS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS karma_logs (
        user_id INTEGER NOT NULL,
        added_at INTEGER NOT NULL,
        added_karma INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT 'n/a',

        CHECK (reason IN ('message', 'n/a')),

        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
"""

KARMIC_BIND_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS karmic_bind (
        user1_id INTEGER NOT NULL,
        user2_id INTEGER NOT NULL,
        bind_karma INTEGER NOT NULL DEFAULT 0,

        PRIMARY KEY (user1_id, user2_id),
        CHECK (user1_id < user2_id),

        FOREIGN KEY (user1_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (user2_id) REFERENCES users(id) ON DELETE CASCADE
    );
"""

KARMIC_BIND_LOGS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS karmic_bind_logs (
        user1_id INTEGER NOT NULL,
        user2_id INTEGER NOT NULL,
        added_at INTEGER NOT NULL,
        added_karma INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT 'n/a',

        CHECK (reason IN ('voice', 'n/a')),
        CHECK (user1_id < user2_id),

        FOREIGN KEY (user1_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (user2_id) REFERENCES users(id) ON DELETE CASCADE
    );
"""

# Тексты повышений уровней Сансары (редактируются в /settings_karma → «Текст повышений»).
# Раньше жили в modules/karma_sistem/levels.json: файл не в git, лежал в папке с кодом,
# писался фоновой задачей — и однажды пропал при обновлении бота (2026-09-29).
# В базе они переживают обновления вместе с кармой.
KARMA_LEVEL_TEXTS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS karma_level_texts (
        level INTEGER PRIMARY KEY,
        text TEXT NOT NULL DEFAULT '',

        CHECK (level BETWEEN 1 AND 100)
    );
"""


class KarmaDatabase(Database):
    """
    База кармы: общие таблицы (пользователи и т.д.) создаёт Database, свои — этот класс.
    Таблицы кармы создаются при первом создании KarmaDatabase (загрузка модуля кармы).
    """

    def _init_db(self) -> None:
        super()._init_db()

        with self.transaction():
            # Таблица кармы
            self.execute(KARMA_TABLE_SQL)

            # Миграция старых баз: время последнего изменения кармы
            # (нужно для порядка в топе при равной карме).
            karma_columns = {row['name'] for row in self.fetchall("PRAGMA table_info(karma)")}
            if 'karma_updated_at' not in karma_columns:
                self.execute("""
                    ALTER TABLE karma
                    ADD COLUMN karma_updated_at INTEGER NOT NULL DEFAULT 0
                """)

            # Логи кармы
            self.execute(KARMA_LOGS_TABLE_SQL)

            # Тексты повышений уровней
            self.execute(KARMA_LEVEL_TEXTS_TABLE_SQL)

            # Таблица кармической связи
            self.execute(KARMIC_BIND_TABLE_SQL)

            # Логи кармической связи
            self.execute(KARMIC_BIND_LOGS_TABLE_SQL)

            # Миграция старых баз: вычистить аппендиксы (см. комментарий у констант схемы).
            # Индексы создаются после — пересборка таблицы удаляет её индексы.
            self._migrate_appendices()

            # Индексы логов кармы для быстрого поиска
            self.execute("""
                CREATE INDEX IF NOT EXISTS idx_karma_logs_user_time
                ON karma_logs(user_id, added_at);
            """)

            # Индексы логов кармической связи для быстрого поиска
            self.execute("""
                CREATE INDEX IF NOT EXISTS idx_karmic_bind_logs_pair_time
                ON karmic_bind_logs(user1_id, user2_id, added_at);
            """)

    def _migrate_appendices(self) -> None:
        """Пересобирает таблицы старых баз без вычищенных колонок и причин логов. Данные сохраняются."""
        if 'gift_karma' in self._table_columns('karma'):
            self._rebuild_table(
                'karma',
                KARMA_TABLE_SQL,
                'user_id, status, karma, weekly_karma, karma_updated_at',
            )

        if {'weekly_bind_karma', 'gift_bind_karma'} & self._table_columns('karmic_bind'):
            self._rebuild_table(
                'karmic_bind',
                KARMIC_BIND_TABLE_SQL,
                'user1_id, user2_id, bind_karma',
            )

        if "'gift'" in self._table_sql('karma_logs'):
            self._rebuild_table(
                'karma_logs',
                KARMA_LOGS_TABLE_SQL,
                'user_id, added_at, added_karma, reason',
                where="WHERE reason IN ('message', 'n/a')",
            )

        logs_sql = self._table_sql('karmic_bind_logs')
        if "'reply'" in logs_sql or "'gift'" in logs_sql:
            self._rebuild_table(
                'karmic_bind_logs',
                KARMIC_BIND_LOGS_TABLE_SQL,
                'user1_id, user2_id, added_at, added_karma, reason',
                where="WHERE reason IN ('voice', 'n/a')",
            )

    # Функции работы с Кармой
    def _ensure_karma_row(self, user_id: int) -> None:
        """
        Создаёт строку кармы для пользователя, если её ещё нет.

        Используется перед операциями с таблицей karma, чтобы гарантировать,
        что у пользователя существует запись с обычной, подарочной
        и недельной кармой.

        :param user_id: Внутренний ID пользователя из таблицы users.
        :return: None.
        """
        with self.transaction():
            self.execute(
                """
                INSERT INTO karma (user_id)
                VALUES (?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id,),
            )

    def get_karma(self, user_id: int) -> DataTypes.Karma | None:
        """
        Возвращает данные кармы пользователя.

        :param user_id: Внутренний ID пользователя из таблицы users.
        :return: Объект ``Karma``, если запись найдена, иначе ``None``.
        """
        self._ensure_karma_row(user_id)

        row = self.fetchone("""
            SELECT *
            FROM karma
            WHERE user_id = ?
        """, (user_id,))

        if row is None:
            return None
        return DataTypes.Karma(row)

    def add_karma(self, user_id: int, karma: int, weekly: bool = False) -> tuple[DataTypes.Karma, DataTypes.Karma]:
        """
        Добавляет пользователю обычную карму. Если weekly=True, также увеличивает
        weekly_karma.

        :param user_id: Внутренний ID пользователя из таблицы users.
        :param karma: Количество добавляемой кармы.
        :param weekly: Учитывать ли изменение в недельной карме.
        :return: Кортеж из старого объекта и обновлённого объекта ``Karma``.
        :raises ValueError: Если karma меньше или равна нулю.
        """
        self._ensure_karma_row(user_id)

        with self.transaction():
            old_row = self.fetchone("""
                SELECT *
                FROM karma
                WHERE user_id = ?
            """, (user_id,))

            new_row = self.fetchone("""
                UPDATE karma
                SET karma = karma + ?,
                    weekly_karma = weekly_karma + ?,
                    karma_updated_at = ?
                WHERE user_id = ?
                RETURNING *
            """, (karma, karma if weekly else 0, now_ms(), user_id))

        return DataTypes.Karma(old_row), DataTypes.Karma(new_row)

    def remove_karma(self, user_id: int, karma: int, weekly: bool = False) -> DataTypes.Karma:
        """
        Уменьшает обычную карму пользователя. Ниже нуля карма не опускается.

        Если weekly=True, на то же количество уменьшается weekly_karma
        (тоже не ниже нуля).

        :param user_id: Внутренний ID пользователя из таблицы users.
        :param karma: Количество убираемой кармы.
        :param weekly: Уменьшать ли также недельную карму.
        :return: Обновлённый объект ``Karma``.
        :raises ValueError: Если karma меньше или равна нулю.
        """
        if karma <= 0:
            raise ValueError("karma должна быть больше нуля.")

        self._ensure_karma_row(user_id)

        with self.transaction():
            row = self.fetchone("""
                UPDATE karma
                SET karma = MAX(karma - ?, 0),
                    weekly_karma = MAX(weekly_karma - ?, 0),
                    karma_updated_at = ?
                WHERE user_id = ?
                RETURNING *
            """, (karma, karma if weekly else 0, now_ms(), user_id))

        return DataTypes.Karma(row)

    def set_karma(self, user_id: int, karma: int, weekly: bool = False) -> DataTypes.Karma:
        """
        Устанавливает точное значение обычной кармы пользователя.

        Если weekly=True, недельная карма меняется на ту же разницу
        (не ниже нуля); установка 0 обнуляет и её.

        :param user_id: Внутренний ID пользователя из таблицы users.
        :param karma: Новое значение обычной кармы.
        :param weekly: Менять ли также недельную карму.
        :return: Обновлённый объект ``Karma``.
        :raises ValueError: Если karma меньше нуля.
        """
        if karma < 0:
            raise ValueError("karma не может быть меньше нуля.")

        self._ensure_karma_row(user_id)

        with self.transaction():
            row = self.fetchone("""
                UPDATE karma
                SET karma_updated_at = CASE
                        WHEN karma != ? THEN ?
                        ELSE karma_updated_at
                    END,
                    weekly_karma = CASE
                        WHEN ? THEN MAX(weekly_karma + (? - karma), 0)
                        ELSE weekly_karma
                    END,
                    karma = ?
                WHERE user_id = ?
                RETURNING *
            """, (karma, now_ms(), weekly, karma, karma, user_id))

        return DataTypes.Karma(row)

    def set_karma_status(self, user_id: int, status: Literal['asur', 'deva'] | None) -> DataTypes.Karma:
        """
        Выдаёт или снимает особый статус Сансары (Асур, Дэва).

        Условия выдачи (например, достигнутый Человек) проверяет вызывающий код.

        :param user_id: Внутренний ID пользователя из таблицы users.
        :param status: 'asur', 'deva' или None, чтобы снять статус.
        :return: Обновлённый объект ``Karma``.
        :raises ValueError: Если статус недопустим.
        """
        if status not in ('asur', 'deva', None):
            raise ValueError('status должен быть "asur", "deva" или None.')

        self._ensure_karma_row(user_id)

        with self.transaction():
            row = self.fetchone("""
                UPDATE karma
                SET status = ?
                WHERE user_id = ?
                RETURNING *
            """, (status, user_id))

        return DataTypes.Karma(row)

    # Тексты повышений уровней

    def get_level_texts(self) -> dict[int, str]:
        """
        Все сохранённые тексты повышений.

        :return: Словарь уровень → текст. Уровней без текста в словаре нет.
        """
        rows = self.fetchall("""
            SELECT level, text
            FROM karma_level_texts
            ORDER BY level
        """)

        return {row['level']: row['text'] for row in rows}

    def get_level_text(self, level: int) -> str:
        """
        Текст повышения на уровень.

        :param level: Уровень Сансары.
        :return: Текст или пустая строка, если текст не задан.
        """
        row = self.fetchone("""
            SELECT text
            FROM karma_level_texts
            WHERE level = ?
        """, (level,))

        return row['text'] if row else ''

    def set_level_texts(self, texts: dict[int, str]) -> None:
        """
        Сохраняет тексты повышений одной транзакцией. Не переданные уровни не меняются.

        :param texts: Словарь уровень → текст.
        :raises ValueError: Если уровень не от 1 до 100 или текст не строка.
        """
        for level, text in texts.items():
            if not isinstance(level, int) or not 1 <= level <= 100:
                raise ValueError(f"Уровень должен быть числом от 1 до 100, а не {level!r}.")
            if not isinstance(text, str):
                raise ValueError(f"Текст уровня {level} должен быть строкой.")

        with self.transaction():
            for level, text in texts.items():
                self.execute("""
                    INSERT INTO karma_level_texts (level, text)
                    VALUES (?, ?)
                    ON CONFLICT(level) DO UPDATE SET text = excluded.text
                """, (level, text))

    def reset_weekly_karma(self) -> None:
        """
        Обнуляет недельную карму у всех пользователей.

        Сбрасывает weekly_karma в таблице karma.

        :return: None.
        """
        with self.transaction():
            self.execute("""
                UPDATE karma
                SET weekly_karma = 0
            """)

    def get_karma_rank(self, user_id: int) -> int | None:
        """
        Возвращает место пользователя в общем топе кармы.

        Место определяется по общей карме от наибольшей
        к наименьшей. При равной карме выше находится
        пользователь, который достиг её позже; если время
        не известно (старые записи) — с большим внутренним ID.
        Порядок совпадает с get_top_karma.

        :param user_id: Внутренний ID пользователя.
        :return: Место пользователя в топе, начиная с 1.
            Если пользователь отсутствует в таблице кармы,
            возвращает ``None``.
        """
        self._ensure_karma_row(user_id)

        row = self.fetchone("""
            SELECT rank
            FROM (
                SELECT karma.user_id,
                       ROW_NUMBER() OVER (
                           ORDER BY karma.karma DESC,
                                    karma.karma_updated_at DESC,
                                    karma.user_id DESC
                       ) AS rank
                FROM karma
            )
            WHERE user_id = ?
        """, (user_id,))

        if row is None:
            return None

        return row["rank"]

    def get_weekly_karma_rank(self, user_id: int) -> int | None:
        """
        Возвращает место пользователя в недельном топе кармы.

        Место определяется по недельной карме от наибольшей
        к наименьшей. При равной недельной карме выше находится
        пользователь с меньшей общей кармой. Если общая карма
        также равна, выше находится пользователь с меньшим ID.

        :param user_id: Внутренний ID пользователя.
        :return: Место пользователя в топе, начиная с 1.
            Если пользователь отсутствует в таблице кармы,
            возвращает ``None``.
        """
        self._ensure_karma_row(user_id)

        row = self.fetchone("""
            SELECT rank
            FROM (
                SELECT karma.user_id,
                       ROW_NUMBER() OVER (
                           ORDER BY karma.weekly_karma DESC,
                                    karma.karma ASC,
                                    karma.user_id ASC
                       ) AS rank
                FROM karma
            )
            WHERE user_id = ?
        """, (user_id,))

        if row is None:
            return None

        return row["rank"]

    def get_top_karma(self) -> list[tuple[DataTypes.Ozernik, DataTypes.Karma]]:
        """
        Возвращает пользователей из топа по общей карме.

        Пользователи расположены от наибольшей кармы
        к наименьшей. При равной карме выше находится
        пользователь, который достиг её позже; если время
        не известно (старые записи) — с большим внутренним ID.
        Порядок совпадает с get_karma_rank.

        :return: Список кортежей из объектов Ozernik и Karma
            в порядке занимаемых мест.
        """
        rows = self.fetchall("""
            SELECT users.*,
                   karma.user_id,
                   karma.status,
                   karma.karma,
                   karma.weekly_karma,
                   karma.karma_updated_at
            FROM users
            JOIN karma ON karma.user_id = users.id
            ORDER BY karma.karma DESC,
                     karma.karma_updated_at DESC,
                     users.id DESC
        """)

        return [(DataTypes.Ozernik(row), DataTypes.Karma(row)) for row in rows]

    def get_top_weekly_karma(self) -> list[tuple[DataTypes.Ozernik, DataTypes.Karma]]:
        """
        Возвращает топ пользователей по недельной карме.

        Пользователи расположены от наибольшей недельной кармы
        к наименьшей. При равной недельной карме выше находится
        пользователь с **меньшей** общей кармой.

        :return: Список кортежей из объектов Ozernik и Karma
            в порядке занимаемых мест.
        """
        rows = self.fetchall("""
            SELECT users.*,
                   karma.user_id,
                   karma.status,
                   karma.karma,
                   karma.weekly_karma,
                   karma.karma_updated_at
            FROM users
            JOIN karma ON karma.user_id = users.id
            ORDER BY karma.weekly_karma DESC,
                     karma.karma ASC,
                     users.id ASC
        """)

        return [(DataTypes.Ozernik(row), DataTypes.Karma(row)) for row in rows]

    # Функции логов кармы
    def _add_karma_log(self, user_id: int, added_karma: int, added_at_in_unix: int, reason: Literal['message', 'n/a']='n/a') -> int:
        """
        Добавляет запись в лог изменения кармы пользователя.

        :param user_id: Внутренний ID пользователя из таблицы users.
        :param added_karma: Количество добавленной или убранной кармы.
        :param added_at_in_unix: Время изменения в Unix timestamp.
        :param reason: Причина изменения кармы:
            message или n/a.
        :return: ID созданной записи в karma_logs.
        """
        if reason not in ("message", "n/a"):
            raise ValueError('reason должен быть равен "message" или "n/a".')

        with self.transaction():
            row = self.fetchone("""
                INSERT INTO karma_logs (
                    user_id,
                    added_karma,
                    added_at,
                    reason
                )
                VALUES (?, ?, ?, ?)
                RETURNING rowid AS id
            """, (user_id, added_karma, added_at_in_unix, reason))

        return row["id"]

    def get_karma_logs(self, start_time_in_unix: int, end_time_in_unix: int) -> list[dict[str, Any]]:
        """
        Возвращает логи изменения кармы за указанный период.

        Границы периода включаются: будут выбраны записи, у которых
        added_at >= start_time_in_unix и added_at <= end_time_in_unix.

        :param start_time_in_unix: Начало периода в Unix timestamp.
        :param end_time_in_unix: Конец периода в Unix timestamp.
        :return: Список логов кармы за выбранный период.
        :raises ValueError: Если начало периода позже его конца.
        """
        if start_time_in_unix > end_time_in_unix:
            raise ValueError("start_time_in_unix не может быть больше end_time_in_unix.")

        rows = self.fetchall("""
            SELECT rowid AS id, *
            FROM karma_logs
            WHERE added_at BETWEEN ? AND ?
            ORDER BY added_at ASC,
                     rowid ASC
        """,(start_time_in_unix, end_time_in_unix))

        return [dict(row) for row in rows]

    def get_user_karma_logs(self, user_id: int, start_time_in_unix: int, end_time_in_unix: int) -> list[dict[str, Any]]:
        """
        Возвращает логи изменения кармы пользователя за указанный период.

        Границы периода включаются.

        :param user_id: Внутренний ID пользователя из таблицы users.
        :param start_time_in_unix: Начало периода в Unix timestamp.
        :param end_time_in_unix: Конец периода в Unix timestamp.
        :return: Список логов кармы пользователя.
        :raises ValueError: Если начало периода позже его конца.
        """
        if start_time_in_unix > end_time_in_unix:
            raise ValueError("start_time_in_unix не может быть больше end_time_in_unix.")

        rows = self.fetchall("""
            SELECT rowid AS id, *
            FROM karma_logs
            WHERE user_id = ?
              AND added_at BETWEEN ? AND ?
            ORDER BY added_at ASC,
                     rowid ASC
        """, (user_id, start_time_in_unix, end_time_in_unix))

        return [dict(row) for row in rows]

    # Функции кармическиих уз
    @staticmethod
    def _normalize_pair(user1_id: int, user2_id: int) -> tuple[int, int]:
        """
        Возвращает пару пользователей в правильном порядке для таблицы karmic_binding.

        Потому-что таблице стоит ограничение CHECK (user1_id < user2_id), следовательно, ID всегда
        должны храниться в отсортированном виде.

        :param user1_id: ID первого пользователя.
        :param user2_id: ID второго пользователя.
        :return: Кортеж из двух ID в порядке от меньшего к большему.
        :raises ValueError: Если передан один и тот же пользователь.
        """
        if user1_id == user2_id:
            raise ValueError("Связь пользователя с им же самим запрещена.")

        return min(user1_id, user2_id), max(user1_id, user2_id)

    def _ensure_karmic_bind(self, user1_id: int, user2_id: int) -> tuple[int, int]:
        """
        Создаёт кармическую связь между двумя пользователями, если её ещё нет.

        :param user1_id: Внутренний ID первого пользователя.
        :param user2_id: Внутренний ID второго пользователя.
        :return: None.
        :raises ValueError: Если user1_id и user2_id указывают на одного пользователя.
        """
        pair = self._normalize_pair(user1_id, user2_id)

        with self.transaction():
            self.execute("""
                INSERT INTO karmic_bind (
                    user1_id, 
                    user2_id
                ) 
                VALUES (?, ?)
                ON CONFLICT(user1_id, user2_id) DO NOTHING
            """, pair)

    def get_karmic_bind(self, user1_id: int, user2_id: int) -> DataTypes.KarmicBind | None:
        """
        Возвращает кармическую связь между двумя пользователями.

        :param user1_id: Внутренний ID первого пользователя.
        :param user2_id: Внутренний ID второго пользователя.
        :return: Объект KarmicBind, если связь найдена, иначе None.
        :raises ValueError: Если user1_id и user2_id указывают на одного пользователя.
        """
        pair = self._normalize_pair(user1_id, user2_id)

        row = self.fetchone("""
            SELECT *
            FROM karmic_bind
            WHERE user1_id = ?
              AND user2_id = ?
        """, pair)

        if row is None:
            return None
        return DataTypes.KarmicBind(row)

    def add_bind_karma(self, user1_id, user2_id, karma: int) -> DataTypes.KarmicBind:
        """
        Добавляет кармическую связь двух пользователей.

        :param user1_id: Внутренний ID первого пользователя.
        :param user2_id: Внутренний ID второго пользователя.
        :param karma: Количество добавляемой кармы связи.
        :return: Обновлённый объект KarmicBind.
        :raises ValueError: Если karma меньше или равна нулю.
        """
        pair = self._normalize_pair(user1_id, user2_id)
        self._ensure_karmic_bind(*pair)

        with self.transaction():
            row = self.fetchone("""
                UPDATE karmic_bind
                SET bind_karma = bind_karma + ?
                WHERE user1_id = ?
                  AND user2_id = ?
                RETURNING *
            """, (karma, *pair))

        return DataTypes.KarmicBind(row)

    def add_bind_karma_many(self, pairs: list[tuple[int, int]], karma: int) -> list[DataTypes.KarmicBind]:
        """
        То же, что add_bind_karma, но для многих пар одной транзакцией.

        Запись на диск (коммит) — самая дорогая часть: ~15 мс. Голосовой цикл
        раньше делал по два коммита на каждую пару и останавливал бота на секунды.

        :param pairs: Пары внутренних ID пользователей.
        :param karma: Сколько кармы связи добавить каждой паре.
        :return: Обновлённые связи в том же порядке, что и pairs.
        :raises ValueError: Если в паре один и тот же пользователь.
        """
        normalized = [self._normalize_pair(user1_id, user2_id) for user1_id, user2_id in pairs]
        binds = []

        with self.transaction():
            for pair in normalized:
                self.execute("""
                    INSERT INTO karmic_bind (
                        user1_id,
                        user2_id
                    )
                    VALUES (?, ?)
                    ON CONFLICT(user1_id, user2_id) DO NOTHING
                """, pair)

                row = self.fetchone("""
                    UPDATE karmic_bind
                    SET bind_karma = bind_karma + ?
                    WHERE user1_id = ?
                      AND user2_id = ?
                    RETURNING *
                """, (karma, *pair))

                binds.append(DataTypes.KarmicBind(row))

        return binds

    def remove_bind_karma(self, user1_id, user2_id, karma: int) -> DataTypes.KarmicBind:
        """
        Уменьшает карму кармической связи двух пользователей.

        :param user1_id: Внутренний ID первого пользователя.
        :param user2_id: Внутренний ID второго пользователя.
        :param karma: Количество убираемой кармы связи.
        :return: Обновлённый объект KarmicBind.
        :raises ValueError: Если karma меньше или равна нулю.
        """
        if karma <= 0:
            raise ValueError("karma должна быть больше нуля.")

        pair = self._normalize_pair(user1_id, user2_id)
        self._ensure_karmic_bind(*pair)

        with self.transaction():
            row = self.fetchone("""
                UPDATE karmic_bind
                SET bind_karma = bind_karma - ?
                WHERE user1_id = ?
                  AND user2_id = ?
                RETURNING *
            """, (karma, *pair))

        return DataTypes.KarmicBind(row)

    def set_bind_karma(self, user1_id, user2_id, karma: int) -> DataTypes.KarmicBind:
        """
        Устанавливает точное значение обычной кармы связи.

        :param user1_id: Внутренний ID первого пользователя.
        :param user2_id: Внутренний ID второго пользователя.
        :param karma: Новое значение обычной кармы связи.
        :return: Обновлённый объект KarmicBind.
        :raises ValueError: Если karma меньше нуля.
        """
        if karma < 0:
            raise ValueError("karma не может быть меньше нуля.")

        pair = self._normalize_pair(user1_id, user2_id)
        self._ensure_karmic_bind(*pair)

        with self.transaction():
            row = self.fetchone("""
                UPDATE karmic_bind
                SET bind_karma = ?
                WHERE user1_id = ?
                  AND user2_id = ?
                RETURNING *
            """, (karma, *pair))

        return DataTypes.KarmicBind(row)

    def get_user_top_karmic_binds(self, user_id) -> list[tuple[DataTypes.Ozernik, DataTypes.KarmicBind]]:
        """
        Возвращает кармические связи конкретного пользователя.

        Для каждой связи возвращается второй её участник и объект связи.
        Результаты располагаются от наибольшей кармы связи к наименьшей.

        :param user_id: Внутренний ID пользователя.
        :return: Список кортежей из второго участника и кармической связи.
        """
        rows = self.fetchall("""
            SELECT users.*,
                   karmic_bind.user1_id,
                   karmic_bind.user2_id,
                   karmic_bind.bind_karma
            FROM karmic_bind
            JOIN users
              ON users.id = CASE
                  WHEN karmic_bind.user1_id = ?
                      THEN karmic_bind.user2_id
                  ELSE karmic_bind.user1_id
              END
            WHERE karmic_bind.user1_id = ?
               OR karmic_bind.user2_id = ?
            ORDER BY karmic_bind.bind_karma DESC,
                     users.id ASC
        """, (user_id, user_id, user_id))

        return [(DataTypes.Ozernik(row), DataTypes.KarmicBind(row)) for row in rows]

    def get_top_karmic_binds(self) -> list[tuple[DataTypes.Ozernik, DataTypes.Ozernik, DataTypes.KarmicBind]]:
        """
        Возвращает общий топ кармических связей.

        Пользователи расположены от наибольшей кармы связи
        к наименьшей. При равной недельной карме выше находится
        пользователь с **меньшей** общей кармой.

        :return: Список кортежей с двумя участниками и объекта связи.
        """
        rows = self.fetchall("""
            SELECT karmic_bind.*,

                   user1.id AS user1_user_id,
                   user1.alias AS user1_alias,
                   user1.discord_id AS user1_discord_id,
                   user1.discord_twink_id AS user1_discord_twink_id,
                   user1.telegram_id AS user1_telegram_id,
                   user1.telegram_bot_id AS user1_telegram_bot_id,

                   user2.id AS user2_user_id,
                   user2.alias AS user2_alias,
                   user2.discord_id AS user2_discord_id,
                   user2.discord_twink_id AS user2_discord_twink_id,
                   user2.telegram_id AS user2_telegram_id,
                   user2.telegram_bot_id AS user2_telegram_bot_id

            FROM karmic_bind
            JOIN users AS user1
              ON user1.id = karmic_bind.user1_id
            JOIN users AS user2
              ON user2.id = karmic_bind.user2_id

            ORDER BY karmic_bind.bind_karma DESC,
                     karmic_bind.user1_id ASC,
                     karmic_bind.user2_id ASC
        """)

        result = []

        for row in rows:
            first_user = DataTypes.Ozernik({
                "id": row["user1_user_id"],
                "alias": row["user1_alias"],
                "discord_id": row["user1_discord_id"],
                "discord_twink_id": row["user1_discord_twink_id"],
                "telegram_id": row["user1_telegram_id"],
                "telegram_bot_id": row["user1_telegram_bot_id"],
            })

            second_user = DataTypes.Ozernik({
                "id": row["user2_user_id"],
                "alias": row["user2_alias"],
                "discord_id": row["user2_discord_id"],
                "discord_twink_id": row["user2_discord_twink_id"],
                "telegram_id": row["user2_telegram_id"],
                "telegram_bot_id": row["user2_telegram_bot_id"],
            })

            karmic_bind = DataTypes.KarmicBind(row)

            result.append((
                first_user,
                second_user,
                karmic_bind,
            ))

        return result

    # Функции логов кармическиих уз
    def _add_karmic_bind_log(self, user1_id: int, user2_id: int, added_karma: int, added_at_in_unix: int, reason: Literal['voice', 'n/a']='n/a') -> int:
        """
        Добавляет запись в лог изменения кармической связи.

        :param user1_id: Внутренний ID первого пользователя.
        :param user2_id: Внутренний ID второго пользователя.
        :param added_karma: Количество добавленной или убранной кармы связи.
        :param added_at_in_unix: Время изменения в Unix timestamp.
        :param reason: Причина изменения связи: ``voice`` или ``n/a``.
        :return: ID созданной записи в karmic_bind_logs.
        :raises ValueError: Если параметр ``reason`` имеет недопустимое значение.
        """
        if reason not in ('voice', 'n/a'):
            raise ValueError('reason должен быть равен "voice" или "n/a".')

        pair = self._normalize_pair(user1_id, user2_id)

        with self.transaction():
            row = self.fetchone("""
                INSERT INTO karmic_bind_logs (
                    user1_id,
                    user2_id,
                    added_karma,
                    added_at,
                    reason
                )
                VALUES (?, ?, ?, ?, ?)
                RETURNING rowid AS id
            """, (*pair, added_karma, added_at_in_unix, reason))

        return row['id']

    def get_users_karmic_bind_logs(self, user1_id: int, user2_id: int, start_time_in_unix: int, end_time_in_unix: int) -> list[dict[str, Any]]:
        """
        Возвращает логи кармической связи двух пользователей за указанный период.

        :param user1_id: Внутренний ID первого пользователя.
        :param user2_id: Внутренний ID второго пользователя.
        :param start_time_in_unix: Начало периода в Unix timestamp.
        :param end_time_in_unix: Конец периода в Unix timestamp.
        :return: Список логов кармической связи за выбранный период.
        """
        if start_time_in_unix > end_time_in_unix:
            raise ValueError("start_time_in_unix не может быть больше end_time_in_unix.")

        pair = self._normalize_pair(user1_id, user2_id)

        rows = self.fetchall("""
            SELECT rowid AS id, *
            FROM karmic_bind_logs
            WHERE user1_id = ?
              AND user2_id = ?
              AND added_at >= ?
              AND added_at < ?
            ORDER BY added_at ASC,
                     rowid ASC
        """, (*pair, start_time_in_unix, end_time_in_unix))

        return [dict(row) for row in rows]

    def get_user_karmic_bind_logs(self, user_id: int, start_time_in_unix: int, end_time_in_unix: int) -> list[dict[str, Any]]:
        """
        Возвращает логи кармической связи пользователя со всеми другими пользователями за указанный период.

        :param user_id: Внутренний ID пользователя.
        :param start_time_in_unix: Начало периода в Unix timestamp.
        :param end_time_in_unix: Конец периода в Unix timestamp.
        :return: Список логов кармической связи за выбранный период.
        """
        if start_time_in_unix > end_time_in_unix:
            raise ValueError("start_time_in_unix не может быть больше end_time_in_unix.")

        rows = self.fetchall("""
            SELECT rowid AS id, *
            FROM karmic_bind_logs
            WHERE (user1_id = ? OR user2_id = ?)
              AND added_at >= ?
              AND added_at < ?
            ORDER BY added_at ASC,
                     rowid ASC
        """, (user_id, user_id, start_time_in_unix, end_time_in_unix))

        return [dict(row) for row in rows]

    def get_karmic_bind_logs(self, start_time_in_unix: int, end_time_in_unix: int) -> list[dict[str, Any]]:
        """
        Возвращает логи кармической всех пользователей за указанный период.

        :param start_time_in_unix: Начало периода в Unix timestamp.
        :param end_time_in_unix: Конец периода в Unix timestamp.
        :return: Список логов кармической связи за выбранный период.
        """
        if start_time_in_unix > end_time_in_unix:
            raise ValueError("start_time_in_unix не может быть больше end_time_in_unix.")

        rows = self.fetchall("""
            SELECT rowid AS id, *
            FROM karmic_bind_logs
            WHERE added_at >= ?
              AND added_at < ?
            ORDER BY added_at ASC,
                     rowid ASC
        """, (start_time_in_unix, end_time_in_unix))

        return [dict(row) for row in rows]
