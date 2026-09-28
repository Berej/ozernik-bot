"""
Общая подготовка тестов.

Модуль кармы при импорте трогает боевые файлы (database.db, data.json,
levels.json, config.json, .env), поэтому здесь всё это подменяется:

- ``config`` — заглушка без .env и без запроса токенов;
- ``Database`` — SQLite в памяти;
- JSON-хранилища — временная папка;
- ``NewDataWorker`` — синхронная версия (оригинал требует запущенный event loop).

Боевой код при этом не меняется.
"""

import copy
import sqlite3
import sys
import tempfile
import types
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GUILD_ID = 1000
OWNER_ID = 512079329619083291


# ---------- ЗАГЛУШКА CONFIG -----------

_config_module = types.ModuleType("config")
_config_module.config = SimpleNamespace(
    GUILD_ID=GUILD_ID,
    OWNERS_IDS=[OWNER_ID, 436586208450052099],
    ADMIN_ROLES_IDS=[],
    MODULES={},
)
_config_module.DISCORD_TOKEN_MAIN = "test"
_config_module.TELEGRAM_TOKEN_MAIN = "test"
_config_module.TELEGRAM_TOKEN_REPEATER_1 = "test"
_config_module.TELEGRAM_TOKEN_REPEATER_2 = "test"
sys.modules["config"] = _config_module


# ---------- ПОДМЕНА ХРАНИЛИЩ -----------

import utilities  # noqa: E402


def _memory_database_init(self) -> None:
    self._con = sqlite3.connect(":memory:")
    self._con.row_factory = sqlite3.Row
    self._con.execute("PRAGMA foreign_keys = ON")
    self._init_db()


utilities.Database.__init__ = _memory_database_init


class SyncNewDataWorker(utilities.NewDataWorker):
    """NewDataWorker без фоновой задачи: пишет на диск сразу."""

    def __init__(self, path, setup: dict | None = None):
        utilities.JsonWorker.__init__(self, path)

        if setup:
            self._data.update({**setup, **self._data})
            self._commit()

    def _commit(self):
        self._commit_data()

    async def close(self):
        pass


utilities.NewDataWorker = SyncNewDataWorker

_IMPORT_TMP = Path(tempfile.mkdtemp(prefix="ozernik_tests_"))
_original_json_init = utilities.JsonWorker.__init__


def _tmp_json_init(self, path):
    _original_json_init(self, _IMPORT_TMP / Path(path).name)


# JSON-файлы, создаваемые при импорте модуля, уходят во временную папку.
utilities.JsonWorker.__init__ = _tmp_json_init
try:
    import bot as bot_module  # noqa: E402
    import modules.karma_sistem.karma_sistem as karma  # noqa: E402
finally:
    utilities.JsonWorker.__init__ = _original_json_init


# ---------- ФЕЙКОВЫЕ ОБЪЕКТЫ DISCORD -----------

