import json
import os
from threading import Lock
from pathlib import Path
from typing import Any
import sqlite3
from typing import Literal
from PIL import Image

class JsonWorker:
    def __init__(self, path: str):
        json_path = Path(path)
        super().__setattr__("json_path", json_path)
        super().__setattr__("_data", self._open_data())

    def _open_data(self) -> dict[str, Any]:
        os.makedirs(os.path.dirname(self.json_path) or ".", exist_ok=True)

        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=4)
            return {}

    def _commit_data(self) -> None:
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=4)

class DataWorker(JsonWorker):
    def __init__(self, path: str, setup: dict | None = None):
        super().__init__(path)
        if setup:
            for key, value in setup.items():
                if key not in self._data.keys():
                    setattr(self, key, value)

    def __getattr__(self, name: str) -> Any:
        return self._data.get(name)

    def __setattr__(self, name: str, value: Any):
        if name in {"json_path", "_data"}:
            super().__setattr__(name, value)
            return

        self._data[name] = value
        self._commit_data()

class BridgeStorage:
    """
    Storage manager for the bridge between Discord and Telegram.
    Handles JSON queues and linked message mappings.
    """

    def __init__(self,
                 link_path="modules/Bridge/LinkMess.json",
                 req_path="modules/Bridge/queues/Requests.json",
                 dtt_path="modules/Bridge/queues/DisToTel.json",
                 ttd_path="modules/Bridge/queues/TelToDis.json"):

        self.req_path = req_path
        self.link_path = link_path
        self.dtt_path = dtt_path
        self.ttd_path = ttd_path

        # Locks to ensure thread-safe access to each JSON file
        self._req_lock = Lock()
        self._link_lock = Lock()
        self._dtt_lock = Lock()
        self._ttd_lock = Lock()

    # -------------------------
    # Internal JSON operations
    # -------------------------

    @staticmethod
    def _load_json(path: str) -> list:
        """
        Safely load JSON data from a file as a list.
        - If file does not exist, creates it as an empty list.
        - If JSON is invalid or any error occurs, returns an empty list.
        """
        # Create a directory if it doesn't exist
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # If the file does not exist, we create an empty JSON
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
            return []

        # Trying to load JSON
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return []

    @staticmethod
    def _save_json(path: str, data: list):
        """Save list to JSON file. Directories are created automatically."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    # -------------------------
    # Request queue operations
    # -------------------------

    def load_req(self) -> list:
        with self._req_lock:
            return self._load_json(self.req_path)

    def save_req(self, data: list):
        with self._req_lock:
            self._save_json(self.req_path, data)

    def add_req(self, element):
        data = self.load_req()
        data.append(element)
        self.save_req(data)

    def pop_req(self, mode):
        """Pop first request where request['way'] == mode."""
        data = self.load_req()
        found = None

        for item in data:
            if item.get("way") == mode:
                found = item
                data.remove(item)
                break

        self.save_req(data)
        return found

    # -------------------------
    # Linked message table
    # -------------------------

    def load_link(self) -> list:
        with self._link_lock:
            return self._load_json(self.link_path)

    def save_link(self, data: list):
        with self._link_lock:
            self._save_json(self.link_path, data)

    def add_link(self, element):
        """Store message link pair (Discord ↔ Telegram). Auto-truncate to 5000 entries."""
        data = self.load_link()
        if len(data) >= 5000:
            data.pop(0)
        data.append(element)
        self.save_link(data)

    def clear_link(self):
        self.save_link([])

    # -------------------------
    # Discord → Telegram queue
    # -------------------------

    def load_dtt(self) -> list:
        with self._dtt_lock:
            return self._load_json(self.dtt_path)

    def save_dtt(self, data: list):
        with self._dtt_lock:
            self._save_json(self.dtt_path, data)

    def add_dtt(self, element):
        data = self.load_dtt()
        data.append(element)
        self.save_dtt(data)

    def pop_dtt(self):
        data = self.load_dtt()
        if not data:
            return None
        element = data.pop(0)
        self.save_dtt(data)
        return element

    # -------------------------
    # Telegram → Discord queue
    # -------------------------

    def load_ttd(self) -> list:
        with self._ttd_lock:
            return self._load_json(self.ttd_path)

    def save_ttd(self, data: list):
        with self._ttd_lock:
            self._save_json(self.ttd_path, data)

    def add_ttd(self, element):
        data = self.load_ttd()
        data.append(element)
        self.save_ttd(data)

    def pop_ttd(self):
        data = self.load_ttd()
        if not data:
            return None
        element = data.pop(0)
        self.save_ttd(data)
        return element

storage = BridgeStorage()

class DataTypes:
    class OzernikDataType:
        pass

    class Ozernik(OzernikDataType):
        def __init__(self, user_row: sqlite3.Row):
            self.id = user_row['id']
            self.alias = user_row['alias'] # Прозвище (хз буду ли использовать)
            self.discord_id = user_row['discord_id']
            self.discord_twink_id = user_row['discord_twink_id']
            self.telegram_id = user_row['telegram_id']
            self.telegram_bot_id = user_row['telegram_bot_id']

            # self.karmic_binds = []
            # for karmic_bind_row in karmic_bind_rows:
            #     if karmic_bind_row['user1_id'] == self.id:
            #         bound_id = karmic_bind_row['user1_id']
            #     elif karmic_bind_row['user2_id'] == self.id:
            #         bound_id = karmic_bind_row['user2_id']
            #     else:
            #         continue
            #
            #     self.karmic_binds.append({
            #         'bound_id': bound_id,
            #         'bind_karma': bind_karma,
            #         'gift_bind_karma': gift_bind_karma,
            #         'weekly_bind_karma': weekly_bind_karma,
            #         'weekly_gift_bind_karma': weekly_gift_bind_karma,
            #     })

    class Karma(OzernikDataType):
        def __init__(self, row: sqlite3.Row):
            self.user_id = row['user_id']
            self.karma = row['karma']
            self.status = row['status']
            self.gift_karma = row['gift_karma']
            self.weekly_karma = row['weekly_karma']

    class KarmicBind(OzernikDataType):
        def __init__(self, row: sqlite3.Row):
            self.ids = (row['user1_id'], row['user2_id'])
            self.bind_karma = row['bind_karma']
            self.gift_bind_karma = row['gift_bind_karma']
            self.weekly_bind_karma = row['weekly_bind_karma']

    class ChatLink(OzernikDataType):
        def __init__(self, row: sqlite3.Row):
            self.id = row['id']
            self.discord_channel_id = row['discord_channel_id']
            self.telegram_chat_id = row['telegram_chat_id']
            self.telegram_topic_id = row['telegram_topic_id']

    class MessageLink(OzernikDataType):
        def __init__(self, row: sqlite3.Row, chat_link: "DataTypes.ChatLink"):
            self.id = row['id']
            self.user_id = row['user_id']
            self.chat_link = chat_link
            self.discord_message_id = row['discord_message_id']
            self.telegram_message_id = row['telegram_message_id']
            self.text = row['text']
            self.repeater_id = row['repeater_id']

class OzernikNotfound(Exception):
    """Озёрник не найден в Discord"""
    pass

class Database:
    def __init__(self) -> None:
        base_dir = Path(__file__).resolve().parent
        db_path = base_dir / 'database.db'

        self._con = sqlite3.connect(db_path)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")

        self._init_db()

    # Базовые функции.
    def _init_db(self) -> None:
        with self.transaction():
            # Таблица пользователей
            self.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alias TEXT,
                    discord_id INTEGER NOT NULL UNIQUE,
                    discord_twink_id INTEGER UNIQUE,
                    telegram_id INTEGER UNIQUE,
                    telegram_bot_id INTEGER UNIQUE
                );
            """)

            # Таблица кармы
            self.execute("""
                CREATE TABLE IF NOT EXISTS karma (
                    user_id INTEGER PRIMARY KEY,
                    status TEXT DEFAULT NULL,
                    karma INTEGER NOT NULL DEFAULT 0,
                    gift_karma INTEGER NOT NULL DEFAULT 0,
                    weekly_karma INTEGER NOT NULL DEFAULT 0,
                
                    CHECK (
                        status IS NULL
                        OR status IN ('asur', 'deva')
                    ),
                
                    FOREIGN KEY (user_id)
                        REFERENCES users(id)
                        ON DELETE CASCADE
                );
            """)

            # Логи кармы
            self.execute("""
                CREATE TABLE IF NOT EXISTS karma_logs (
                    user_id INTEGER NOT NULL,
                    added_at INTEGER NOT NULL,
                    added_karma INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT 'n/a',
                    
                    CHECK (reason IN ('message', 'gift', 'n/a')),
    
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
            """)

            # Индексы логов кармы для быстрого поиска
            self.execute("""
                CREATE INDEX IF NOT EXISTS idx_karma_logs_user_time
                ON karma_logs(user_id, added_at);
            """)

            # Таблица кармической связи
            self.execute("""
                CREATE TABLE IF NOT EXISTS karmic_bind (
                    user1_id INTEGER NOT NULL,
                    user2_id INTEGER NOT NULL,
                    bind_karma INTEGER NOT NULL DEFAULT 0,
                    gift_bind_karma INTEGER NOT NULL DEFAULT 0,
                    weekly_bind_karma INTEGER NOT NULL DEFAULT 0,
    
                    PRIMARY KEY (user1_id, user2_id),
                    CHECK (user1_id < user2_id),
    
                    FOREIGN KEY (user1_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (user2_id) REFERENCES users(id) ON DELETE CASCADE
                );
            """)

            # Логи кармической связи
            self.execute("""
                CREATE TABLE IF NOT EXISTS karmic_bind_logs (
                    user1_id INTEGER NOT NULL,
                    user2_id INTEGER NOT NULL,
                    added_at INTEGER NOT NULL,
                    added_karma INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT 'n/a',
                    
                    CHECK (reason IN ('voice', 'reply', 'gift', 'n/a')),
                    CHECK (user1_id < user2_id),
                    
                    FOREIGN KEY (user1_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (user2_id) REFERENCES users(id) ON DELETE CASCADE
                );
            """)

            # Индексы логов кармической связи для быстрого поиска
            self.execute("""
                CREATE INDEX IF NOT EXISTS idx_karmic_bind_logs_pair_time
                ON karmic_bind_logs(user1_id, user2_id, added_at);
            """)

            # Таблица мостов
            self.execute("""
                CREATE TABLE IF NOT EXISTS chat_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    discord_channel_id INTEGER NOT NULL UNIQUE,
                    telegram_chat_id INTEGER NOT NULL,
                    telegram_topic_id INTEGER NOT NULL,
                    UNIQUE (telegram_chat_id, telegram_topic_id)
                )
            """)

            # Таблица сообщений моста
            self.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    link_chat_id INTEGER NOT NULL,
                    discord_message_id INTEGER NOT NULL,
                    telegram_message_id INTEGER NOT NULL,
                    text TEXT,
                    repeater_id int,

                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (link_chat_id) REFERENCES chat_links(id)
                );
            """)

    def close_(self) -> None:
        self._con.close()

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self._con.execute(query, params)

    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return self.execute(query, params).fetchone()

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return self.execute(query, params).fetchall()

    def transaction(self):
        return self._con

    # Функции управления пользователями в БД.
    def add_user(
            self,
            discord_id: int,
            alias: str | None = None,
            telegram_id: int | None = None,
            discord_twink_id: int | None = None,
            telegram_bot_id: int | None = None,
    ) -> int:
        """
        Добавляет пользователя в таблицу.

        Создаёт новую запись пользователя с основным Discord ID и,
        при наличии, дополнительными привязками к Telegram, Discord-твинку
        и Telegram-боту.

        :param discord_id: Discord ID основного аккаунта пользователя.
        :param alias: Прозвище пользователя в именительном падеже.
        :param telegram_id: Telegram ID пользователя, если он уже известен.
        :param discord_twink_id: Discord ID дополнительного аккаунта пользователя.
        :param telegram_bot_id: Telegram ID пользователя для отдельной привязки.
        :return: Внутренний ID созданного пользователя.
        :raises sqlite3.IntegrityError: Если один из переданных ID уже привязан.
        """
        if discord_twink_id == discord_id:
            raise sqlite3.IntegrityError("Основной Discord ID и Discord ID твинка не могут совпадать.")

        with self.transaction():
            existing_row = self.fetchone("""
                SELECT id
                FROM users
                WHERE discord_id IN (?, ?)
                   OR discord_twink_id IN (?, ?)
                LIMIT 1
            """,
                (
                    discord_id,
                    discord_twink_id,
                    discord_id,
                    discord_twink_id,
                ),
            )

            if existing_row is not None:
                raise sqlite3.IntegrityError("Один из переданных Discord ID уже привязан к пользователю.")

            row = self.fetchone("""
                INSERT INTO users (
                    discord_id,
                    alias,
                    telegram_id,
                    discord_twink_id,
                    telegram_bot_id
                )
                VALUES (?, ?, ?, ?, ?)
                RETURNING id
            """,
                (
                    discord_id,
                    alias,
                    telegram_id,
                    discord_twink_id,
                    telegram_bot_id,
                ),
            )

        return row["id"]

    def get_user(self, user_id: int) -> DataTypes.Ozernik | None:
        """
        Возвращает пользователя по внутреннему ID из таблицы users.

        :param user_id: Внутренний ID пользователя в базе данных.
        :return: Объект Ozernik, если пользователь найден, иначе None.
        """
        row = self.fetchone("""
            SELECT * FROM users WHERE id = ?
        """, (user_id,))
        if row is None:
            return None
        return DataTypes.Ozernik(row)

    def get_user_by_discord_id(self, discord_id: int, ) -> DataTypes.Ozernik | None:
        """
        Возвращает пользователя по основному или дополнительному Discord ID.

        :param discord_id: Discord ID основного аккаунта или твинка пользователя.
        :return: Объект Ozernik, если пользователь найден, иначе ``None``.
        """
        row = self.fetchone("""
            SELECT *
            FROM users
            WHERE discord_id = ?
               OR discord_twink_id = ?
        """,(discord_id, discord_id))

        if row is None:
            return None

        return DataTypes.Ozernik(row)

    def get_user_by_telegram_id(self, telegram_id: int) -> DataTypes.Ozernik | None:
        """
        Возвращает пользователя по Telegram ID.

        :param telegram_id: Telegram ID пользователя.
        :return: Объект Ozernik, если пользователь найден, иначе ``None``.
        """
        row = self.fetchone("""
            SELECT *
            FROM users
            WHERE telegram_id = ?
        """,(telegram_id,))

        if row is None:
            return None

        return DataTypes.Ozernik(row)

    def update_user(
            self,
            user_id: int,
            discord_id: int | None = None,
            telegram_id: int | None = None,
            discord_twink_id: int | None = None,
            telegram_bot_id: int | None = None,
    ) -> DataTypes.Ozernik:
        """
        Обновляет данные пользователя в таблице users.

        Изменяет только те поля, для которых были переданы новые значения.
        Поля со значением None остаются без изменений.

        :param user_id: Внутренний ID пользователя в базе данных.
        :param discord_id: Новый основной Discord ID пользователя.
        :param telegram_id: Новый Telegram ID пользователя.
        :param discord_twink_id: Новый Discord ID дополнительного аккаунта.
        :param telegram_bot_id: Новый Telegram ID пользователя для бота.
        :return: Обновлённый объект Ozernik.
        :raises ValueError: Если пользователь с указанным user_id не найден.
        :raises sqlite3.IntegrityError: Если новый ID уже привязан
            к другому пользователю.
        """
        updates = []
        values = []

        if discord_id is not None:
            updates.append("discord_id = ?")
            values.append(discord_id)

        if telegram_id is not None:
            updates.append("telegram_id = ?")
            values.append(telegram_id)

        if discord_twink_id is not None:
            updates.append("discord_twink_id = ?")
            values.append(discord_twink_id)

        if telegram_bot_id is not None:
            updates.append("telegram_bot_id = ?")
            values.append(telegram_bot_id)

        with self.transaction():
            current_row = self.fetchone("""
                SELECT *
                FROM users
                WHERE id = ?
            """,(user_id,))

            if current_row is None:
                raise ValueError(f"Пользователь с внутренним ID {user_id} не найден.")

            if not updates:
                return DataTypes.Ozernik(current_row)

            new_discord_id = (
                discord_id
                if discord_id is not None
                else current_row["discord_id"]
            )
            new_discord_twink_id = (
                discord_twink_id
                if discord_twink_id is not None
                else current_row["discord_twink_id"]
            )
            new_telegram_id = (
                telegram_id
                if telegram_id is not None
                else current_row["telegram_id"]
            )
            new_telegram_bot_id = (
                telegram_bot_id
                if telegram_bot_id is not None
                else current_row["telegram_bot_id"]
            )

            if new_discord_twink_id is not None and new_discord_id == new_discord_twink_id:
                raise sqlite3.IntegrityError("Основной Discord ID и Discord ID твинка не могут совпадать.")

            if new_telegram_id is not None and new_telegram_id == new_telegram_bot_id:
                raise sqlite3.IntegrityError("Telegram ID и Telegram Bot ID не могут совпадать.")

            existing_row = self.fetchone("""
                SELECT id
                FROM users
                WHERE id != ?
                  AND (
                        discord_id IN (?, ?)
                     OR discord_twink_id IN (?, ?)
                     OR telegram_id IN (?, ?)
                     OR telegram_bot_id IN (?, ?)
                  )
                LIMIT 1
            """,(
                user_id,
                new_discord_id, new_discord_twink_id,
                new_discord_id, new_discord_twink_id,
                new_telegram_id, new_telegram_bot_id,
                new_telegram_id, new_telegram_bot_id,
            ))

            if existing_row is not None:
                raise sqlite3.IntegrityError("Один из переданных ID уже привязан к другому пользователю.")

            values.append(user_id)

            row = self.fetchone(f"""
                UPDATE users
                SET {", ".join(updates)}
                WHERE id = ?
                RETURNING *
            """, tuple(values))

        return DataTypes.Ozernik(row)

    def delete_user(self, user_id: int) -> bool:
        """
        Удаляет пользователя из таблицы users.

        При удалении пользователя также удаляются связанные данные
        из таблиц, внешние ключи которых используют ON DELETE CASCADE.

        :param user_id: Внутренний ID пользователя в базе данных.
        :return: True, если пользователь был удалён, иначе False.
        """
        with self.transaction():
            row = self.fetchone("""
                DELETE FROM users
                WHERE id = ?
                RETURNING id
            """,(user_id,))

        return row is not None

class KarmaDatabase(Database):
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
                    weekly_karma = weekly_karma + ?
                WHERE user_id = ?
                RETURNING *
            """, (karma, karma if weekly else 0, user_id))

        return DataTypes.Karma(old_row), DataTypes.Karma(new_row)

    def remove_karma(self, user_id: int, karma: int) -> DataTypes.Karma:
        """
        Уменьшает обычную карму пользователя.

        При необходимости также должна уменьшать gift_karma, если подарочная карма
        стала больше обычной кармы.

        :param user_id: Внутренний ID пользователя из таблицы users.
        :param karma: Количество убираемой кармы.
        :return: Обновлённый объект ``Karma``.
        :raises ValueError:
        """
        self._ensure_karma_row(user_id)

        with self.transaction():
            row = self.fetchone("""
                UPDATE karma
                SET karma = karma - ?,
                    gift_karma = MIN(
                        gift_karma,
                        MAX(karma - ?, 0)
                    )
                WHERE user_id = ?
                RETURNING *
            """, (karma, karma, user_id))

        return DataTypes.Karma(row)

    def set_karma(self, user_id: int, karma: int) -> DataTypes.Karma:
        """
        Устанавливает точное значение обычной кармы пользователя.

        :param user_id: Внутренний ID пользователя из таблицы users.
        :param karma: Новое значение обычной кармы.
        :return: Обновлённый объект ``Karma``.
        :raises ValueError:
        """
        self._ensure_karma_row(user_id)

        with self.transaction():
            row = self.fetchone("""
                UPDATE karma
                SET karma = ?
                WHERE user_id = ?
                RETURNING *
            """, (karma, user_id))

        return DataTypes.Karma(row)

    def add_gift_karma(self, user_id: int, karma: int, weekly: bool = False) -> DataTypes.Karma:
        """
        Добавляет пользователю подарочную карму.

        Увеличивает общую карму пользователя и отдельно учитывает её
        как подарочную в gift_karma. Если weekly=True, также увеличивает
        weekly_karma.

        gift_karma нужна для учёта того, какая часть общей кармы была
        получена в подарок.

        :param user_id: Внутренний ID пользователя из таблицы users.
        :param karma: Количество добавляемой подарочной кармы.
        :param weekly: Учитывать ли изменение в недельной карме.
        :return: Обновлённый объект ``Karma``.
        """
        self._ensure_karma_row(user_id)

        with self.transaction():
            row = self.fetchone("""
                UPDATE karma
                SET karma = karma + ?,
                    gift_karma = gift_karma + ?,
                    weekly_karma = weekly_karma + ?
                WHERE user_id = ?
                RETURNING *
            """,(karma, karma, karma if weekly else 0, user_id))

        return DataTypes.Karma(row)

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
        пользователь с меньшим внутренним ID.

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
                                    karma.user_id ASC
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
        пользователь большим внутренним ID.

        :return: Список кортежей из объектов Ozernik и Karma
            в порядке занимаемых мест.
        """
        rows = self.fetchall("""
            SELECT users.*,
                   karma.user_id,
                   karma.karma,
                   karma.gift_karma,
                   karma.weekly_karma
            FROM users
            JOIN karma ON karma.user_id = users.id
            ORDER BY karma.karma DESC,
                     users.id ASC
        """, (limit, offset))

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
                   karma.karma,
                   karma.gift_karma,
                   karma.weekly_karma
            FROM users
            JOIN karma ON karma.user_id = users.id
            ORDER BY karma.weekly_karma DESC,
                     karma.karma ASC,
                     users.id ASC
        """)

        return [(DataTypes.Ozernik(row), DataTypes.Karma(row)) for row in rows]

    # Функции логов кармы
    def _add_karma_log(self, user_id: int, added_karma: int, added_at_in_unix: int, reason: Literal['message', 'gift', 'n/a']='n/a') -> int:
        """
        Добавляет запись в лог изменения кармы пользователя.

        :param user_id: Внутренний ID пользователя из таблицы users.
        :param added_karma: Количество добавленной или убранной кармы.
        :param added_at_in_unix: Время изменения в Unix timestamp.
        :param reason: Причина изменения кармы:
            message, gift или n/a.
        :return: ID созданной записи в karma_logs.
        """
        if reason not in ("message", "gift", "n/a"):
            raise ValueError('reason должен быть равен "message", "gift" или "n/a".')

        with self.transaction():
            row = self.fetchone("""
                INSERT INTO karma_logs (
                    user_id,
                    added_karma,
                    added_at,
                    reason
                )
                VALUES (?, ?, ?, ?)
                RETURNING id
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
            SELECT *
            FROM karma_logs
            WHERE added_at BETWEEN ? AND ?
            ORDER BY added_at ASC,
                     id ASC
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
            SELECT *
            FROM karma_logs
            WHERE user_id = ?
              AND added_at BETWEEN ? AND ?
            ORDER BY added_at ASC,
                     id ASC
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

    def add_gift_bind_karma(self, user1_id, user2_id, karma: int) -> DataTypes.KarmicBind:
        """
        Добавляет подарочную карму к кармической связи двух пользователей.

        :param user1_id: Внутренний ID первого пользователя.
        :param user2_id: Внутренний ID второго пользователя.
        :param karma: Количество добавляемой подарочной кармы связи.
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
                SET bind_karma = bind_karma + ?,
                    gift_bind_karma = gift_bind_karma + ?
                WHERE user1_id = ?
                  AND user2_id = ?
                RETURNING *
            """, (karma, karma, *pair))

        return DataTypes.KarmicBind(row)

    def remove_bind_karma(self, user1_id, user2_id, karma: int) -> DataTypes.KarmicBind:
        """
        Уменьшает обычную карму кармической связи двух пользователей.

        При необходимости также должна уменьшать gift_binding_karma, если подарочная
        карма связи стала больше обычной кармы связи.

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
                SET bind_karma = bind_karma - ?,
                    gift_bind_karma = MIN(
                        gift_bind_karma,
                        MAX(bind_karma - ?, 0)
                    )
                WHERE user1_id = ?
                  AND user2_id = ?
                RETURNING *
            """, (karma, karma, *pair))

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
                SET bind_karma = ?,
                    gift_bind_karma = MIN(
                        gift_bind_karma,
                        ?
                    )
                WHERE user1_id = ?
                  AND user2_id = ?
                RETURNING *
            """, (karma, karma, *pair))

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
                   karmic_bind.bind_karma,
                   karmic_bind.gift_bind_karma,
                   karmic_bind.weekly_bind_karma
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
    def _add_karmic_bind_log(self, user1_id: int, user2_id: int, added_karma: int, added_at_in_unix: int, reason: Literal['voice', 'reply', 'gift', 'n/a']='n/a') -> int:
        """
        Добавляет запись в лог изменения кармической связи.

        :param user1_id: Внутренний ID первого пользователя.
        :param user2_id: Внутренний ID второго пользователя.
        :param added_karma: Количество добавленной или убранной кармы связи.
        :param added_at_in_unix: Время изменения в Unix timestamp.
        :param reason: Причина изменения связи: ``voice``, ``reply``, ``gift`` или ``n/a``.
        :return: ID созданной записи в karmic_bind_logs.
        :raises ValueError: Если параметр ``reason`` имеет недопустимое значение.
        """
        if reason not in ('voice', 'reply', 'gift', 'n/a'):
            raise ValueError('reason должен быть равен "voice", "reply", "gift" или "n/a".')

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
                RETURNING id
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
            SELECT *
            FROM karmic_bind_logs
            WHERE user1_id = ?
              AND user2_id = ?
              AND added_at >= ?
              AND added_at < ?
            ORDER BY added_at ASC,
                     id ASC
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
            SELECT *
            FROM karmic_bind_logs
            WHERE (user1_id = ? OR user2_id = ?)
              AND added_at >= ?
              AND added_at < ?
            ORDER BY added_at ASC,
                     id ASC
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
            SELECT *
            FROM karmic_bind_logs
            WHERE added_at >= ?
              AND added_at < ?
            ORDER BY added_at ASC,
                     id ASC
        """, (start_time_in_unix, end_time_in_unix))

        return [dict(row) for row in rows]

class BridgeDatabase(Database):
    def add_chat_link(self, discord_chanel_id: int, telegram_chat_id: int, telegram_topic_id: int = -1) -> DataTypes.ChatLink:
        with self.transaction():
            cur = self.execute("""
                INSERT INTO chat_links (
                    discord_chanel_id,
                    telegram_chat_id,
                    telegram_topic_id
                )
                VALUES (?, ?, ?)
                RETURNING *
            """, (discord_chanel_id, telegram_chat_id, telegram_topic_id))
            return DataTypes.ChatLink(cur.fetchone())

    def update_chat_link(self, link_id: int, discord_chanel_id: int | None = None, telegram_chat_id: int | None = None, telegram_topic_id: int | None = None) -> DataTypes.ChatLink | None:
        fields = []
        values = []

        if discord_chanel_id is not None:
            fields.append("discord_chanel_id = ?")
            values.append(discord_chanel_id)

        if telegram_chat_id is not None:
            fields.append("telegram_chat_id = ?")
            values.append(telegram_chat_id)

        if telegram_topic_id is not None:
            fields.append("telegram_topic_id = ?")
            values.append(telegram_topic_id)

        if not fields:
            raise ValueError("You must pass at least one field to change.")

        values.append(link_id)

        with self.transaction():
            cur = self.execute(f"""
                UPDATE chat_links
                SET {", ".join(fields)}
                WHERE id = ?
                RETURNING *
            """, values)

            row = cur.fetchone()
            return DataTypes.ChatLink(row) if row else None

    def get_chat_link(self, chat_id: int, chat_type: str = Literal['link_id', 'discord', 'telegram'], thread_id: int = None) -> DataTypes.ChatLink | None:
        cur = None
        match chat_type:
            case 'link_id':
                cur = self.execute("""
                    SELECT * FROM chat_links WHERE id = ?
                """, (chat_id,))
            case 'discord':
                cur = self.execute("""
                    SELECT * FROM chat_links WHERE discord_chanel_id = ?
                """, (chat_id,))
            case 'telegram':
                cur = self.execute("""
                    SELECT * FROM chat_links WHERE telegram_chat_id = ? AND telegram_topic_id = ?
                """,(chat_id, thread_id,))
            case _:
                raise TypeError(f'Invalid chat type: {chat_type}')

        row = cur.fetchone()  # noqa
        if row is None:
            return None

        return DataTypes.ChatLink(row)

    def del_chat_link(self, link_id: int) -> DataTypes.ChatLink | None:
        with self.transaction():
            cur = self.execute("""
                DELETE FROM chat_links
                WHERE id = ?
                RETURNING *
            """, (link_id,))

            row = cur.fetchone()
            return DataTypes.ChatLink(row) if row else None

    def add_message_link(
            self,
            link_chat_id: int,
            discord_message_id: int,
            telegram_message_id: int,
            user_id: int | None = None,
            text: str | None = None,
            repeater_id: str | None = None
    ) -> DataTypes.MessageLink:
        with self.transaction():
            if user_id:
                cur = self.execute("SELECT 1 FROM users WHERE id = ?", (user_id,))
                if cur.fetchone() is None:
                    raise ValueError(f"user_id={user_id} не существует")

            cur = self.execute("SELECT 1 FROM chat_links WHERE id = ?", (link_chat_id,))
            if cur.fetchone() is None:
                raise ValueError(f"link_chat_id={link_chat_id} не существует")

            cur = self.execute("""
                INSERT INTO messages (
                    user_id,
                    link_chat_id,
                    discord_message_id,
                    telegram_message_id,
                    text,
                    repeater_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
                RETURNING *
            """, (
                user_id,
                link_chat_id,
                discord_message_id,
                telegram_message_id,
                text,
                repeater_id
            ))

            row = cur.fetchone()
            if row is None:
                raise RuntimeError("Не удалось добавить message link")

            chat_link = self.get_chat_link(row["link_chat_id"], "link_id")

            return DataTypes.MessageLink(row, chat_link)

    def get_message_link(self, message_id: int, mess_type: Literal['discord', 'telegram'], chat_link_id: int | None = None) -> DataTypes.MessageLink:
        cur = None
        match mess_type:
            case 'discord':
                cur = self.execute("""
                    SELECT * FROM messages WHERE discord_message_id = ?
                """,(message_id,))
            case 'telegram':
                cur = self.execute("""
                SELECT * FROM messages WHERE telegram_message_id = ? AND link_chat_id = ?
                """,(message_id, chat_link_id))

        row = cur.fetchone()  # noqa
        if row is None:
            return None

        chat_link = self.get_chat_link(row['link_chat_id'], 'link_id')

        return DataTypes.MessageLink(row, chat_link)

    def deep_get_discord_message_link(self, telegram_message_id: int, telegram_chat_id: int) -> DataTypes.MessageLink | None:
        cur = self.execute("""
                SELECT *
                FROM messages AS m
                JOIN chat_links AS c ON c.id = m.link_chat_id
                WHERE m.telegram_message_id = ?
                  AND c.telegram_chat_id = ?
                LIMIT 1
            """, (telegram_message_id, telegram_chat_id))

        row = cur.fetchone()
        if row is None:
            return None

        chat_link = self.get_chat_link(row['link_chat_id'], 'link_id')

        return DataTypes.MessageLink(row, chat_link)

class Assets:
    def __init__(self, path: Path):
        self.path = path

        self.path.mkdir(exist_ok=True)

        self.font_dir = path / 'fonts'
        self.font_dir.mkdir(exist_ok=True)

        self.background_dir = path / 'backgrounds'
        self.background_dir.mkdir(exist_ok=True)

        self.cubes_dir = path / 'cubes'
        self.cubes_dir.mkdir(exist_ok=True)

        self.sansara_dir = path / 'sansara'
        self.sansara_dir.mkdir(exist_ok=True)

    @property
    def fonts(self) -> dict[str, Path]:
        files = {}
        for file in self.font_dir.glob("*.ttf"):
            files[file.stem] = file
        return files

    @property
    def cubes(self) -> dict[str, Path]:
        files = {}
        for file in self.cubes_dir.glob("*.png"):
            files[file.stem] = file
        return files

    @property
    def sansara(self) -> dict[str, Path]:
        files = {}
        for file in self.sansara_dir.glob("*.png"):
            files[file.stem] = file
        return files

    @property
    def backgrounds(self) -> dict[str, Path]:
        files = {}
        for file in self.background_dir.glob("*.png"):
            files[file.stem] = file
        return files

assets = Assets(Path(__file__).parent / 'assets')