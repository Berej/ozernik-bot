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
  _twinks.py    — твинки: подтверждение привязки в ЛС
  _card_pages.py — кнопки страниц карточек (,rs и ,rc)
"""

import asyncio
import inspect
import traceback
from datetime import datetime, timedelta, timezone
from itertools import combinations
from types import SimpleNamespace

import discord
from discord import Interaction, app_commands, Role
from discord.ext import commands, tasks

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
from ._twinks import *
from ._card_pages import *


# ---------------------------------------------------------
#  ||||||||||||||||||| Класс модуля |||||||||||||||||||||||
# ---------------------------------------------------------

class KarmaSistem(commands.Cog):

    # ---------- ИНИЦИАЛИЗАЦИЯ -----------

    def __init__(self, bot: OzernikiBot):
        self.bot = bot
        self.roles = Roles(bot)
        self.delays = set()

        # Журнал активности копится здесь и пишется в базу раз в минуту одной пачкой
        # (flush_activity в голосовом цикле): запись на каждое сообщение — ~15 мс простоя бота.
        # (ID пользователя, начало часа, вид) → сколько.
        self.activity: dict[tuple[int, int, str], int] = {}

    def cog_load(self):
        # Тексты повышений из старого levels.json — в базу (один раз).
        import_legacy_level_texts(LEGACY_LEVELS_PATH)

        self.voice_karma_check.start()
        self.weekly_countdown.start()

        # Кнопки вкладок таблиц Связи работают на всех сообщениях, даже после перезапуска.
        self.bot.add_view(BindLeaderboardView(self))

        # Кнопки страниц карточек — тоже (чья карточка и какая страница — в custom_id).
        self.bot.add_dynamic_items(CardPageButton)

    async def cog_unload(self):
        self.voice_karma_check.cancel()
        self.weekly_countdown.cancel()
        self.bot.remove_dynamic_items(CardPageButton)

        # Не потерять последнюю минуту активности при выгрузке модуля.
        self.flush_activity()

    # ---------- ЖУРНАЛ АКТИВНОСТИ -----------

    def note_activity(self, user_id: int, kind: str, amount: int = 1) -> None:
        """Запомнить активность (в базу уйдёт при следующем flush_activity)."""
        key = (user_id, current_hour(), kind)
        self.activity[key] = self.activity.get(key, 0) + amount

    def flush_activity(self) -> None:
        """Записать накопленную активность в общую базу одной транзакцией."""
        if not self.activity:
            return

        # Сначала забираем накопленное: если запись упадёт — вернём, и она повторится через минуту.
        pending, self.activity = self.activity, {}

        try:
            _state.db.add_activity_many([(user_id, hour, kind, amount) for (user_id, hour, kind), amount in pending.items()])
        except Exception as error:
            self.bot.print_error("flush_activity", error)
            for key, amount in pending.items():
                self.activity[key] = self.activity.get(key, 0) + amount

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

    def accounts_in_guild(self, member: discord.Member, ozernik: DataTypes.Ozernik) -> list[discord.Member]:
        """
        Все аккаунты человека на сервере: основной и твинки. Роли у них одинаковые
        (решение Alium) — поэтому любой пересчёт ролей применяется ко всем сразу.
        """
        accounts = [] if member.bot else [member]

        for discord_id in _state.db.get_discord_ids(ozernik.id):
            account = member.guild.get_member(discord_id) if member.guild else None

            if account is not None and not account.bot and account not in accounts:
                accounts.append(account)

        return accounts

    @staticmethod
    async def set_exclusive_role(account: discord.Member, role: Role | None, family: list[Role]) -> None:
        """Оставляет у аккаунта из семейства ролей (Сансара или Кубы) ровно role (или ни одной, если None)."""
        extra = [current for current in account.roles if current in family and current != role]

        if extra:
            await account.remove_roles(*extra)

        if role is not None and role not in account.roles:
            await account.add_roles(role)

    async def update_cube_roles(self, member: discord.Member) -> Role | None:
        ozernik = _state.db.get_user_by_discord_id(member.id)
        member_cube = get_cube_status(ozernik.id)

        # Куба может не быть (например, у только что отвязанного твинка) — тогда роли Кубов снимаются.
        member_cube_role = await self.roles.get_cube_role(member_cube['tag_name']) if member_cube else None
        cube_roles = await self.roles.get_cube_roles() or []

        for account in self.accounts_in_guild(member, ozernik):
            await self.set_exclusive_role(account, member_cube_role, cube_roles)

        return member_cube_role

    async def update_sansara_roles(self, member: discord.Member) -> Role:
        ozernik = _state.db.get_user_by_discord_id(member.id)
        karma = _state.db.get_karma(ozernik.id)
        member_status = get_status(karma)

        member_sansara_role = await self.roles.get_sansara_role(
            member_status['tag_name']
        )

        sansara_roles = await self.roles.get_sansara_roles() or []

        for account in self.accounts_in_guild(member, ozernik):
            await self.set_exclusive_role(account, member_sansara_role, sansara_roles)

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

    async def give_status_message(self, member: discord.Member, status: str) -> None:
        """
        Поздравление с особым статусом Сансары (/karma_status) — оформлено как повышение уровня:
        «Имя возвышается!», текст статуса, «Теперь вы @Статус». Для статуса без текста — молча.
        """
        description = build_status_up_description(status)
        channel = self.bot.get_channel(_state.data.karma_channel_id if _state.data.karma_channel_id else 0)

        if description is None or not channel or member.bot:
            return

        role = await self.update_sansara_roles(member)

        embed = discord.Embed(
            title=f"**{member.display_name} возвышается!**",
            description=description,
            colour=role.colour if role else discord.Colour.default(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=member.guild.name, icon_url=member.guild.icon.url if member.guild.icon else None)

        await channel.send(
            member.mention,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False),
        )

    async def get_user_anywhere(self, discord_id: int) -> discord.abc.User:
        """Участник сервера, а если ушёл — пользователь Discord (запросом)."""
        return self.bot.guild.get_member(discord_id) or await self.bot.fetch_user(discord_id)

    async def give_guise_message(self, main_id: int, twink_id: int) -> None:
        """
        Объявление о привязке твинка в канале кармы (решение Alium: при любой привязке, и админской):
        «@основной приобретает себе новый облик в лице @твинк» + открытка с аватарами друг напротив друга.
        """
        channel = self.bot.get_channel(_state.data.karma_channel_id if _state.data.karma_channel_id else 0)

        if not channel:
            return

        file = None

        # Открытка — по возможности: если аватар не скачать, объявление всё равно выйдет.
        try:
            main = await self.get_user_anywhere(main_id)
            twink = await self.get_user_anywhere(twink_id)
            ozernik = _state.db.get_user_by_discord_id(main_id)
            stage = get_status(_state.db.get_karma(ozernik.id))['tag_name']

            main_avatar = await get_discord_avatar(main)
            twink_avatar = await get_discord_avatar(twink)

            # Рисование — в отдельном потоке, чтобы не останавливать бота.
            postcard = await asyncio.to_thread(create_guise_postcard, main_avatar, twink_avatar, stage)
            file = discord.File(postcard, filename=f"{main.name}_{twink.name}_guise.png")
        except Exception as error:
            self.bot.print_error("give_guise_message", error)

        try:
            await channel.send(
                f'<@{main_id}> приобретает себе новый облик в лице <@{twink_id}>',
                **({'file': file} if file else {}),
            )
        except discord.HTTPException as error:
            self.bot.print_error("give_guise_message", error)

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

        # Активность — каждое сообщение, даже без кармы из-за задержки (решение Alium).
        self.note_activity(ozernik.id, 'messages')

        # Кулдаун проверяется последним, чтобы сообщения без кармы его не запускали.
        if self.check_delay(ozernik.id):
            return

        # В статистику — канал сообщения; ветка засчитывается своему каналу (решение Alium).
        old_karma, new_karma = _state.db.add_karma(ozernik.id, 1, True, channel_id=parent_id or message.channel.id)

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

        # Раз в минуту — накопленная активность (сообщения и голос) в базу.
        self.flush_activity()

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

        # Минута в голосе — на условиях Связи (не один, не заглушён, не AFK; решение Alium).
        for _, ozernik in entries.values():
            self.note_activity(ozernik.id, 'voice_minutes')

        pairs = list(combinations(entries.values(), 2))
        binds = _state.db.add_bind_karma_many(
            [(ozernik_1.id, ozernik_2.id) for (_, ozernik_1), (_, ozernik_2) in pairs],
            karma=1,
            channel_id=channel.id,  # +1 минута каждому в статистику канала
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

    async def build_rank_cube_file(self, member: discord.Member, offset: int = 0) -> discord.File:
        ozernik = self.bot.db_ensure_user(member)
        avatar = await get_discord_avatar(member)
        full_binds = await get_full_binds(member, self.bot, self.BINDS_PER_PAGE, offset)

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

    async def build_stats_file(self, member: discord.Member, ozernik: DataTypes.Ozernik, kind: str) -> discord.File:
        """Страница «Где пишу» (text) или «Где сижу» (voice) карточки Сансары."""
        avatar = await get_discord_avatar(member)

        # База и сервер — здесь, в основном потоке; рисование — в отдельном.
        page = get_channel_stats_page(ozernik.id, kind, self.bot.guild)
        color = get_status(_state.db.get_karma(ozernik.id))['color']

        card_bytes = await asyncio.to_thread(create_stats_card, avatar, member.display_name, color, page)

        return discord.File(card_bytes, filename=f"{member.name}_sansara_{kind}.png")

    async def build_events_file(self, member: discord.Member, ozernik: DataTypes.Ozernik) -> discord.File:
        """Страница «Ивенты» карточки Сансары: карма за ивенты и последние награды."""
        avatar = await get_discord_avatar(member)

        page = get_events_page(ozernik.id)
        color = get_status(_state.db.get_karma(ozernik.id))['color']

        card_bytes = await asyncio.to_thread(create_events_card, avatar, member.display_name, color, page)

        return discord.File(card_bytes, filename=f"{member.name}_sansara_events.png")

    async def build_guises_file(self, member: discord.Member, ozernik: DataTypes.Ozernik) -> discord.File:
        """Страница «Другие обличья» карточки Сансары: основной аккаунт и твинки."""
        avatar = await get_discord_avatar(member)
        color = get_status(_state.db.get_karma(ozernik.id))['color']
        moscow = self.bot.moscow_tz

        links = [(ozernik.discord_id, None)] + _state.db.get_twink_links(ozernik.id)
        accounts = []

        for n, (discord_id, linked_at) in enumerate(links[:GUISES_ON_PAGE]):
            # Аккаунт мог уйти с сервера — тогда берём его как пользователя Discord.
            user = self.bot.guild.get_member(discord_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(discord_id)
                except discord.HTTPException:
                    user = None

            since = ''
            if linked_at:
                since = datetime.fromtimestamp(linked_at / 1000, moscow).strftime('%d.%m.%Y')

            accounts.append({
                'name': user.display_name if user else f'ID {discord_id}',
                'avatar': await get_discord_avatar(user) if user else None,
                'main': n == 0,
                'since': since,
            })

        # Оставшиеся (больше восьми) не рисуются — только их число («и ещё N»).
        accounts += [{'name': '', 'avatar': None, 'main': False, 'since': ''}] * (len(links) - len(accounts))

        card_bytes = await asyncio.to_thread(create_guises_card, avatar, member.display_name, color, accounts)

        return discord.File(card_bytes, filename=f"{member.name}_sansara_guises.png")

    # ------ СТРАНИЦЫ КАРТОЧЕК ------

    BINDS_PER_PAGE = 10

    def card_pages_count(self, card: str, ozernik_id: int) -> int:
        if card == 'rs':
            # «Другие обличья» — последняя страница и только у тех, у кого есть твинки (решение Alium).
            has_twinks = bool(_state.db.get_twinks(ozernik_id))
            return len(SANSARA_PAGE_NAMES) if has_twinks else len(SANSARA_PAGE_NAMES) - 1

        binds = len(_state.db.get_user_top_karmic_binds(ozernik_id))
        return max(1, -(-binds // self.BINDS_PER_PAGE))  # деление с округлением вверх

    async def build_card_page(self, card: str, target: discord.abc.User, page: int = 0) -> tuple[discord.File, dict]:
        """
        Картинка страницы карточки и кнопки к ней.

        :return: (файл, {'view': кнопки} или {} — если страница одна).
        """
        ozernik = self.bot.db_ensure_user(target)
        pages = self.card_pages_count(card, ozernik.id)
        page %= pages  # связей могло стать меньше, чем было страниц, когда рисовалась кнопка

        if card == 'rs':
            if page == 0:
                file = await self.build_rank_sansara_file(target)
            elif page == 3:
                file = await self.build_events_file(target, ozernik)
            elif page == 4:
                file = await self.build_guises_file(target, ozernik)
            else:
                file = await self.build_stats_file(target, ozernik, ('text', 'voice')[page - 1])
        else:
            file = await self.build_rank_cube_file(target, offset=page * self.BINDS_PER_PAGE)

        view = card_pages_view(card, target.id, page, pages)
        return file, ({'view': view} if view else {})

    async def turn_card_page(self, interaction: Interaction, card: str, user_id: int, page: int) -> None:
        """Нажата ◀ / ▶ под карточкой."""
        # Картинка (и аватарки связей) готовится дольше 3 секунд, которые Discord ждёт ответа.
        await interaction.response.defer()

        target = self.bot.guild.get_member(user_id) or await self.bot.fetch_user(user_id)
        file, view = await self.build_card_page(card, target, page)

        await interaction.edit_original_response(attachments=[file], **view)

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

            file, view = await self.build_card_page('rs' if name == 'rank_sansara' else 'rc', target)

            await message.reply(file=file, allowed_mentions=no_ping, **view)
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
        await interaction.response.defer()

        file, view = await self.build_card_page('rs', user or interaction.user)

        await interaction.followup.send(file=file, **view)

    @app_commands.command(name='rank_cube', description='Просмотр ранга Куба. Сокращение: ,rc')
    @app_commands.guilds(config.GUILD_ID)
    async def rank_cube(self, interaction: Interaction, user: discord.Member = None):
        await interaction.response.defer()

        file, view = await self.build_card_page('rc', user or interaction.user)

        await interaction.followup.send(file=file, **view)

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

    # -------------- ТВИНКИ --------------

    async def notify_user(self, discord_id: int, text: str) -> None:
        """Сообщение в ЛС. Если ЛС закрыты или человека не найти — просто пропускаем."""
        try:
            user = self.bot.get_user(discord_id) or await self.bot.fetch_user(discord_id)
            await user.send(text)
        except (discord.HTTPException, AttributeError):
            pass

    async def link_accounts(self, main_id: int, twink_id: int) -> tuple[str | None, str]:
        """
        Привязывает твинк в базе и выравнивает роли всех аккаунтов человека.

        :return: (причина отказа или None, строка о перенесённом для сообщений).
        """
        # С момента запроса всё могло поменяться — проверяем заново (link_twink тоже проверяет).
        if _state.db.is_twink(main_id):
            return 'основной аккаунт сам стал чьим-то твинком.', ''

        main_user = self.bot.db_ensure_user(SimpleNamespace(id=main_id))

        try:
            moved = _state.db.link_twink(main_user.id, twink_id)
        except ValueError as error:
            return str(error), ''

        # Роли твинка — как у основного (решение Alium): пересчёт сразу для всех аккаунтов.
        guild = self.bot.guild
        account = guild.get_member(twink_id) or guild.get_member(main_id) if guild else None

        if account is not None:
            try:
                await self.update_sansara_roles(account)
                await self.update_cube_roles(account)
            except Exception as error:
                self.bot.print_error("confirm_twink", error)

        summary = ''
        if any(moved.values()):
            summary = (
                f'\nПеренесено к основному: карма `{moved["karma"]}`, '
                f'недельная `{moved["weekly_karma"]}`, связей `{moved["binds"]}`.'
            )

        return None, summary

    async def confirm_twink(self, main_id: int, twink_id: int) -> str:
        """
        Привязка после подтверждения твинком в ЛС.

        :return: Текст для сообщения твинку.
        """
        reason, summary = await self.link_accounts(main_id, twink_id)

        if reason:
            return f'Не привязано: {reason}'

        await self.send_log(f'**Твинк привязан:** <@{twink_id}> → <@{main_id}>{summary}')
        await self.give_guise_message(main_id, twink_id)
        await self.notify_user(main_id, f'<@{twink_id}> подтвердил привязку — теперь это ваш твинк.{summary}')

        return f'Готово: этот аккаунт — твинк <@{main_id}>.{summary}'

    @app_commands.command(name='twink_add', description='Привязать свой второй аккаунт: его карма будет считаться вашей. Твинк подтверждает в ЛС.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.describe(account='Ваш второй аккаунт на сервере')
    async def twink_add(self, interaction: Interaction, account: discord.Member):
        reason = check_twink_request(interaction.user, account)

        if reason:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        view = TwinkConfirmView(self, interaction.user.id, account.id)

        try:
            view.message = await account.send(twink_request_text(interaction.user, account), view=view)
        except discord.HTTPException:
            await interaction.response.send_message(
                f'Не получилось написать {account.mention} в ЛС. Откройте на этом аккаунте личные сообщения '
                f'от участников сервера (Настройки конфиденциальности сервера) и повторите.',
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f'Запрос отправлен в ЛС {account.mention}. Подтвердите его с того аккаунта в течение суток.',
            ephemeral=True,
        )

    @app_commands.command(name='twink_link', description='Привязать твинк к человеку без его подтверждения.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(main='Основной аккаунт', account='Твинк (можно ID, даже если он ушёл с сервера)')
    async def twink_link(self, interaction: Interaction, main: discord.User, account: discord.User):
        if not await self.check_admin(interaction):
            return

        summary = ''

        if main.bot:
            reason = 'бот не может быть основным аккаунтом.'
        elif main.id == account.id:
            reason = 'это один и тот же аккаунт.'
        elif _state.db.is_twink(main.id):
            owner = _state.db.get_user_by_discord_id(main.id)
            reason = f'{main.mention} сам твинк <@{owner.discord_id}> — укажите основной аккаунт.'
        else:
            reason = check_twink_request(main, account)

        if not reason:
            reason, summary = await self.link_accounts(main.id, account.id)

        if reason:
            await interaction.response.send_message(f'Не привязано: {reason}', ephemeral=True)
            return

        text = f'{account.mention} привязан как твинк {main.mention}.{summary}'

        await interaction.response.send_message(text, ephemeral=True)
        await self.send_log(f'{interaction.user.mention} → **Твинк привязан администрацией:** {text}')
        await self.give_guise_message(main.id, account.id)

        notice = (
            f'Администрация связала аккаунты: {account.mention} теперь твинк {main.mention}, '
            f'вся его карма считается основному.'
        )
        await self.notify_user(main.id, notice)
        await self.notify_user(account.id, notice)

    @app_commands.command(name='twink_remove', description='Отвязать твинк. Карма, набранная за время привязки, остаётся у основного.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(account='Твинк, который нужно отвязать')
    async def twink_remove(self, interaction: Interaction, account: discord.User):
        # Только администрация (решение Alium): иначе твинк можно отвязать, чтобы уйти от наказания.
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                'Отвязать твинк может только администрация — обратитесь к ней.',
                ephemeral=True,
            )
            return

        owner = _state.db.get_user_by_discord_id(account.id) if _state.db.is_twink(account.id) else None

        if owner is None:
            await interaction.response.send_message(f'{account.mention} не привязан как твинк.', ephemeral=True)
            return

        _state.db.unlink_twink(account.id)

        # Отвязанный аккаунт начинает с нуля — его роли пересчитываются по его собственной карме.
        member = interaction.guild.get_member(account.id) if interaction.guild else None
        if member is not None and not member.bot:
            try:
                self.bot.db_ensure_user(member)
                await self.update_sansara_roles(member)
                await self.update_cube_roles(member)
            except Exception as error:
                self.bot.print_error("twink_remove", error)

        text = f'{account.mention} отвязан от <@{owner.discord_id}>. Набранная за время привязки карма осталась у основного.'

        await interaction.response.send_message(text, ephemeral=True)
        await self.send_log(f'{interaction.user.mention} → **Твинк отвязан:** {text}')
        await self.notify_user(owner.discord_id, f'Администрация отвязала {account.mention} от вашего аккаунта.')

    @app_commands.command(name='twinks', description='Показать привязанные твинки.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.describe(user='Чьи твинки показать (по умолчанию — ваши)')
    async def twinks(self, interaction: Interaction, user: discord.Member = None):
        target = user or interaction.user
        ozernik = _state.db.get_user_by_discord_id(target.id)
        twink_ids = _state.db.get_twinks(ozernik.id) if ozernik else []

        if not twink_ids:
            text = f'У {target.mention} нет твинков.'
        else:
            lines = [f'Основной аккаунт: <@{ozernik.discord_id}>', 'Твинки:']
            lines += [f'- <@{twink_id}>' for twink_id in twink_ids]
            text = '\n'.join(lines)

        await interaction.response.send_message(
            text,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name='twinks_all', description='Таблица всех твинков сервера.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    async def twinks_all(self, interaction: Interaction):
        if not await self.check_admin(interaction):
            return

        groups = _state.db.get_all_twinks()

        if not groups:
            await interaction.response.send_message('Твинков на сервере нет.', ephemeral=True)
            return

        view = AllTwinksView(interaction.user.id, groups)
        await interaction.response.send_message(
            embed=view.embed(),
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
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
    @app_commands.describe(user='Участник', amount='Сколько кармы добавить', reason='За что (видно человеку в ЛС и на странице «Ивенты»)')
    async def karma_add(
            self,
            interaction: Interaction,
            user: discord.Member,
            amount: app_commands.Range[int, 1, 1_000_000],
            reason: app_commands.Range[str, 1, 200] = None,
    ):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        reason = (reason or '').strip() or self.DEFAULT_EVENT_REASON

        ozernik = self.bot.db_ensure_user(user)
        # Недельная карма не меняется: недельный топ — только за собственную активность.
        # Начисление — награда за ивент: попадает в «Карму за ивенты» с причиной.
        old, new, award_id = _state.db.add_event_karma(ozernik.id, amount, reason, interaction.user.id)

        text = (
            self.karma_change_text('Карма добавлена', user, old, new)
            + f'\nПричина: {reason} (награда #{award_id})'
        )
        await self.finish_admin_edit(interaction, user, text, old, new)

        if not user.bot:
            await self.notify_user(user.id, f'Вам начислено **{amount}** кармы: {reason}')

    DEFAULT_EVENT_REASON = 'Ивент'

    @app_commands.command(name='karma_event_delete', description='Убрать ошибочную награду из «Кармы за ивенты». Сама карма не меняется.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(award='Номер награды (#N — на странице «Ивенты» и в логе)')
    async def karma_event_delete(self, interaction: Interaction, award: app_commands.Range[int, 1]):
        if not await self.check_admin(interaction):
            return

        deleted = _state.db.delete_event_award(award)

        if deleted is None:
            await interaction.response.send_message(f'Награды #{award} нет.', ephemeral=True)
            return

        owner = _state.db.get_user(deleted['user_id'])
        mention = f'<@{owner.discord_id}>' if owner and owner.discord_id else 'участника'
        text = (
            f'**Награда #{award} удалена** у {mention}: `{deleted["amount"]}` кармы, «{deleted["reason"]}».\n'
            f'-# Сама карма не изменилась — если нужно, отнимите её через /karma_remove.'
        )

        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        await self.send_log(f'{interaction.user.mention} → {text}')

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

        if new_status is not None:
            try:
                await self.give_status_message(user, new_status)
            except Exception as error:
                self.bot.print_error("karma_status", error)

async def setup(bot):
    await bot.add_cog(KarmaSistem(bot))
    pass
