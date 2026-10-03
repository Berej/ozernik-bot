import json
import os
import traceback

import discord
import asyncio
from threading import Lock
from pathlib import Path
from typing import Any, Literal
import sqlite3
import time
from collections.abc import MutableMapping
from copy import deepcopy

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
            self._write_json({})
            return {}

    def _write_json(self, data: dict[str, Any]) -> None:
        """
        Безопасная запись: сначала во временный файл рядом, потом подмена старого.

        Если бота выключат посреди записи, на диске останется старый целый файл,
        а не обрезанный наполовину (os.replace подменяет файл одним действием).
        """
        tmp_path = self.json_path.with_name(self.json_path.name + ".tmp")

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        os.replace(tmp_path, self.json_path)

    def _commit_data(self) -> None:
        self._write_json(self._data)

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

class NewDataWorker(JsonWorker, MutableMapping):
    """
    JSON-словарь, который пишет на диск фоновой задачей (чтобы запись не тормозила бота).

    Правила, выученные на потере текстов уровней (2026-09-29):
    - ошибка записи не должна убивать задачу-писатель: раньше после первой же ошибки
      правки жили только в памяти и молча пропадали при перезапуске;
    - при закрытии — последняя запись: иначе правка за долю секунды до выключения терялась.
    """

    # Пауза перед повтором после ошибки записи (секунды).
    RETRY_DELAY = 10

    def __init__(self, path: str, setup: dict | None = None):
        super().__init__(path)

        # Есть правки, ещё не дошедшие до диска.
        self._dirty = False

        self._commit_event = asyncio.Event()
        self._commit_task = asyncio.create_task(
            self._commit_worker()
        )

        if setup:
            self._data.update({**setup, **self._data})
            self._commit()

    async def _commit_worker(self):
        while True:
            await self._commit_event.wait()
            self._commit_event.clear()

            # Снимок в основном потоке: пока файл пишется в другом потоке,
            # словарь могут менять — json.dump по живому словарю может упасть.
            snapshot = deepcopy(self._data)

            try:
                await asyncio.to_thread(self._write_json, snapshot)
            except asyncio.CancelledError:
                raise
            except Exception:
                print(
                    f"[NewDataWorker] Не удалось записать {self.json_path}, "
                    f"повтор через {self.RETRY_DELAY} с:\n{traceback.format_exc()}"
                )
                await asyncio.sleep(self.RETRY_DELAY)
                self._commit_event.set()
                continue

            # Если за время записи пришла новая правка — событие снова взведено.
            self._dirty = self._commit_event.is_set()

    def _commit(self):
        self._dirty = True
        self._commit_event.set()

    async def close(self):
        self._commit_task.cancel()

        try:
            await self._commit_task
        except asyncio.CancelledError:
            pass

        # Последняя запись того, что не успело дойти до диска.
        if self._dirty:
            try:
                self._write_json(self._data)
                self._dirty = False
            except Exception:
                print(f"[NewDataWorker] Не удалось записать {self.json_path} при закрытии:\n{traceback.format_exc()}")

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value
        self._commit()

    def __delitem__(self, key):
        del self._data[key]
        self._commit()

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

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

    class Karma(OzernikDataType):
        def __init__(self, row: sqlite3.Row):
            self.user_id = row['user_id']
            self.karma = row['karma']
            self.status = row['status']
            self.weekly_karma = row['weekly_karma']
            self.karma_updated_at = row['karma_updated_at'] # Время последнего изменения кармы в мс

    class KarmicBind(OzernikDataType):
        def __init__(self, row: sqlite3.Row):
            self.ids = (row['user1_id'], row['user2_id'])
            self.bind_karma = row['bind_karma']

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

def now_ms() -> int:
    """Текущее время в миллисекундах (Unix)."""
    return time.time_ns() // 1_000_000

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

    def _rebuild_table(self, name: str, create_sql: str, columns: str, where: str = '') -> None:
        """
        Пересоздаёт таблицу по новой схеме с сохранением данных.

        SQLite не умеет менять CHECK у существующей таблицы, а DROP COLUMN
        есть только с версии 3.35 — поэтому стандартный путь: переименовать
        старую, создать новую, перенести строки, удалить старую.
        """
        self.execute(f"ALTER TABLE {name} RENAME TO {name}_old")
        self.execute(create_sql)
        self.execute(f"INSERT INTO {name} ({columns}) SELECT {columns} FROM {name}_old {where}")
        self.execute(f"DROP TABLE {name}_old")

    def _table_columns(self, name: str) -> set[str]:
        return {row['name'] for row in self.fetchall(f"PRAGMA table_info({name})")}

    def _table_sql(self, name: str) -> str:
        return self.fetchone(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        )['sql']

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

