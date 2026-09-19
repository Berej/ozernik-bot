# fame_cog.py
import asyncio
import aiosqlite
import csv
import logging
import os
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import config

# ====== Settings ======
GUILD_ID = config.GUILD_ID
DB_PATH = "ranks.db"
LOG_CHANNEL_ID = 1342897615905226842  # log channel
#LOG_CHANNEL_ID = 1409197452954828990 # log channel test server
LOG_COLOR = 0x2F3136  # Color for embed messages on log channel
# moderator roles
FAME_MODS = {779015800555176006, 725675581881974794, 1296437623761539146, 780422873122603018, 1398937618527293510}

# Directory for reference
REASON_TO_POINTS = {
    1: 6,   # event
    2: 4,   # sigame
    3: 2,   # top_1
    4: 1,   # top_3
    5: 1,   # lib
    6: 2,   # public
    7: 3,   # role_active
    8: 2,   # role_custom
    9: 2,   # fmafia_victory
    10: 1,  # fmafia_highlight
    11: 2,  # fmafia_game
}

RES_TEXT = {
    1: 'за победу в ивенте',
    2: 'за победу в SIGame',
    3: 'за топ-1 по опыту в неделе',
    4: 'за топ-3 по опыту в неделе',
    5: 'за публикацию в "Библиотеке"',
    6: 'за публикацию в "Публикациях"',
    7: 'за кастомную роль за заслуги',
    8: 'за кастомную роль "просто так"',
    9: 'за победу в фмафеи',
    10: 'Хайлайтер фмафеи',
    11: 'за проведение партии в фмафеи'
}

logger = logging.getLogger(__name__)


def is_gm(user: discord.abc.User) -> bool:
    """Check if a user is a moderator (by roles + two static ids)."""
    if user.id in {640504347137933334, 685425244051079222}:
        return True
    if hasattr(user, "roles"):
        for role in user.roles:
            if role.id in FAME_MODS:
                return True
    return False


def fame_note_embed(author: discord.User, target: discord.User, action: int, reason_text: str, count: int) -> discord.Embed:
    """
    Build an embed for logging score changes.
    action: 1 = plus, 2 = minus
    reason_text: string provided by the moderator
    """
    note = discord.Embed(color=LOG_COLOR)
    note.set_author(name=author.name, icon_url=author.avatar.url if hasattr(author, "avatar") and author.avatar else None)
    sign = "``+``" if action == 1 else "``-``"
    # limit length of 'reason' in embed log message
    reason_display = (reason_text[:300] + '...') if reason_text and len(reason_text) > 300 else (reason_text or "Админская правка")
    target_name = getattr(target, "display_name", str(target))
    target_tag = getattr(target, "name", "")
    note.add_field(name=f'{target_name} ({target_tag} | {target.id})', value=f'{sign} **{count}** — {reason_display}')
    if hasattr(target, "avatar") and target.avatar:
        note.set_thumbnail(url=target.avatar.url)
    note.set_footer(text=f'by {author} • {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}')
    return note


