"""
Модуль кармы: Cog — события (сообщения, голос, вход на сервер), фоновые циклы, команды.

Остальное — во вспомогательных файлах рядом:
  _db.py        — база кармы: схемы таблиц, миграции, KarmaDatabase
  _state.py     — пути, настройки (data.json), база, тексты повышений
  _formulas.py  — уровни, ступени, Кубы, форматирование
  _cards.py     — картинки (карточки, открытки)
  _roles.py     — роли Сансары и Кубов
  _settings.py  — меню /settings_karma
  _boards.py    — таблицы лидеров Связи и их вкладки
"""

import asyncio # noqa
import inspect
import random
import traceback # noqa
import typing # noqa
import ast
import json
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from copy import deepcopy
from io import BytesIO
from itertools import combinations
from pathlib import Path # noqa
from typing import Any # noqa
from pprint import pformat # noqa

import discord # noqa
from discord import Interaction, app_commands, Role
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont, ImageOps # noqa
from PIL.ImageFont import FreeTypeFont
from discord.ui import Item, Button, View, LayoutView # noqa

from bot import OzernikiBot
from config import config
from utilities import *


# Части модуля. Вспомогательные файлы начинаются с «_»: bot.py грузит как расширение
# каждый .py в папке модуля, кроме таких (иначе потребовалась бы функция setup в каждом).
from . import _state
from ._db import *
from ._state import *
from ._formulas import *
from ._cards import *
from ._roles import *
from ._settings import *
from ._boards import *


# ---------------------------------------------------------
#  ||||||||||||||||||| Класс модуля |||||||||||||||||||||||
# ---------------------------------------------------------

