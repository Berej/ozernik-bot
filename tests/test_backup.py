"""
Бэкапы баз (modules/backup): снимки SQLite в zip, отправка в закрытый канал, расписание.
"""

import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config as config_stub  # заглушка из conftest
from modules.backup import backup


def make_db(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (v TEXT)")
    con.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    con.commit()
    con.close()


def rows_in_archive(archive: Path, member: str, tmp_path: Path) -> list[str]:
    with zipfile.ZipFile(archive) as zf:
        extracted = Path(zf.extract(member, tmp_path / "out"))
    con = sqlite3.connect(extracted)
    try:
        return [r[0] for r in con.execute("SELECT v FROM t ORDER BY rowid")]
    finally:
        con.close()


@pytest.fixture
def root(tmp_path):
    """Корень «бота»: базы, настройки кармы и секретные файлы, которых в бэкапе быть не должно."""
    root = tmp_path / "bot"
    make_db(root / "database.db", ["карма"])
    make_db(root / "modules" / "rsn" / "rsn.db", ["дело"])
    (root / "modules" / "karma_sistem").mkdir(parents=True)
    (root / "modules" / "karma_sistem" / "data.json").write_text('{"a": 1}', encoding="utf-8")
    (root / ".env").write_text("DISCORD_TOKEN_MAIN=secret", encoding="utf-8")
    (root / "config.json").write_text('{"TELEGRAM_TOKEN_1": "secret"}', encoding="utf-8")
    return root


# ---------- АРХИВ ----------

def test_archive_contents(root, tmp_path):
    archive = backup.make_backup(root, tmp_path / "backups", datetime(2026, 10, 24, 4, 0, 0))

    with zipfile.ZipFile(archive) as zf:
        names = sorted(zf.namelist())

    assert archive.name == "backup_2026-10-24_04-00-00.zip"
    assert names == ["database.db", "modules/karma_sistem/data.json", "modules/rsn/rsn.db"]  # ranks.db нет — пропущен
    assert rows_in_archive(archive, "database.db", tmp_path) == ["карма"]
    assert rows_in_archive(archive, "modules/rsn/rsn.db", tmp_path) == ["дело"]


def test_secrets_not_in_archive(root, tmp_path):
    archive = backup.make_backup(root, tmp_path / "backups")

    with zipfile.ZipFile(archive) as zf:
        assert ".env" not in zf.namelist() and "config.json" not in zf.namelist()
        assert all(b"secret" not in zf.read(name) for name in zf.namelist())


def test_snapshot_consistent_during_write(root, tmp_path):
    """Пока другое соединение пишет (транзакция не закончена), снимок — целый, без недописанного."""
    writer = sqlite3.connect(root / "database.db")
    writer.execute("BEGIN")
    writer.execute("INSERT INTO t VALUES ('недописано')")
    try:
        archive = backup.make_backup(root, tmp_path / "backups")
    finally:
        writer.rollback()
        writer.close()

    assert rows_in_archive(archive, "database.db", tmp_path) == ["карма"]


def test_old_archives_pruned(root, tmp_path):
    out = tmp_path / "backups"
    for day in range(1, 11):
        backup.make_backup(root, out, datetime(2026, 10, day, 4, 0, 0))

    kept = sorted(p.name for p in out.glob("backup_*.zip"))

    assert len(kept) == backup.BACKUPS_KEPT
    assert kept[0] == "backup_2026-10-04_04-00-00.zip"  # самые старые удалены


# ---------- ОТПРАВКА И РАСПИСАНИЕ ----------

class FakeChannel:
    def __init__(self, limit=10 * 1024 * 1024, public=False, bot_can_send=True):
        self.mention = "#бэкапы"
        self.send = AsyncMock()
        self.guild = SimpleNamespace(filesize_limit=limit, default_role="everyone", me="bot")
        self._public, self._bot_can_send = public, bot_can_send

    def permissions_for(self, who):
        if who == "everyone":
            return SimpleNamespace(view_channel=self._public)
        return SimpleNamespace(send_messages=self._bot_can_send, attach_files=self._bot_can_send)


@pytest.fixture
def cog(root, tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "ROOT", root)
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(backup.make_backup, "__defaults__", (root, tmp_path / "backups", None))
    monkeypatch.setattr(config_stub.config, "BACKUP_CHANNEL_ID", None, raising=False)
    monkeypatch.setattr(config_stub.config, "BACKUP_LAST_AT", None, raising=False)
    channel = FakeChannel()
    bot = SimpleNamespace(get_channel=lambda cid: channel if cid == 555 else None, print_error=MagicMock())
    return SimpleNamespace(cog=backup.Backup(bot), channel=channel, bot=bot)


def admin_interaction(admin=True):
    return SimpleNamespace(
        user=SimpleNamespace(guild_permissions=SimpleNamespace(administrator=admin), display_name="Alium"),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


async def test_sent_to_channel(cog):
    config_stub.config.BACKUP_CHANNEL_ID = 555

    text = await cog.cog.backup_now("ежедневный")

    message = cog.channel.send.call_args
    assert message.args[0].startswith("Бэкап (ежедневный)")
    assert message.kwargs["file"].filename.startswith("backup_")
    assert "отправлен в #бэкапы" in text
    assert config_stub.config.BACKUP_LAST_AT == pytest.approx(time.time(), abs=5)


async def test_no_channel_kept_on_server(cog):
    text = await cog.cog.backup_now("ежедневный")

    assert "сохранён только на сервере" in text


async def test_too_big_for_discord(cog):
    config_stub.config.BACKUP_CHANNEL_ID = 555
    cog.channel.guild.filesize_limit = 10  # байт

    text = await cog.cog.backup_now("ежедневный")

    assert "больше лимита" in text
    assert "file" not in cog.channel.send.call_args.kwargs


@pytest.mark.parametrize(("hours_ago", "expected"), [(None, True), (25, True), (23, False)])
async def test_daily_once_a_day(cog, monkeypatch, hours_ago, expected):
    if hours_ago is not None:
        config_stub.config.BACKUP_LAST_AT = int(time.time()) - hours_ago * 3600
    cog.cog.backup_now = AsyncMock()

    await cog.cog.daily.coro(cog.cog)

    assert cog.cog.backup_now.await_count == int(expected)


async def test_daily_failure_retried_later(cog, monkeypatch):
    monkeypatch.setattr(backup, "make_backup", MagicMock(side_effect=OSError("диск полон")))

    await cog.cog.daily.coro(cog.cog)

    assert config_stub.config.BACKUP_LAST_AT is None  # время не записано — через 30 минут снова
    cog.bot.print_error.assert_called_once()


async def test_backup_command_admin_only(cog):
    interaction = admin_interaction(admin=False)

    await cog.cog.backup.callback(cog.cog, interaction)

    assert interaction.response.send_message.call_args.args[0] == "Только для Администраторов."


async def test_backup_command(cog):
    config_stub.config.BACKUP_CHANNEL_ID = 555
    interaction = admin_interaction()

    await cog.cog.backup.callback(cog.cog, interaction)

    assert cog.channel.send.call_args.args[0].startswith("Бэкап (вручную, Alium) · ")
    assert "отправлен" in interaction.followup.send.call_args.args[0]


@pytest.mark.parametrize(("channel", "answer"), [
    (FakeChannel(public=True), "видят все"),
    (FakeChannel(bot_can_send=False), "нет права"),
])
async def test_backup_channel_refused(cog, channel, answer):
    channel.id = 777
    interaction = admin_interaction()

    await cog.cog.backup_channel.callback(cog.cog, interaction, channel)

    assert answer in interaction.response.send_message.call_args.args[0]
    assert config_stub.config.BACKUP_CHANNEL_ID is None


async def test_backup_channel_set(cog):
    channel = FakeChannel()
    channel.id = 777

    await cog.cog.backup_channel.callback(cog.cog, admin_interaction(), channel)

    assert config_stub.config.BACKUP_CHANNEL_ID == 777