class FameGroup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        # Initialize DB asynchronously
        self._db_init_task = bot.loop.create_task(self._init_db())

    async def _init_db(self):
        """
        Initialize the database for the project:
        table rank: uid, score, past_seasons
        table glory_settings: single-row (id=1)
        """
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            CREATE TABLE IF NOT EXISTS rank (
                uid INTEGER PRIMARY KEY,
                score INTEGER NOT NULL DEFAULT 0,
                past_seasons INTEGER NOT NULL DEFAULT 0
            );
            """)
            await db.execute("""
            CREATE TABLE IF NOT EXISTS glory_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                glory_channel INTEGER,
                glory_message INTEGER,
                season INTEGER NOT NULL DEFAULT 1
            );
            """)
            await db.commit()
        logger.info("DB initialized (clean schema: uid, score, past_seasons)")

    # ---------------- DB helpers ----------------
    async def ensure_member(self, uid: int):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO rank (uid) VALUES (?) ON CONFLICT(uid) DO NOTHING;", (uid,))
            await db.commit()

    async def get_member_row(self, uid: int) -> Optional[aiosqlite.Row]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT uid, score, past_seasons FROM rank WHERE uid=?", (uid,))
            row = await cur.fetchone()
            await cur.close()
            return row

    async def modify_score(self, uid: int, delta: int):
        """
        Add delta (can be negative) to score.
        Returns a dict with old_score/new_score.
        Prevents resulting score from going below zero.
        """
        async with self._lock:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO rank (uid) VALUES (?) ON CONFLICT(uid) DO NOTHING;", (uid,))
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT score FROM rank WHERE uid=?", (uid,))
                row = await cur.fetchone()
                await cur.close()
                old_score = int(row["score"]) if row else 0
                new_score = max(0, old_score + delta)
                await db.execute("UPDATE rank SET score = ? WHERE uid = ?", (new_score, uid))
                await db.commit()
                return {"uid": uid, "old_score": old_score, "new_score": new_score}

    async def top_n(self, n: int = 10):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT uid, score, past_seasons FROM rank ORDER BY score DESC LIMIT ?", (n,))
            rows = await cur.fetchall()
            await cur.close()
            return rows

    async def get_glory_settings(self):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM glory_settings WHERE id = 1")
            row = await cur.fetchone()
            await cur.close()
            return row

    async def set_glory_settings(self, channel_id: int, message_id: int, season: int):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO glory_settings (id, glory_channel, glory_message, season) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET glory_channel=excluded.glory_channel, glory_message=excluded.glory_message, season=excluded.season;",
                (channel_id, message_id, season)
            )
            await db.commit()

    async def inc_past_seasons_and_reset(self):
        """
        Move seasonal scores into past_seasons and reset current scores.
        """
        async with self._lock:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE rank SET past_seasons = COALESCE(past_seasons,0) + COALESCE(score,0)")
                await db.execute("UPDATE rank SET score = 0")
                await db.execute("UPDATE glory_settings SET season = season + 1 WHERE id = 1")
                await db.commit()

    # ---------------- Embeds / reports ----------------
    async def create_top_embed(self, guild: discord.Guild, limit: int = 25, viewer: Optional[discord.User] = None) -> discord.Embed:
        """
        Build the hall-of-fame top embed. If viewer is provided — append their scores at the end.
        Shows season score, past_seasons and the total.
        """
        embed = discord.Embed(title='Топ Зала славы', color=discord.Color.red())
        rows = await self.top_n(n=limit)
        num = 1
        for row in rows:
            uid = int(row["uid"])
            season_score = int(row["score"] or 0)
            past = int(row["past_seasons"] or 0)
            if season_score == 0 and past == 0:
                continue
            member = guild.get_member(uid)
            name = member.display_name if member else f"Пользователь ({uid})"
            place = ':first_place:' if num == 1 else ':second_place:' if num == 2 else ':third_place:' if num == 3 else f'{num}.'
            total = season_score + past
            embed.add_field(
                name=f'{place} {name}',
                value=f'Баллы в сезоне: **{season_score}**\nПрошлые сезоны: **{past}**\n—# Всего: **{total}**',
                inline=False
            )
            num += 1

        if viewer is not None:
            member = guild.get_member(viewer.id)
            viewer_name = member.display_name if member else getattr(viewer, "name", str(viewer))
            row = await self.get_member_row(viewer.id)
            season_score = int(row["score"]) if row and row["score"] is not None else 0
            past = int(row["past_seasons"]) if row and row["past_seasons"] is not None else 0
            total = season_score + past
            embed.add_field(
                name=f'Ваши баллы — {viewer_name}',
                value=(f'Баллы в сезоне: **{season_score}**\n'
                       f'Баллы в прошлых сезонах: **{past}**\n'
                       f'—# Всего: **{total}**'),
                inline=False
            )

        return embed

    async def create_report_file(self, guild: discord.Guild) -> str:
        """Generate a CSV report (uid, score, past_seasons)."""
        rows = await self._get_all_ranks()
        settings = await self.get_glory_settings()
        season = settings["season"] if settings else 1
        filename = f'Отчёт_зал_славы_{guild.id}_season_{season}_{datetime.now().strftime("%d%m%Y%H%M%S")}.csv'
        with open(filename, 'w', encoding='utf-8-sig', newline='') as csvfile:
            writer = csv.writer(csvfile, delimiter=';')
            header = ['Участник', 'Баллы в сезоне', 'Прошлые сезоны', 'Всего']
            writer.writerow(header)
            for row in rows:
                uid = int(row["uid"])
                member = guild.get_member(uid)
                user_display = f"{member.display_name} ({member.name} | {uid})" if member else f"Потерянный пользователь ({uid})"
                season_score = int(row["score"] or 0)
                past = int(row["past_seasons"] or 0)
                writer.writerow([user_display, season_score, past, season_score + past])
        return filename

    async def _get_all_ranks(self):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT uid, score, past_seasons FROM rank ORDER BY score DESC")
            rows = await cur.fetchall()
            await cur.close()
            return rows

    # ---------------- Helper: ensure command called in correct guild ----------------
    async def _check_guild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != GUILD_ID:
            try:
                await interaction.response.send_message('Команда доступна только на основном сервере.', ephemeral=True)
            except Exception:
                pass
            return False
        return True

    # --------------- Init fame hall slash command -----------------------

    @app_commands.command(name='startfamehall', description='Запустить "Зал славы"')
    @app_commands.rename(channel='канал')
    @app_commands.describe(channel='Канал "Зал славы", в котором публикуется таблица лидеров')
    @app_commands.guilds(GUILD_ID)
    async def start_fame(self, interaction: discord.Interaction, channel: discord.TextChannel):
        # check guild + permissions
        if not await self._check_guild(interaction):
            return
        if not is_gm(interaction.user):
            await interaction.response.send_message('У Вас нет доступа к этой команде', ephemeral=True)
            return

        # the channel must be on the same server
        if channel.guild is None or channel.guild.id != interaction.guild.id:
            await interaction.response.send_message('Укажите канал этого сервера.', ephemeral=True)
            return

        # get current season (if exists) — otherwise 1
        settings = await self.get_glory_settings()
        season = settings["season"] if settings else 1

        try:
            # build embed and send the wall message
            top_embed = await self.create_top_embed(interaction.guild, limit=25, viewer=None)
            wall_msg = await channel.send(embed=top_embed)

            # save settings (channel + message id + season)
            await self.set_glory_settings(channel.id, wall_msg.id, season)

            await interaction.response.send_message(f'Топ запущен: {wall_msg.jump_url}', ephemeral=True)
        except Exception:
            logger.exception("Ошибка при запуске зала славы")
            try:
                await interaction.response.send_message('Произошла ошибка при запуске зала славы.', ephemeral=True)
            except Exception:
                pass

    # ---------------- New moderation commands: /add & /rem ----------------
    @app_commands.command(name='add', description='Добавить указанное количество баллов участнику')
    @app_commands.rename(user="участник", amount="сумма", reason="причина")
    @app_commands.describe(user="Участник, которому нужно добавить баллы", amount="Сколько баллов добавить (целое положительное число)", reason="Краткая причина (текст) для логов")
    @app_commands.guilds(GUILD_ID)
    async def add(self, interaction: discord.Interaction, user: discord.User, amount: int, reason: str):
        if not await self._check_guild(interaction):
            return
        if not is_gm(interaction.user):
            await interaction.response.send_message('У Вас нет доступа к этой команде', ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message('Сумма должна быть положительным целым числом.', ephemeral=True)
            return
        if amount > 12:
            await interaction.response.send_message('Сумма должна быть в разумном диапазоне <=12', ephemeral=True)
            return

        # Ensure the user already exists in the DB; require /useradd otherwise
        row = await self.get_member_row(user.id)
        if row is None:
            await interaction.response.send_message(
                'Пользователь отсутствует в базе. Сначала используйте команду /useradd, чтобы создать запись.', ephemeral=True
            )
            return

        # reason — manually entered string
        try:
            res = await self.modify_score(user.id, delta=amount)
            self.bot.loop.create_task(self.upd_wall(interaction.guild))
            await interaction.response.send_message(
                f'Изменены баллы: {getattr(user, "display_name", getattr(user, "name", str(user)))}: {res["old_score"]} -> {res["new_score"]}',
                ephemeral=False
            )
            # logging the reason
            note = fame_note_embed(interaction.user, user, action=1, reason_text=reason, count=amount)
            try:
                log_ch = self.bot.get_channel(LOG_CHANNEL_ID)
                if log_ch:
                    await log_ch.send(embed=note)
            except Exception:
                logger.exception("Не удалось отправить лог-embed в канал логов")
        except Exception:
            logger.exception("Ошибка в команде add")
            await interaction.response.send_message('Произошла ошибка при добавлении баллов.', ephemeral=True)


    @app_commands.command(name='rem', description='Отнять указанное количество баллов у участника')
    @app_commands.rename(user="участник", amount="сумма", reason="причина")
    @app_commands.describe(user="Участник, у которого нужно отнять баллы", amount="Сколько баллов отнять (целое положительное число)", reason="Краткая причина (текст) для логов")
    @app_commands.guilds(GUILD_ID)
    async def rem(self, interaction: discord.Interaction, user: discord.User, amount: int, reason: str):
        if not await self._check_guild(interaction):
            return
        if not is_gm(interaction.user):
            await interaction.response.send_message('У Вас нет доступа к этой команде', ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message('Сумма должна быть положительным целым числом.', ephemeral=True)
            return
        if amount > 12:
            await interaction.response.send_message('Сумма должна быть в разумном диапазоне <=12', ephemeral=True)
            return

        # Require that the user exists in DB before removing points
        row = await self.get_member_row(user.id)
        if row is None:
            await interaction.response.send_message(
                'Пользователь отсутствует в базе. Сначала используйте команду /useradd, чтобы создать запись.', ephemeral=True
            )
            return

        try:
            res = await self.modify_score(user.id, delta=-amount)
            self.bot.loop.create_task(self.upd_wall(interaction.guild))  # updating wall
            await interaction.response.send_message(
                f'У {getattr(user, "display_name", getattr(user, "name", str(user)))} снято {amount} баллов: {res["old_score"]} -> {res["new_score"]}',
                ephemeral=False
            )
            note = fame_note_embed(interaction.user, user, action=2, reason_text=reason, count=amount)
            try:
                log_ch = self.bot.get_channel(LOG_CHANNEL_ID)
                if log_ch:
                    await log_ch.send(embed=note)
            except Exception:
                logger.exception("Не удалось отправить лог-embed в канал логов")
        except Exception:
            logger.exception("Ошибка в команде rem")
            await interaction.response.send_message('Произошла ошибка при снятии баллов.', ephemeral=True)


    # ---------------- other slash-commands ----------------
    @app_commands.command(name='useradd', description='Добавить участника в "Зал Славы" (создать запись)')
    @app_commands.guilds(GUILD_ID)
    async def add_member(self, interaction: discord.Interaction, user: discord.Member):
        # TODO: check if the added user is a bot. If so, respond with ephemeral error.
        if user.bot:
            await interaction.response.send_message('Вы не можете добавить бота в Зал Славы', ephemeral=True)
            return
        if not await self._check_guild(interaction):
            return
        if not is_gm(interaction.user):
            await interaction.response.send_message('У Вас нет доступа к этой команде', ephemeral=True)
            return
        try:
            await self.ensure_member(user.id)
            await interaction.response.send_message(f'{user.mention} добавлен в список', ephemeral=False)
        except Exception:
            logger.exception("Ошибка при добавлении участника")
            await interaction.response.send_message('Произошла ошибка при добавлении участника.', ephemeral=True)

    @app_commands.command(name='top', description='Топ зала славы')
    @app_commands.guilds(GUILD_ID)
    async def topscore(self, interaction: discord.Interaction):
        if not await self._check_guild(interaction):
            return
        embed = await self.create_top_embed(interaction.guild, limit=10, viewer=interaction.user)
        await interaction.response.send_message(embed=embed, view=None, ephemeral=True)

    @app_commands.command(name='score', description='Показывает баллы участника в зале славы')
    @app_commands.rename(user='озёрник')
    @app_commands.describe(user='Озёрник, баллы которого Вы хотите узнать')
    @app_commands.guilds(GUILD_ID)
    async def uscore(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
        if not await self._check_guild(interaction):
            return
        user = user or interaction.user
        row = await self.get_member_row(user.id)
        if row is None:
            await interaction.response.send_message('Участник не участвует в рейтинге :confused:', ephemeral=True)
            return
        season_score = int(row["score"] or 0)
        past = int(row["past_seasons"] or 0)
        embed = discord.Embed(title=f'Баллы активности {getattr(user, "display_name", str(user))}', color=discord.Color.random())
        if hasattr(user, "display_avatar"):
            embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name='Баллы в сезоне', value=str(season_score), inline=False)
        embed.add_field(name='Баллы в прошлых сезонах', value=str(past), inline=False)
        embed.add_field(name='Всего', value=str(season_score + past), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='report', description='Запросить таблицу Зала славы')
    @app_commands.guilds(GUILD_ID)
    async def fame_report(self, interaction: discord.Interaction):
        if not await self._check_guild(interaction):
            return
        if not is_gm(interaction.user):
            await interaction.response.send_message('У Вас нет доступа к этой команде', ephemeral=True)
            return
        try:
            path = await self.create_report_file(interaction.guild)
            await interaction.response.send_message(file=discord.File(path), ephemeral=False)
            try:
                os.remove(path)
            except Exception:
                pass
        except Exception:
            logger.exception("Ошибка при создании/отправке отчета")
            await interaction.response.send_message('Произошла ошибка при создании отчёта.', ephemeral=True)

    @app_commands.command(name='reset', description='Завершить сезон (немедленно)')
    @app_commands.guilds(GUILD_ID)
    async def reset_season(self, interaction: discord.Interaction):
        if not await self._check_guild(interaction):
            return
        if not is_gm(interaction.user):
            await interaction.response.send_message('У Вас нет доступа к этой команде', ephemeral=True)
            return
        try:
            report_path = await self.create_report_file(interaction.guild)
            await interaction.response.send_message('Выполняю сброс сезона — отчёт во вложении.', file=discord.File(report_path), ephemeral=False)
            try:
                os.remove(report_path)
            except Exception:
                logger.exception("Не удалось удалить временный файл отчёта после отправки")
            await self.inc_past_seasons_and_reset()
            guild = self.bot.get_guild(GUILD_ID) or interaction.guild
            if guild:
                await self.upd_wall(guild)
            log_ch = self.bot.get_channel(LOG_CHANNEL_ID)
            if log_ch:
                try:
                    await log_ch.send(f"Сезон немедленно сброшен модератором {interaction.user} в гильдии {GUILD_ID}")
                except Exception:
                    logger.exception("Не удалось отправить сообщение в лог-канал после сброса")
            logger.info("Season reset executed (past_seasons preserved)")
        except Exception:
            logger.exception("Ошибка при выполнении немедленного сброса сезона")
            try:
                await interaction.followup.send('Произошла ошибка при выполнении сброса сезона.', ephemeral=False)
            except Exception:
                pass

    async def upd_wall(self, guild: discord.Guild):
        """Update the pinned hall-of-fame wall message if configured."""
        try:
            settings = await self.get_glory_settings()
            if not settings:
                return
            channel_id = settings["glory_channel"]
            message_id = settings["glory_message"]
            if not channel_id or not message_id:
                return
            ch = self.bot.get_channel(channel_id)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(channel_id)
                except Exception:
                    return
            try:
                msg = await ch.fetch_message(message_id)
            except Exception:
                return
            new_embed = await self.create_top_embed(guild, viewer=None)
            await msg.edit(embed=new_embed)
        except Exception:
            logger.exception("Ошибка при обновлении стены лидеров")

    # ---------------- Context menu callback ----------------
    async def ctx_fame(self, interaction: discord.Interaction, user: discord.User):
        if not await self._check_guild(interaction):
            return
        row = await self.get_member_row(user.id)
        if row is None:
            await interaction.response.send_message('Участник не участвует в рейтинге :confused:', ephemeral=True)
            return
        season_score = int(row["score"] or 0)
        past = int(row["past_seasons"] or 0)
        embed = discord.Embed(title=f'Баллы активности {getattr(user, "display_name", str(user))}', color=discord.Color.random())
        if hasattr(user, "display_avatar"):
            embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name='Баллы в сезоне', value=str(season_score), inline=False)
        embed.add_field(name='Баллы в прошлых сезонах', value=str(past), inline=False)
        embed.add_field(name='Всего', value=str(season_score + past), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------- Setup: add cog + register ContextMenu on bot.tree ----------------
async def setup(bot: commands.Bot):
    cog = FameGroup(bot)
    await bot.add_cog(cog)

    try:
        bot.tree.remove_command('Зал славы', type=app_commands.CommandType.user)
    except Exception:
        pass

    menu = app_commands.ContextMenu(name='Зал славы', callback=cog.ctx_fame)
    bot.tree.add_command(menu)
