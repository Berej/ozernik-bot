"""
Бэкапы баз бота (решение Alium 2026-10-24).

Раз в сутки и по команде /backup бот делает целостный снимок баз, складывает в zip и присылает
в закрытый канал Discord: копии переживут поломку хостинга и доступны админам без доступа
к серверу. Последние BACKUPS_KEPT архивов лежат и на сервере, в папке backups/.

Что входит: базы SQLite (карма/твинки/статистика, РСН, старый Зал славы) и настройки кармы.
Что НЕ входит: .env и config.json — там токены, им не место в Discord.

Восстановление: остановить бота, положить файлы из архива на их места (пути внутри архива —
от корня бота), запустить бота.
"""

import asyncio
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import config

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT / "backups"
BACKUPS_KEPT = 7
BACKUP_INTERVAL = 24 * 60 * 60  # секунд между автоматическими бэкапами

MOSCOW_TZ = timezone(timedelta(hours=3))

# Пути — от корня бота; чего нет на сервере, то пропускается.
SQLITE_FILES = (
    "database.db",          # общая база: карма, твинки, статистика, активность
    "modules/rsn/rsn.db",   # РСН: дела, баллы, наказания
    "ranks.db",             # старый Зал славы
)
PLAIN_FILES = (
    "modules/karma_sistem/data.json",  # настройки кармы (каналы, роли, пороги)
)


def snapshot_sqlite(source: Path, target: Path) -> None:
    """
    Целостный снимок базы SQLite, даже пока бот в неё пишет.

    Обычное копирование файла посреди записи может дать битую копию: часть страниц — до
    записи, часть — после. Встроенный бэкап SQLite (Connection.backup) читает базу как
    один снимок. Соединения свои — функцию можно звать из отдельного потока.
    """
    src = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def prune_backups(out_dir: Path, keep: int = BACKUPS_KEPT) -> None:
    """Оставить на сервере только keep последних архивов (имена сортируются по времени)."""
    archives = sorted(out_dir.glob("backup_*.zip"))
    for old in archives[:-keep] if keep else archives:
        old.unlink(missing_ok=True)


def make_backup(root: Path = ROOT, out_dir: Path = BACKUP_DIR, now: datetime | None = None) -> Path:
    """
    Собрать zip со снимками баз и настройками. Работа синхронная (sqlite3, zipfile) —
    из бота звать через asyncio.to_thread, иначе бот на это время встанет.

    :return: Путь к архиву.
    """
    now = now or datetime.now(MOSCOW_TZ)
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"backup_{now:%Y-%m-%d_%H-%M-%S}.zip"

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for relative in SQLITE_FILES:
                source = root / relative
                if source.exists():
                    snapshot = Path(tmp) / relative.replace("/", "_")
                    snapshot_sqlite(source, snapshot)
                    zf.write(snapshot, relative)

            for relative in PLAIN_FILES:
                source = root / relative
                if source.exists():
                    zf.write(source, relative)

    prune_backups(out_dir)
    return archive


class Backup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Команда и расписание не должны собирать архив одновременно.
        self.lock = asyncio.Lock()

    async def cog_load(self):
        self.daily.start()

    async def cog_unload(self):
        self.daily.cancel()

    # ---------- РАСПИСАНИЕ ----------

    @tasks.loop(minutes=30)
    async def daily(self):
        # Не «в 4 утра», а «прошли ли сутки с прошлого бэкапа»: время прошлого — в config.json.
        # Так бэкап не пропадёт, если бот лежал в назначенный час, и не задвоится после перезапуска.
        if time.time() - (config.BACKUP_LAST_AT or 0) < BACKUP_INTERVAL:
            return

        try:
            await self.backup_now("ежедневный")
        except Exception as error:
            # Время не записано — попробуем снова через 30 минут.
            self.bot.print_error("backup.daily", error)

    @daily.before_loop
    async def before_daily(self):
        await self.bot.wait_until_ready()

    # ---------- БЭКАП ----------

    async def backup_now(self, reason: str) -> str:
        """Сделать бэкап и отправить в канал. :return: текст для админа."""
        async with self.lock:
            archive = await asyncio.to_thread(make_backup)
            config.BACKUP_LAST_AT = int(time.time())
            return await self.send_archive(archive, reason)

    async def send_archive(self, archive: Path, reason: str) -> str:
        channel = self.bot.get_channel(config.BACKUP_CHANNEL_ID or 0)
        if channel is None:
            return (
                f"Бэкап `{archive.name}` сохранён только на сервере: канал для бэкапов не задан "
                f"(`/backup_channel`)."
            )

        size = archive.stat().st_size
        size_text = f"{size / 1024 / 1024:.1f} МБ" if size >= 1024 * 1024 else f"{size // 1024 + 1} КБ"

        if size > channel.guild.filesize_limit:
            text = (
                f"⚠️ Бэкап `{archive.name}` ({size_text}) больше лимита файла на сервере "
                f"({channel.guild.filesize_limit // 1024 // 1024} МБ) — лежит только на сервере, в папке backups/."
            )
            await channel.send(text)
            return text

        await channel.send(
            f"Бэкап ({reason}) · {size_text}",
            file=discord.File(archive, filename=archive.name),
        )
        return f"Бэкап `{archive.name}` ({size_text}) отправлен в {channel.mention}."

    # ---------- КОМАНДЫ ----------

    @staticmethod
    async def check_admin(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True

        await interaction.response.send_message("Только для Администраторов.", ephemeral=True)
        return False

    @app_commands.command(name="backup", description="Сделать бэкап баз бота прямо сейчас.")
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    async def backup(self, interaction: discord.Interaction):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            text = await self.backup_now(f"вручную, {interaction.user.display_name}")
        except Exception as error:
            self.bot.print_error("backup", error)
            text = f"Бэкап не удался: `{error}`"

        await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(name="backup_channel", description="Куда присылать бэкапы баз (закрытый канал).")
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(channel="Канал, который видят только админы")
    async def backup_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await self.check_admin(interaction):
            return

        # В архиве — данные участников (Discord ID, карма, дела РСН): канал для всех не годится.
        if channel.permissions_for(channel.guild.default_role).view_channel:
            await interaction.response.send_message(
                f"{channel.mention} видят все (@everyone) — в бэкапе данные участников. "
                f"Закройте канал и повторите.",
                ephemeral=True,
            )
            return

        bot_permissions = channel.permissions_for(channel.guild.me)
        if not (bot_permissions.send_messages and bot_permissions.attach_files):
            await interaction.response.send_message(
                f"У бота нет права писать и прикреплять файлы в {channel.mention}.",
                ephemeral=True,
            )
            return

        config.BACKUP_CHANNEL_ID = channel.id
        await interaction.response.send_message(f"Бэкапы будут приходить в {channel.mention}.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Backup(bot))