class KarmaSistem(commands.Cog):

    # ---------- ИНИЦИАЛИЗАЦИЯ -----------

    def __init__(self, bot: OzernikiBot):
        self.bot = bot
        self.roles = Roles(bot)
        self.delays = set()

    def cog_load(self):
        # Тексты повышений из старого levels.json — в базу (один раз).
        import_legacy_level_texts(LEGACY_LEVELS_PATH)

        self.voice_karma_check.start()
        self.weekly_countdown.start()

        # Кнопки вкладок таблиц Связи работают на всех сообщениях, даже после перезапуска.
        self.bot.add_view(BindLeaderboardView(self))

    async def cog_unload(self):
        self.voice_karma_check.cancel()
        self.weekly_countdown.cancel()

    async def cog_command_error(self, ctx: commands.Context, error):
        self.bot.print_error(ctx.command, error)

        await ctx.send(
            f"Произошла ошибка:\n"
            f"```\n{error}\n```",
            ephemeral=True
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error):
        func_name = inspect.currentframe().f_code.co_name
        self.bot.print_error(func_name, error)

        if interaction.response.is_done():
            await interaction.followup.send(
                f"Произошла ошибка:\n"
                f"```\n{error}\n```",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Произошла ошибка:\n"
                f"```\n{error}\n```",
                ephemeral=True
            )

    # --------- ВСПОМОГАТЕЛЬНО -----------

    async def remove_delay(self, user_id: int, delay: int) -> None:
        await asyncio.sleep(delay)
        self.delays.discard(user_id)

    def check_delay(self, user_id: int) -> bool:
        if user_id in self.delays:
            return True
        # Кулдаун ставится сразу, чтобы два сообщения подряд не прошли оба.
        self.delays.add(user_id)
        asyncio.create_task(self.remove_delay(user_id, _state.data.karma_message_delay))
        return False

    # - ПРОВЕРКА БЛОКИРОВОК ВЫДАЧИ ОПЫТА -

    @staticmethod
    def check_blocked_channels(channel_id: int) -> bool:
        if channel_id in _state.data.blocked_channels_id:
            return True
        return False

    @staticmethod
    def check_blocked_roles(roles: list[discord.Role]) -> bool:
        blocked_roles_id = _state.data.blocked_roles_id
        for role in roles:
            if role.id in blocked_roles_id:
                return True
        return False

    @staticmethod
    def check_blocked_users(user_id: int) -> bool:
        if user_id in _state.data.blocked_users_id:
            return True
        return False

    # ------ ОБНОВЛЕНИЕ УЧАСТНИКОВ -------

    async def update_cube_roles(self, member: discord.Member) -> Role:
        ozernik = _state.db.get_user_by_discord_id(member.id)
        member_cube = get_cube_status(ozernik.id)

        member_cube_role = await self.roles.get_cube_role(
            member_cube['tag_name']
        )

        cube_roles = await self.roles.get_cube_roles()

        for role in member.roles:
            if role in cube_roles and role != member_cube_role:
                await member.remove_roles(role)

        await member.add_roles(member_cube_role)

        return member_cube_role

    async def update_sansara_roles(self, member: discord.Member) -> Role:
        ozernik = _state.db.get_user_by_discord_id(member.id)
        karma = _state.db.get_karma(ozernik.id)
        member_status = get_status(karma)

        member_sansara_role = await self.roles.get_sansara_role(
            member_status['tag_name']
        )

        sansara_roles = await self.roles.get_sansara_roles()

        for role in member.roles:
            if role in sansara_roles and role != member_sansara_role:
                await member.remove_roles(role)

        await member.add_roles(member_sansara_role)

        return member_sansara_role

    # ------------ СООБЩЕНИЯ -------------

    async def give_level_up_message(self, member: discord.Member, old_karma: int, new_karma: int, only_last: bool = False) -> None:
        """
        Поздравляет с новыми уровнями: по сообщению на каждый уровень.
        only_last=True — одно сообщение о последнем достигнутом уровне (для админских правок).
        """
        channel = self.bot.get_channel(_state.data.karma_channel_id if _state.data.karma_channel_id else 0)

        if not channel:
            return

        if new_karma <= old_karma:
            return

        old_level = get_level(old_karma)
        new_level = get_level(new_karma)

        levels = range(old_level + 1, new_level + 1)

        if only_last:
            levels = levels[-1:]

        # С какой кармы считается переход для каждого сообщения
        # (для only_last — с исходной, чтобы не потерять смену ступени).
        previous_karma = old_karma

        for level in levels:
            role = await self.update_sansara_roles(member)

            description = build_level_up_description(level, previous_karma)
            previous_karma = get_karma(level)

            embed = discord.Embed(
                title=f"**{member.display_name} повышает уровень!**",
                description=description,
                colour=role.colour
            )

            embed.set_thumbnail(url=member.display_avatar.url)

            embed.set_footer(text=role.guild.name, icon_url=role.guild.icon.url if role.guild.icon else None)

            await channel.send(
                member.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                ),
            )

            await asyncio.sleep(0.2)

    async def give_bind_up_message(self, member_1: discord.Member, member_2: discord.Member, old_karma: int, new_karma: int) -> None:
        channel = self.bot.get_channel(_state.data.karma_channel_id if _state.data.karma_channel_id else 0)

        if not channel:
            print('no karma channel')
            return

        if new_karma <= old_karma:
            return

        old_cube = get_cube(old_karma)
        new_cube = get_cube(new_karma)

        print(new_cube['name'], old_cube['name'])

        if new_cube['name'] != old_cube['name']:
            ozernik_1 = _state.db.get_user_by_discord_id(member_1.id)
            ozernik_2 = _state.db.get_user_by_discord_id(member_2.id)

            bind = _state.db.get_karmic_bind(ozernik_1.id, ozernik_2.id)

            avatar_1 = await get_discord_avatar(member_1)
            avatar_2 = await get_discord_avatar(member_2)

            print(avatar_1.size)
            print(avatar_2.size)

            # Рисование — в отдельном потоке, чтобы не останавливать бота.
            postcard_bytes = await asyncio.to_thread(
                create_bind_up_postcard,
                bind=bind,
                avatar_1_image=avatar_1,
                avatar_2_image=avatar_2,
            )

            file = discord.File(postcard_bytes, filename=f"{member_1.name}_{member_2.name}_postcard.png")

            await channel.send(
                f"{member_1.mention} и {member_2.mention} получили воспоминание!",
                file=file,
            )
            await asyncio.sleep(0.2)

    async def give_cube_up_message(self, member: discord.Member, old_cube: dict, new_cube: dict) -> None:
        try:
            channel = self.bot.get_channel(
                _state.data.karma_channel_id if _state.data.karma_channel_id else 0
            )

            if not channel:
                return

            if old_cube == new_cube:
                return

            role = await self.update_cube_roles(member)
            cube_name = get_cube_status(_state.db.get_user_by_discord_id(member.id).id)['name']

            title = f"**{member.display_name} получает новый Куб!**"
            description = ''

            match cube_name:
                case 'Черный Куб':
                    description = ("**«Столкнись со своими демонами»\n"
                                   "Вы достигли 1 Черной связи.\n"
                                   f"Теперь у вас есть {role.mention}**")

                case 'Белый Куб':
                    description = ("**«Переживи свою прошлую жизнь вновь»\n"
                                   "Вы достигли 10 Белых связей.\n"
                                   f"Теперь у вас есть {role.mention}**")

                case 'Синий Куб':
                    description = ("**«Прошлое никогда не умирает, оно даже не прошлое»\n"
                                   "Вы достигли 10 Синих связей.\n"
                                   f"Теперь у вас есть {role.mention}**")

                case 'Золотой Куб':
                    description = ("**«Воспоминания — это не только ключ к прошлому, но и к будущему»\n"
                                   "Вы достигли 10 Золотых связей.\n"
                                   f"Теперь у вас есть {role.mention}**")

            embed = discord.Embed(
                title=title,
                description=description,
                colour=role.colour
            )

            embed.set_thumbnail(url=member.display_avatar.url)

            embed.set_footer(text=role.guild.name, icon_url=role.guild.icon.url if role.guild.icon else None)

            await channel.send(
                member.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                ),
            )
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")

    # ------------- СОБЫТИЯ --------------

    async def give_join_roles(self, member: discord.Member) -> None:
        """
        Роли для зашедшего на сервер: ступень Сансары по карме (новичку — Нарака)
        и Куб, если связи уже есть (вернувшийся участник). Ботам — ничего.

        Раньше роль выдавалась только при повышении уровня, и новички
        до 1 уровня ходили без роли.
        """
        if member.bot or member.guild != self.bot.guild:
            return

        try:
            ozernik = self.bot.db_ensure_user(member)

            await self.update_sansara_roles(member)

            if get_cube_status(ozernik.id) is not None:
                await self.update_cube_roles(member)
        except Exception as error:
            self.bot.print_error("give_join_roles", error)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # С проверкой участника (правила сервера) — ждём, пока её пройдут:
        # роль могла бы открыть каналы в обход проверки. См. on_member_update.
        if getattr(member, 'pending', False):
            return

        await self.give_join_roles(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # Прошёл проверку участника — теперь можно выдать роли.
        if getattr(before, 'pending', False) and not getattr(after, 'pending', False):
            await self.give_join_roles(after)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return

        if message.author.bot: # Боты и вебхуки карму не получают
            return

        member = self.bot.guild.get_member(message.author.id)

        if member is None:
            return

        ozernik = self.bot.db_ensure_user(member)

        # Сокращения команд (,rs, ,lb, ...) карму не дают.
        if await self.handle_shortcut(message, member):
            return

        if self.check_blocked_channels(message.channel.id): # Проверка заблокирован ли канал
            return

        # Ветка в заблокированном канале тоже заблокирована (у ветки свой ID).
        parent_id = getattr(message.channel, 'parent_id', None)
        if parent_id and self.check_blocked_channels(parent_id):
            return

        if not self.can_get_karma(member):
            return

        # Кулдаун проверяется последним, чтобы сообщения без кармы его не запускали.
        if self.check_delay(ozernik.id):
            return

        old_karma, new_karma = _state.db.add_karma(ozernik.id, 1, True)

        await self.give_level_up_message(member, old_karma.karma, new_karma.karma)

    def can_get_karma(self, member: discord.Member) -> bool:
        """Бот, заблокированный пользователь или роль — карма не начисляется."""
        if member.bot:
            return False

        if self.check_blocked_users(member.id):
            return False

        if self.check_blocked_roles(member.roles):
            return False

        return True

    def get_voice_karma_members(self, channel: discord.VoiceChannel) -> list[discord.Member]:
        """Участники канала, которые получают голосовую карму."""
        if channel == self.bot.guild.afk_channel:
            return []

        members = []

        for member in channel.members:
            if not self.can_get_karma(member):
                continue

            # Заглушённые (сами или сервером) не участвуют в разговоре.
            if member.voice and (member.voice.self_deaf or member.voice.deaf):
                continue

            members.append(member)

        return members

    @tasks.loop(minutes=1)
    async def voice_karma_check(self):
        if not self.bot.ready:
            return

        for channel in self.bot.guild.voice_channels:
            # Ошибка в одном канале не должна срывать начисление в остальных:
            # раньше весь проход был в одном try, и первая же ошибка выбрасывала из цикла.
            try:
                await self.process_voice_channel(channel)
            except Exception as error:
                self.bot.print_error(f"voice_karma_check [{channel.name}]", error)

    async def process_voice_channel(self, channel: discord.VoiceChannel) -> None:
        """Начисляет +1 Связи каждой паре в канале и присылает открытки/новые Кубы."""
        # Основной аккаунт и его твинк — один озерник: оставляем одного,
        # иначе получилась бы «связь с самим собой» (ValueError в базе).
        members = self.get_voice_karma_members(channel)

        if len(members) < 2:
            return

        entries: dict[int, tuple[discord.Member, DataTypes.Ozernik]] = {}

        for member in members:
            ozernik = self.bot.db_ensure_user(member)
            entries.setdefault(ozernik.id, (member, ozernik))

        if len(entries) < 2:
            return

        # Куб каждого участника считается один раз до и один раз после,
        # а все пары записываются в базу одной транзакцией. Раньше на
        # каждую пару было по два коммита и четыре пересчёта Кубов —
        # при 15 людях в войсе бот стоял ~3 секунды каждую минуту.
        old_cubes = {ozernik.id: get_cube_status(ozernik.id) for _, ozernik in entries.values()}

        pairs = list(combinations(entries.values(), 2))
        binds = _state.db.add_bind_karma_many(
            [(ozernik_1.id, ozernik_2.id) for (_, ozernik_1), (_, ozernik_2) in pairs],
            karma=1,
        )

        for ((member_1, _), (member_2, _)), bind in zip(pairs, binds):
            await self.give_bind_up_message(member_1, member_2, bind.bind_karma - 1, bind.bind_karma)

        for member, ozernik in entries.values():
            await self.give_cube_up_message(member, old_cubes[ozernik.id], get_cube_status(ozernik.id))

    @voice_karma_check.error
    async def voice_karma_check_error(self, error):
        self.bot.print_error("voice_karma_check", error)

    def get_week_start(self) -> str:
        """Дата понедельника текущей недели по Москве, например '2026-09-28'."""
        now = datetime.now(timezone.utc).astimezone(self.bot.moscow_tz)
        return (now - timedelta(days=now.weekday())).date().isoformat()

    @tasks.loop(minutes=5)
    async def weekly_countdown(self):
        """
        Раз в 5 минут сверяет неделю с датой последнего сброса.

        Дата хранится в data.json, поэтому сброс не теряется,
        если бот был выключен в момент смены недели.
        """
        try:
            week_start = self.get_week_start()

            if _state.data.weekly_reset_week is None:
                # Первый запуск: не сбрасываем, просто запоминаем текущую неделю.
                _state.data.weekly_reset_week = week_start
                return

            if _state.data.weekly_reset_week == week_start:
                return

            await self.announce_weekly_winners()

            _state.db.reset_weekly_karma()
            _state.data.weekly_reset_week = week_start

        except Exception as error:
            self.bot.print_error("weekly_countdown", error)

    @weekly_countdown.before_loop
    async def before_weekly_countdown(self):
        await self.bot.wait_until_ready()

    async def announce_weekly_winners(self) -> None:
        leaderboard = [
            (ozernik, karma)
            for ozernik, karma in _state.db.get_top_weekly_karma()[:3]
            if karma.weekly_karma > 0
        ]

        if not leaderboard:
            return

        channel = self.bot.get_channel(_state.data.karma_channel_id if _state.data.karma_channel_id else 0)

        if not channel:
            return

        lines = [
            f'## {n}. <@{ozernik.discord_id}> — `{karma.weekly_karma}` к.'
            for n, (ozernik, karma) in enumerate(leaderboard, 1)
        ]

        view = discord.ui.LayoutView()

        container = discord.ui.Container()

        container.add_item(discord.ui.TextDisplay(
            '# Победители Недели!\n' + '\n'.join(lines)
        ))

        view.add_item(container)

        try:
            await channel.send(view=view)
        except discord.HTTPException as error:
            # Не удалось отправить — всё равно сбрасываем, иначе неделя зависнет.
            self.bot.print_error("announce_weekly_winners", error)


    # ------------- КОМАНДЫ --------------

    @app_commands.command(name='settings_karma', description='Настройка системы Кармы.')
    @app_commands.guilds(config.GUILD_ID)
    async def settings_karma(self, interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Только для Администраторов.",
                ephemeral=True
            )
            return

        navigator = Navigator(SettingsMainPage, author=interaction.user, bot=self.bot)

        await navigator.send(interaction)

    # ---- КАРТОЧКИ И ТАБЛИЦЫ ЛИДЕРОВ ----
    # Общие для slash-команд и сокращений (,rs, ,lb и т.д.).

    async def build_rank_sansara_file(self, member: discord.Member) -> discord.File:
        ozernik = self.bot.db_ensure_user(member)
        avatar = await get_discord_avatar(member)

        stats = get_card_stats(ozernik.id)

        # Рисование — в отдельном потоке, чтобы не останавливать бота.
        rank_card_bytes = await asyncio.to_thread(create_rank_card, ozernik, avatar, member.display_name, stats)

        return discord.File(rank_card_bytes, filename=f"{member.name}_sansara_rank.png")

    async def build_rank_cube_file(self, member: discord.Member) -> discord.File:
        ozernik = self.bot.db_ensure_user(member)
        avatar = await get_discord_avatar(member)
        full_binds = await get_full_binds(member, self.bot)

        stats = get_card_stats(ozernik.id)

        # Рисование — в отдельном потоке, чтобы не останавливать бота.
        rank_card_bytes = await asyncio.to_thread(
            create_cube_cart,
            ozernik,
            avatar,
            member.display_name,
            full_binds,
            stats,
        )

        return discord.File(rank_card_bytes, filename=f"{member.name}_cube_rank.png")

    @staticmethod
    def build_leaderboard_embed(
            title: str,
            subtitle: str,
            rows: list[tuple[str, str]],
            viewer: discord.Member,
            guild: discord.Guild,
    ) -> discord.Embed:
        """
        rows — (заголовок места, строки под ним) для первых 10 мест. Заголовок — упоминание(я).
        Строки под заголовком начинаются с отступа-невидимки «ㅤ», места разделены пустой строкой.
        """
        embed = discord.Embed(
            title=title,
            colour=viewer.top_role.colour,
        )

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        lines = [subtitle]

        for n, (header, body) in enumerate(rows[:10], 1):
            lines.append('')
            lines.append(f'**#{n} <:cigar:1208007437639225415> {header}**\n{body}')

        embed.description = '\n'.join(lines).replace('#1 ', '🥇 ').replace('#2 ', '🥈 ').replace('#3 ', '🥉 ')

        return embed

    def build_leaderboard(self, viewer: discord.Member, guild: discord.Guild) -> discord.Embed | None:
        leaderboard = _state.db.get_top_karma()
        if not leaderboard:
            return None

        rows = []
        for ozernik, karma in leaderboard[:10]:
            level = get_level(karma.karma)
            rows.append((
                f'<@{ozernik.discord_id}>',
                f'ㅤ  Уровень: `{level}`\n'
                f'ㅤ  Карма: `{karma.karma}/{get_karma(level + 1)}`'
            ))

        return self.build_leaderboard_embed('Таблица лидеров', '*Ментальное здоровье и рыбалка.*', rows, viewer, guild)

    def build_leaderboard_weekly(self, viewer: discord.Member, guild: discord.Guild) -> discord.Embed | None:
        leaderboard = _state.db.get_top_weekly_karma()
        if not leaderboard:
            return None

        rows = []
        for ozernik, karma in leaderboard[:10]:
            level = get_level(karma.karma)
            rows.append((
                f'<@{ozernik.discord_id}>',
                f'ㅤ  Уровень: `{level}`\n'
                f'ㅤ  Карма за неделю: `{karma.weekly_karma}`'
            ))

        return self.build_leaderboard_embed(
            'Таблица лидеров',
            '*Место для опустошения разума, \nразмышлений о прошлом и будущем.*',
            rows, viewer, guild,
        )

    def build_bind_pairs(self, viewer: discord.Member, guild: discord.Guild) -> discord.Embed | None:
        leaderboard = get_pairs_leaderboard()
        if not leaderboard:
            return None

        rows = [
            (
                f'<@{row["first"].discord_id}> + <@{row["second"].discord_id}>',
                f'ㅤ  Связь: {row["level_emoji"]} {row["level"]}\n'
                f'ㅤ  Вместе: `{format_hours(row["bind"].bind_karma)}`',
            )
            for row in leaderboard[:10]
        ]

        return self.build_leaderboard_embed(
            'Пары',
            '*Ты должен собрать воспоминания. Они нужны нам для нашего будущего, для полноценного эликсира.*',
            rows, viewer, guild,
        )

    def build_bind_together(self, viewer: discord.Member, guild: discord.Guild) -> discord.Embed | None:
        leaderboard = get_together_leaderboard()
        if not leaderboard:
            return None

        rows = [
            (
                f'<@{row["ozernik"].discord_id}>',
                f'ㅤ  Время: `{format_hours(row["total_bind_karma"])}`\n'
                f'ㅤ  Связей: `{row["binds"]}`',
            )
            for row in leaderboard[:10]
        ]

        return self.build_leaderboard_embed(
            'Время',
            '*Балансируй субстанцию своих прошлых жизней.*',
            rows, viewer, guild,
        )

    def build_bind_colored(self, viewer: discord.Member, guild: discord.Guild) -> discord.Embed | None:
        leaderboard = get_colored_leaderboard()
        if not leaderboard:
            return None

        rows = [
            (
                f'<@{row["ozernik"].discord_id}>',
                # Кружки отдельной строкой: на телефоне со шрифтом 100% «Связи: 🟡 · 🔵 · ⚪» в одну
                # строку не помещалась, хвост переносился без отступа и места сливались.
                f'ㅤ  Связи:\n'
                f'ㅤ  🟡 `{row["gold"]}` · 🔵 `{row["blue"]}` · ⚪ `{row["white"]}`',
            )
            for row in leaderboard[:10]
        ]

        return self.build_leaderboard_embed(
            'Кубы',
            '*Я вижу свои воспоминания... Пойманные в маленькие кубики...*',
            rows, viewer, guild,
        )

    # Вкладки ,lbc: ключ → (надпись кнопки, сборщик, текст пустой вкладки).
    BIND_TABS = {
        'pairs': ('Пары', 'build_bind_pairs', 'Пар пока нет.'),
        'together': ('Время', 'build_bind_together', 'Связей пока нет.'),
        'colored': ('Кубы', 'build_bind_colored', 'Цветных связей пока ни у кого нет.'),
    }

    def build_bind_tab(self, tab: str, viewer: discord.Member, guild: discord.Guild) -> discord.Embed:
        label, builder, empty_text = self.BIND_TABS[tab]
        embed = getattr(self, builder)(viewer, guild)

        if embed is None:
            embed = discord.Embed(title=label, description=empty_text, colour=viewer.top_role.colour)

        return embed

    # Таблицы лидеров: (сборщик, ответ при пустой таблице).
    LEADERBOARDS = {
        'leaderboard': ('build_leaderboard', 'Нет таблицы лидеров.'),
        'leaderboard_weekly': ('build_leaderboard_weekly', 'Нет недельной таблицы лидеров.'),
    }

    BIND_EMPTY_TEXT = 'Нет таблиц лидеров Связи.'

    async def send_leaderboard(self, interaction: Interaction, name: str) -> None:
        builder, empty_text = self.LEADERBOARDS[name]
        embed = getattr(self, builder)(interaction.user, interaction.guild)

        if embed is None:
            await interaction.response.send_message(empty_text, ephemeral=True)
            return

        await interaction.response.send_message(embed=embed)

    # ------------ СОКРАЩЕНИЯ ------------

    # Первое слово сообщения → команда. ,r оставлен как старое сокращение ,rs.
    SHORTCUTS = {
        ',rs': 'rank_sansara',
        ',r': 'rank_sansara',
        ',rc': 'rank_cube',
        ',lb': 'leaderboard',
        ',lbw': 'leaderboard_weekly',
        ',lbc': 'leaderboard_cubes',
    }

    async def handle_shortcut(self, message: discord.Message, member: discord.Member) -> bool:
        """
        Выполняет сокращение, если сообщение с него начинается (отдельным словом).
        Для карточек можно упомянуть другого участника: ,rs @участник.

        :return: True, если сообщение было сокращением.
        """
        words = message.content.split()

        if not words:
            return False

        name = self.SHORTCUTS.get(words[0].lower())

        if name is None:
            return False

        no_ping = discord.AllowedMentions(replied_user=False)

        if name in ('rank_sansara', 'rank_cube'):
            # Первый упомянутый участник сервера, иначе автор.
            mentioned = (message.guild.get_member(user.id) for user in getattr(message, 'mentions', []))
            target = next((user for user in mentioned if user is not None), member)

            if name == 'rank_sansara':
                file = await self.build_rank_sansara_file(target)
            else:
                file = await self.build_rank_cube_file(target)

            await message.reply(file=file, allowed_mentions=no_ping)
            return True

        if name == 'leaderboard_cubes':
            if not _state.db.get_top_karmic_binds():
                await message.reply(self.BIND_EMPTY_TEXT, allowed_mentions=no_ping)
                return True

            await message.reply(
                embed=self.build_bind_tab('pairs', member, message.guild),
                view=BindLeaderboardView(self),
                allowed_mentions=no_ping,
            )
            return True

        builder, empty_text = self.LEADERBOARDS[name]
        embed = getattr(self, builder)(member, message.guild)

        if embed is None:
            await message.reply(empty_text, allowed_mentions=no_ping)
        else:
            await message.reply(embed=embed, allowed_mentions=no_ping)

        return True

    # ------ КОМАНДЫ УЧАСТНИКОВ ------

    @app_commands.command(name='rank_sansara', description='Просмотр ранга Сансары. Сокращение: ,rs')
    @app_commands.guilds(config.GUILD_ID)
    async def rank_sansara(self, interaction: Interaction, user: discord.Member = None):
        file = await self.build_rank_sansara_file(user or interaction.user)

        await interaction.response.send_message(file=file)

    @app_commands.command(name='rank_cube', description='Просмотр ранга Куба. Сокращение: ,rc')
    @app_commands.guilds(config.GUILD_ID)
    async def rank_cube(self, interaction: Interaction, user: discord.Member = None):
        await interaction.response.defer()

        file = await self.build_rank_cube_file(user or interaction.user)

        await interaction.followup.send(file=file)

    @app_commands.command(name='leaderboard', description='Таблица лидеров по карме. Сокращение: ,lb')
    @app_commands.guilds(config.GUILD_ID)
    async def leaderboard(self, interaction: Interaction):
        await self.send_leaderboard(interaction, 'leaderboard')

    @app_commands.command(name='leaderboard_weekly', description='Недельная таблица лидеров по карме. Сокращение: ,lbw')
    @app_commands.guilds(config.GUILD_ID)
    async def leaderboard_weekly(self, interaction: Interaction):
        await self.send_leaderboard(interaction, 'leaderboard_weekly')

    @app_commands.command(name='leaderboard_cubes', description='Таблицы Связи: пары, со всеми, цветные. Сокращение: ,lbc')
    @app_commands.guilds(config.GUILD_ID)
    async def leaderboard_cubes(self, interaction: Interaction):
        if not _state.db.get_top_karmic_binds():
            await interaction.response.send_message(self.BIND_EMPTY_TEXT, ephemeral=True)
            return

        await interaction.response.send_message(
            embed=self.build_bind_tab('pairs', interaction.user, interaction.guild),
            view=BindLeaderboardView(self),
        )

    # ------ АДМИНИСТРИРОВАНИЕ КАРМЫ ------

    @staticmethod
    async def check_admin(interaction: Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True

        await interaction.response.send_message(
            "Только для Администраторов.",
            ephemeral=True
        )
        return False

    async def send_log(self, text: str) -> None:
        """Пишет в канал логов из настроек кармы, если он задан."""
        channel = self.bot.get_channel(_state.data.log_channel_id if _state.data.log_channel_id else 0)

        if not channel:
            return

        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as error:
            self.bot.print_error("send_log", error)

    async def finish_admin_edit(
            self,
            interaction: Interaction,
            member: discord.Member,
            text: str,
            old: DataTypes.Karma | None = None,
            new: DataTypes.Karma | None = None,
    ) -> None:
        """
        Пересчитывает роль Сансары, отвечает админу и пишет лог.
        Если уровень вырос — одно поздравление о новом уровне (без промежуточных).
        """
        if not member.bot:
            try:
                await self.update_sansara_roles(member)

                if old is not None and new is not None:
                    await self.give_level_up_message(member, old.karma, new.karma, only_last=True)
            except Exception as error:
                self.bot.print_error("finish_admin_edit", error)
                text += '\n-# Не удалось обновить роль Сансары, см. консоль.'

        await interaction.followup.send(text, ephemeral=True)
        await self.send_log(f'{interaction.user.mention} → {text}')

    @staticmethod
    def drop_status_if_not_human(karma: DataTypes.Karma) -> tuple[DataTypes.Karma, str]:
        """
        Асур и Дэва требуют достигнутого Человека: если карма упала ниже порога,
        статус снимается. Возвращает обновлённую карму и пометку для ответа админу.
        """
        human_karma = _state.data.karma_roles['human']['required_karma']

        if karma.status is None or karma.karma >= human_karma:
            return karma, ''

        status_name = _state.data.karma_roles[karma.status]['name']
        karma = _state.db.set_karma_status(karma.user_id, None)

        return karma, f'\nСтатус `{status_name}` снят: карма ниже Человека (`{human_karma}`).'

    @staticmethod
    def karma_change_text(action: str, member: discord.Member, old: DataTypes.Karma, new: DataTypes.Karma) -> str:
        return (
            f'**{action}:** {member.mention}\n'
            f'Карма: `{old.karma}` → `{new.karma}` (уровень {get_level(old.karma)} → {get_level(new.karma)})'
        )

    @app_commands.command(name='karma_add', description='Добавить карму участнику. Недельная карма не меняется.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(user='Участник', amount='Сколько кармы добавить')
    async def karma_add(self, interaction: Interaction, user: discord.Member, amount: app_commands.Range[int, 1, 1_000_000]):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        ozernik = self.bot.db_ensure_user(user)
        # Недельная карма не меняется: недельный топ — только за собственную активность.
        old, new = _state.db.add_karma(ozernik.id, amount)

        await self.finish_admin_edit(interaction, user, self.karma_change_text('Карма добавлена', user, old, new), old, new)

    @app_commands.command(name='karma_remove', description='Отнять карму у участника. Ниже нуля не опускается, недельная не меняется.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(user='Участник', amount='Сколько кармы отнять')
    async def karma_remove(self, interaction: Interaction, user: discord.Member, amount: app_commands.Range[int, 1, 1_000_000]):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        ozernik = self.bot.db_ensure_user(user)
        old = _state.db.get_karma(ozernik.id)
        new = _state.db.remove_karma(ozernik.id, amount)
        new, status_note = self.drop_status_if_not_human(new)

        text = self.karma_change_text('Карма отнята', user, old, new) + status_note
        await self.finish_admin_edit(interaction, user, text, old, new)

    @app_commands.command(name='karma_set', description='Установить карму участнику. 0 — обнулить. Недельная карма не меняется.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(user='Участник', amount='Новое значение кармы')
    async def karma_set(self, interaction: Interaction, user: discord.Member, amount: app_commands.Range[int, 0, 1_000_000]):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        ozernik = self.bot.db_ensure_user(user)
        old = _state.db.get_karma(ozernik.id)
        new = _state.db.set_karma(ozernik.id, amount)
        new, status_note = self.drop_status_if_not_human(new)

        action = 'Карма обнулена' if amount == 0 else 'Карма установлена'
        text = self.karma_change_text(action, user, old, new) + status_note
        await self.finish_admin_edit(interaction, user, text, old, new)

    @app_commands.command(name='karma_status', description='Выдать или снять статус Асура/Дэвы (нужен достигнутый Человек).')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(user='Участник', status='Статус')
    @app_commands.choices(status=[
        app_commands.Choice(name='Асур', value='asur'),
        app_commands.Choice(name='Дэва', value='deva'),
        app_commands.Choice(name='Снять статус', value='none'),
    ])
    async def karma_status(self, interaction: Interaction, user: discord.Member, status: app_commands.Choice[str]):
        if not await self.check_admin(interaction):
            return

        new_status = None if status.value == 'none' else status.value

        ozernik = self.bot.db_ensure_user(user)
        old = _state.db.get_karma(ozernik.id)

        if old.status == new_status:
            await interaction.response.send_message(
                f'У {user.mention} уже статус `{get_status(old)["name"]}`.',
                ephemeral=True
            )
            return

        human_karma = _state.data.karma_roles['human']['required_karma']

        if new_status is not None and old.karma < human_karma:
            await interaction.response.send_message(
                f'Статус можно выдать только достигшему Человека (`{human_karma}` кармы). '
                f'У {user.mention} `{old.karma}`.',
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        new = _state.db.set_karma_status(ozernik.id, new_status)

        await self.finish_admin_edit(
            interaction,
            user,
            f'**Статус изменён:** {user.mention}\n'
            f'`{get_status(old)["name"]}` → `{get_status(new)["name"]}`'
        )

async def setup(bot):
    await bot.add_cog(KarmaSistem(bot))
    pass