def make_png_bytes(size=(64, 64), color=(200, 50, 50, 255)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


PNG_BYTES = make_png_bytes()


class FakeRole:
    def __init__(self, role_id: int, name: str = "role", guild=None):
        self.id = role_id
        self.name = name
        self.guild = guild
        self.colour = discord.Colour.default()
        self.mention = f"<@&{role_id}>"
        self.edit = AsyncMock()

    def __eq__(self, other):
        return isinstance(other, FakeRole) and other.id == self.id

    def __hash__(self):
        return hash(self.id)

    def __repr__(self):
        return f"FakeRole({self.id})"


class FakeChannel:
    def __init__(self, channel_id: int, members=None, name="channel"):
        self.id = channel_id
        self.name = name
        self.members = members or []
        self.send = AsyncMock()

    def __eq__(self, other):
        return isinstance(other, FakeChannel) and other.id == self.id

    def __hash__(self):
        return hash(self.id)


class FakeMember:
    def __init__(
        self,
        member_id: int,
        guild=None,
        *,
        bot: bool = False,
        roles=None,
        self_deaf: bool = False,
        deaf: bool = False,
        self_mute: bool = False,
        avatar: bool = True,
    ):
        self.id = member_id
        self.bot = bot
        self.guild = guild
        self.roles = list(roles or [])
        self.name = f"user{member_id}"
        self.display_name = f"User {member_id}"
        self.mention = f"<@{member_id}>"
        self.voice = SimpleNamespace(self_deaf=self_deaf, deaf=deaf, self_mute=self_mute)
        self.avatar = SimpleNamespace(url=f"https://cdn.test/{member_id}.png") if avatar else None
        self.display_avatar = SimpleNamespace(
            url=f"https://cdn.test/{member_id}.png",
            read=AsyncMock(return_value=PNG_BYTES),
        )
        self.guild_permissions = SimpleNamespace(administrator=False)
        self.top_role = FakeRole(1, "top")

        async def add_roles(*roles, reason=None):
            for role in roles:
                if role not in self.roles:
                    self.roles.append(role)

        async def remove_roles(*roles, reason=None):
            for role in roles:
                if role in self.roles:
                    self.roles.remove(role)

        self.add_roles = AsyncMock(side_effect=add_roles)
        self.remove_roles = AsyncMock(side_effect=remove_roles)

    def __eq__(self, other):
        return isinstance(other, FakeMember) and other.id == self.id

    def __hash__(self):
        return hash(self.id)


class FakeGuild:
    def __init__(self):
        self.id = GUILD_ID
        self.name = "Озёрники"
        self.icon = SimpleNamespace(url="https://cdn.test/guild.png")
        self.members: list[FakeMember] = []
        self.voice_channels: list[FakeChannel] = []
        self.afk_channel = None
        self.roles: dict[int, FakeRole] = {}
        self._next_role_id = 900_000

        self.fetch_role = AsyncMock(side_effect=self._fetch_role)
        self.create_role = AsyncMock(side_effect=self._create_role)

    def add_member(self, member_id: int, **kwargs) -> FakeMember:
        member = FakeMember(member_id, self, **kwargs)
        self.members.append(member)
        return member

    def get_member(self, member_id: int):
        return next((m for m in self.members if m.id == member_id), None)

    def get_role(self, role_id: int):
        return self.roles.get(role_id)

    async def _fetch_role(self, role_id: int):
        raise discord.NotFound(MagicMock(status=404), "Unknown Role")

    async def _create_role(self, *, name, colour=None, **kwargs):
        self._next_role_id += 1
        role = FakeRole(self._next_role_id, name, guild=self)
        role.colour = colour
        self.roles[role.id] = role
        return role


class FakeBot:
    """Минимальная замена OzernikiBot с настоящими db_ensure_user и print_error."""

    db_ensure_user = bot_module.OzernikiBot.db_ensure_user
    print_error = staticmethod(bot_module.OzernikiBot.print_error)

    def __init__(self, db, guild: FakeGuild):
        self.db = db
        self.guild = guild
        self.ready = True
        self.moscow_tz = bot_module.MOSCOW_TZ
        self.channels: dict[int, FakeChannel] = {}
        self.user = SimpleNamespace(id=1)
        self.fetch_user = AsyncMock(side_effect=lambda user_id: guild.get_member(user_id))

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def wait_until_ready(self):
        pass


def make_interaction(user: FakeMember, guild: FakeGuild):
    response = SimpleNamespace(
        send_message=AsyncMock(),
        defer=AsyncMock(),
        send_modal=AsyncMock(),
        is_done=MagicMock(return_value=False),
    )
    followup_message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(
        user=user,
        guild=guild,
        response=response,
        followup=SimpleNamespace(send=AsyncMock(return_value=followup_message)),
        original_response=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())),
    )


def make_message(author, channel: FakeChannel, guild, content: str = "привет"):
    return SimpleNamespace(
        author=author,
        channel=channel,
        guild=guild,
        content=content,
        reply=AsyncMock(),
    )


# ---------- ФИКСТУРЫ -----------

@pytest.fixture
def env(monkeypatch, tmp_path):
    """
    Чистое окружение модуля кармы на каждый тест:
    новая БД в памяти, новые data.json/levels.json во временной папке,
    фейковые бот и сервер, готовый Cog.
    """
    db = karma.KarmaDatabase()
    data = utilities.DataWorker(tmp_path / "data.json", setup=copy.deepcopy(karma.data_setup))
    levels = SyncNewDataWorker(tmp_path / "levels.json", setup=dict(karma.levels_setup))

    monkeypatch.setattr(karma, "db", db)
    monkeypatch.setattr(karma, "data", data)
    monkeypatch.setattr(karma, "levels_data", levels)
    monkeypatch.setattr(karma.SettingsSansaraPage, "restore_task", None)
    monkeypatch.setattr(karma.SettingsCubesPage, "restore_task", None)

    guild = FakeGuild()
    bot = FakeBot(db, guild)

    karma_channel = FakeChannel(5000, name="karma")
    bot.channels[karma_channel.id] = karma_channel

    cog = karma.KarmaSistem(bot)

    return SimpleNamespace(
        karma=karma,
        db=db,
        data=data,
        levels=levels,
        guild=guild,
        bot=bot,
        cog=cog,
        karma_channel=karma_channel,
    )


def enable_karma_channel(env) -> FakeChannel:
    env.data.karma_channel_id = env.karma_channel.id
    return env.karma_channel


def create_all_roles(env) -> None:
    """Создаёт на фейковом сервере все роли Сансары и Кубов и прописывает их ID в data.json."""
    karma_roles = env.data.karma_roles
    for n, (tag, role) in enumerate(karma_roles.items(), start=1):
        role_id = 100 + n
        env.guild.roles[role_id] = FakeRole(role_id, role["name"], guild=env.guild)
        role["role_id"] = role_id
    env.data.karma_roles = karma_roles

    cube_roles = env.data.cube_roles
    for n, (tag, role) in enumerate(cube_roles.items(), start=1):
        role_id = 200 + n
        env.guild.roles[role_id] = FakeRole(role_id, role["name"], guild=env.guild)
        role["role_id"] = role_id
    env.data.cube_roles = cube_roles


def add_ozernik(env, discord_id: int) -> int:
    return env.db.add_user(discord_id=discord_id)