class BridgeDatabase(Database):
    def add_chat_link(self, discord_channel_id: int, telegram_chat_id: int, telegram_topic_id: int = -1) -> DataTypes.ChatLink:
        with self.transaction():
            cur = self.execute("""
                INSERT INTO chat_links (
                    discord_channel_id,
                    telegram_chat_id,
                    telegram_topic_id
                )
                VALUES (?, ?, ?)
                RETURNING *
            """, (discord_channel_id, telegram_chat_id, telegram_topic_id))
            return DataTypes.ChatLink(cur.fetchone())

    def update_chat_link(self, link_id: int, discord_channel_id: int | None = None, telegram_chat_id: int | None = None, telegram_topic_id: int | None = None) -> DataTypes.ChatLink | None:
        fields = []
        values = []

        if discord_channel_id is not None:
            fields.append("discord_channel_id = ?")
            values.append(discord_channel_id)

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
                    SELECT * FROM chat_links WHERE discord_channel_id = ?
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


""" 
Работа с discord.LayoutView. Позволяет сверстать много-вложенные сообщения без репетативной работы. 
"""

class Page:
    title = 'None'

    def __init__(self, navigator, author: discord.Member):
        self.navigator = navigator
        self.author = author

    def build(self):
        view = discord.ui.LayoutView(timeout=360)
        view.interaction_check = self._interaction_check

        async def on_timeout():
            await self._on_timeout(view)

        view.on_timeout = on_timeout

        container = discord.ui.Container()
        view.add_item(container)

        self.build_header(container)
        self.build_content(container)
        self.build_footer(container)

        return view

    async def _interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Это меню принадлежит другому пользователю.",
                ephemeral=True,
            )
            return False

        return True

    async def _on_timeout(self, view: discord.ui.LayoutView):
        if self.navigator.current_page != self:
            return

        for item in view.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True

        if self.navigator.message:
            await self.navigator.message.edit(view=view)

    def build_header(self, container):
        container.add_item(
            discord.ui.TextDisplay(f'# {self.title}')
        )

        container.add_item(
            discord.ui.Separator()
        )

    def build_content(self, container):
        raise NotImplementedError

    def build_footer(self, container):
        container.add_item(
            discord.ui.Separator()
        )

        row = discord.ui.ActionRow()

        if self.navigator.can_back:
            back_button = discord.ui.Button(
                label="Назад",
                style=discord.ButtonStyle.secondary
            )

            back_button.callback = self._back_callback

            row.add_item(back_button)

        for button in self.build_footer_buttons():
            row.add_item(button)

        if len(row.children):
            container.add_item(row)

    def build_footer_buttons(self): # noqa
        return []

    async def _back_callback(self, interaction): # noqa
        await self.navigator.back()
        await interaction.response.defer()

    def create_confirm_button(
            self,
            label: str,
            confirm_label: str,
            action,
    ) -> discord.ui.Button:

        button = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.primary,
        )

        async def first_callback(interaction: discord.Interaction):
            await interaction.response.defer()

            button.label = confirm_label
            button.style = discord.ButtonStyle.danger
            button.callback = second_callback

            await self.navigator.render()

        async def second_callback(interaction: discord.Interaction):
            await interaction.response.defer()

            button.label = label
            button.style = discord.ButtonStyle.primary
            button.callback = first_callback

            await action(interaction)

            await self.navigator.render()

        button.callback = first_callback

        return button

    async def on_open(self):
        pass

    async def on_close(self):
        pass

class Navigator:
    def __init__(self, page, **kwargs):
        self.history = []
        self.current_page = page(
            self,
            **kwargs
        )
        self.message = None

    @property
    def can_back(self):
        return bool(self.history)

    async def send(self, interaction):
        view = self.current_page.build()

        await interaction.response.send_message(view=view)

        self.message = await interaction.original_response()

    async def push(self, page, **kwargs):
        try:
            await self.current_page.on_close()

            self.history.append(self.current_page)

            self.current_page = page(
                self,
                **kwargs
            )

            await self.current_page.on_open()

            await self.render()
        except Exception as e:
            print(e)
            print(traceback.format_exc())

    async def back(self):
        if not self.history:
            return

        await self.current_page.on_close()

        self.current_page = self.history.pop()

        await self.current_page.on_open()

        await self.render()

    async def render(self):
        view = self.current_page.build()

        await self.message.edit(
            view=view
        )

    async def close(self):
        await self.current_page.on_close()
        await self.message.delete()

































