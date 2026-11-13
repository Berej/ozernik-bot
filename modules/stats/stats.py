"""
Плашка статистики *i (решения Alium 2026-11-14) — общая статистика человека по серверу.
Полная замена страниц статистики карточки ,rs: «Где пишу/сижу», «Ивенты» и «Другие обличья»
переехали сюда, в ,rs их больше нет (без дублей).

Страницы — кнопки с номерами, по одной на страницу; переключать может только тот, кто вызвал
плашку (решение Alium 2026-11-14); кнопки работают и после перезапуска:
- Статистика — заход на сервер и стаж, места в таблицах лидеров кармы, сообщения и войс
  за 1/7/30 дней, краткий топ каналов, мини-график;
- Топ каналов — топ-5 текстовых и топ-5 голосовых, период кнопками: сутки, неделя,
  30 дней, всё время;
- График — по дням за 30 дней;
- Личные войсы — в чьих сидит, кто сидит у него (только если есть данные);
- Ивенты — карма за ивенты и последние награды (только если есть награды; данные — у кармы);
- Другие обличья — все аккаунты человека (только если есть твинки).

Модуль сам собирает то, что показывает (в общую базу database.db):
- сообщения — ВСЕ, кроме ботов и сокращений-команд (,rs, *i …): и в каналах без кармы,
  и у людей с меткой РСН. Раньше журнал писала карма по своим правилам — теперь карма
  пишет туда только минуты Связи;
- войс — всё время в голосовых, кроме AFK-канала (сидел один, заглушён — тоже считается);
- по каналам и часам — для «топа каналов»;
- первый заход — самый ранний из виденных (Discord помнит только последний).

Сообщения копятся в памяти и пишутся пачкой раз в минуту: коммит на диск ~15 мс, по коммиту
на сообщение однопоточный бот стоял бы. Падение бота теряет не больше минуты.
Человек со всеми аккаунтами — один (твинки): всё пишется на основной.
"""

import asyncio
import re
import time
import traceback
from collections import OrderedDict
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands, tasks
from PIL import Image

from ._stats_cards import (
    render_channels, render_chart, render_events, render_guises, render_overview, render_voices,
)
from ._stats_data import (
    CHANNEL_PERIODS, DEFAULT_CHANNEL_PERIOD, GUISES_ON_PAGE, collect_channels, collect_chart,
    collect_overview, collect_voices, events_page, format_numeric_date, has_voices_page,
)

SHORTCUT = "*i"

# Ключ страницы → название на кнопке. Порядок — порядок листания. В custom_id кнопок пишется
# ключ, а не номер: набор страниц у человека меняется (появились твинки, награды), и номер
# из старой кнопки открыл бы не ту страницу.
PAGES = {
    "main": "Статистика",
    "channels": "Топ каналов",
    "chart": "График",
    "voices": "Личные войсы",
    "events": "Ивенты",
    "guises": "Другие обличья",
}


def now() -> float:
    """Текущее время. Отдельной функцией — тесты подменяют её здесь, где она используется."""
    return time.time()


# ---------- Аватары и ушедшие ----------
# Своя маленькая память, как у карточек кармы (_cards._avatar_cache): модуль статистики не
# зависит от кармы и работает, даже если она выключена.

AVATAR_FETCH_SIZE = 256
AVATAR_CACHE_SIZE = 50
_avatar_cache: OrderedDict[str, Image.Image] = OrderedDict()

# Ушедших с сервера бот берёт запросом fetch_user — отдельный запрос с лимитами Discord.
# Запоминаем на 10 минут (имя и аватар могут смениться, поэтому не навсегда).
FETCHED_USER_TTL = 600
_fetched_users: dict[int, tuple[float, discord.abc.User]] = {}


def decode_avatar(avatar_bytes: bytes) -> Image.Image:
    with Image.open(BytesIO(avatar_bytes)) as source:
        return source.convert("RGBA").resize((200, 200), Image.Resampling.LANCZOS)


async def get_avatar(user) -> Image.Image:
    asset = user.display_avatar.with_size(AVATAR_FETCH_SIZE)
    cached = _avatar_cache.get(asset.url)
    if cached is not None:
        _avatar_cache.move_to_end(asset.url)
        return cached.copy()

    try:
        avatar_bytes = await asset.read()
        # Раскодировка — синхронный Pillow: в основном потоке она остановила бы бота.
        avatar = await asyncio.to_thread(decode_avatar, avatar_bytes)
    except (discord.HTTPException, OSError):
        return Image.new("RGBA", (200, 200), (60, 62, 68, 255))

    _avatar_cache[asset.url] = avatar
    if len(_avatar_cache) > AVATAR_CACHE_SIZE:
        _avatar_cache.popitem(last=False)
    return avatar.copy()


def name_color(user) -> str:
    """Цвет имени — как на сервере (старшая цветная роль); без цвета — белый."""
    colour = getattr(user, "colour", None)
    if colour is None or not colour.value:
        return "#FFFFFF"
    return str(colour)


# ---------- Кнопки ----------
# Чья плашка, какая страница (период) и кто вызвал — в custom_id: кнопки переживают перезапуск,
# а проверка «нажимает вызвавший» не требует ничего хранить в памяти.

NOT_YOURS = "Переключать может только тот, кто вызвал плашку. Своя — `*i`."
PAGE_BUTTONS_PER_ROW = 5   # больше в одном ряду Discord не принимает


async def check_invoker(interaction: discord.Interaction, invoker_id: int):
    """Cog статистики, если нажал вызвавший и модуль включён; иначе — скрытый ответ и None."""
    if interaction.user.id != invoker_id:
        await interaction.response.send_message(NOT_YOURS, ephemeral=True)
        return None
    cog = interaction.client.get_cog("Stats")
    if cog is None:
        await interaction.response.send_message("Статистика сейчас выключена.", ephemeral=True)
    return cog


class StatsPageButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"stats:i:(?P<user>\d+):(?P<page>[a-z]+):(?P<invoker>\d+)",
):
    """Кнопка с номером страницы. В custom_id — ключ страницы, а не номер (см. PAGES)."""

    def __init__(self, user_id: int, page: str, invoker_id: int, label: str = "·", row: int = 0):
        super().__init__(discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"stats:i:{user_id}:{page}:{invoker_id}",
            row=row,
        ))
        self.user_id = user_id
        self.page = page
        self.invoker_id = invoker_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str], /):
        return cls(int(match["user"]), match["page"], int(match["invoker"]))

    async def callback(self, interaction: discord.Interaction):
        cog = await check_invoker(interaction, self.invoker_id)
        if cog is not None:
            await cog.turn_page(interaction, self.user_id, self.page, invoker_id=self.invoker_id)


class StatsPeriodButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"stats:period:(?P<user>\d+):(?P<period>1|7|30|all):(?P<invoker>\d+)",
):
    """Период страницы «Топ каналов»: сутки / неделя / 30 дней / всё время."""

    def __init__(self, user_id: int, period: str, invoker_id: int, row: int = 1):
        super().__init__(discord.ui.Button(
            label=CHANNEL_PERIODS[period][1],
            style=discord.ButtonStyle.secondary,
            custom_id=f"stats:period:{user_id}:{period}:{invoker_id}",
            row=row,
        ))
        self.user_id = user_id
        self.period = period
        self.invoker_id = invoker_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str], /):
        return cls(int(match["user"]), match["period"], int(match["invoker"]))

    async def callback(self, interaction: discord.Interaction):
        cog = await check_invoker(interaction, self.invoker_id)
        if cog is not None:
            await cog.turn_page(interaction, self.user_id, "channels", self.period, self.invoker_id)


def pages_view(user_id: int, page: str, pages: list[str], invoker_id: int,
               period: str | None = None) -> discord.ui.View:
    """
    Кнопка с номером на каждую страницу; открытая — подсвечена и не нажимается. Страниц больше
    пяти — два ряда поровну (в ряду Discord принимает не больше пяти кнопок). На «Топе каналов» —
    ещё ряд с периодами.
    """
    view = discord.ui.View(timeout=None)
    per_row = len(pages) if len(pages) <= PAGE_BUTTONS_PER_ROW else -(-len(pages) // 2)

    for n, key in enumerate(pages):
        row = n // per_row
        if key == page:
            view.add_item(discord.ui.Button(
                label=str(n + 1), style=discord.ButtonStyle.primary, disabled=True,
                custom_id=f"stats:i_now:{user_id}", row=row,
            ))
        else:
            view.add_item(StatsPageButton(user_id, key, invoker_id, label=str(n + 1), row=row))

    if page == "channels":
        period_row = (len(pages) - 1) // per_row + 1
        for key, (_, label, _) in CHANNEL_PERIODS.items():
            if key == period:
                view.add_item(discord.ui.Button(
                    label=label, style=discord.ButtonStyle.primary, disabled=True,
                    custom_id=f"stats:period_now:{user_id}", row=period_row,
                ))
            else:
                view.add_item(StatsPeriodButton(user_id, key, invoker_id, row=period_row))
    return view


DYNAMIC_ITEMS = (StatsPageButton, StatsPeriodButton)


class Stats(commands.Cog):
    # Сокращения модуля: карма их видит (KarmaSistem.is_foreign_shortcut) и карму за них не даёт.
    SHORTCUTS = (SHORTCUT,)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        # (человек, начало часа, вид) → сколько; (человек, канал, начало часа, вид) → сколько
        self.pending_hours: dict[tuple[int, int, str], int] = {}
        self.pending_channels: dict[tuple[int, int, int, str], int] = {}
        self.joins_scanned = False

    async def cog_load(self):
        self.bot.add_dynamic_items(*DYNAMIC_ITEMS)
        self.stats_tick.start()

    async def cog_unload(self):
        self.stats_tick.cancel()
        self.bot.remove_dynamic_items(*DYNAMIC_ITEMS)
        self.flush()

    # ---------- сбор ----------

    def note(self, person_id: int, channel_id: int, kind: str, amount: int = 1) -> None:
        hour = int(now()) // 3600 * 3600
        hour_key = (person_id, hour, kind)
        channel_key = (person_id, channel_id, hour, kind)
        self.pending_hours[hour_key] = self.pending_hours.get(hour_key, 0) + amount
        self.pending_channels[channel_key] = self.pending_channels.get(channel_key, 0) + amount

    def flush(self) -> None:
        """Записать накопленное. Не вышло (база занята) — вернуть в память, повторится в следующий раз."""
        hours, channels = self.pending_hours, self.pending_channels
        self.pending_hours, self.pending_channels = {}, {}

        for pending, rows, write in (
            (hours, [(*key, amount) for key, amount in hours.items()], self.db.add_activity_many),
            (channels, [(*key, amount) for key, amount in channels.items()], self.db.add_channel_activity_many),
        ):
            try:
                write(rows)
            except Exception:
                print(f"[Статистика] Не записать, повторю через минуту:\n{traceback.format_exc()}")
                target = self.pending_hours if pending is hours else self.pending_channels
                for key, amount in pending.items():
                    target[key] = target.get(key, 0) + amount

    def is_command(self, first_word: str) -> bool:
        """Сокращения-команды в статистику не идут: *i и сокращения кармы (,rs, ,lb …)."""
        if first_word == SHORTCUT:
            return True
        karma = self.bot.get_cog("KarmaSistem")
        return first_word in getattr(karma, "SHORTCUTS", {})

    def own_guild(self, guild) -> bool:
        main = self.bot.guild
        return guild is not None and main is not None and guild.id == main.id

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self.own_guild(message.guild) or message.author.bot:
            return

        words = message.content.split()
        first_word = words[0].lower() if words else ""

        if first_word == SHORTCUT:
            await self.handle_shortcut(message)
            return
        if self.is_command(first_word):
            return

        person = self.bot.db_ensure_user(message.author)
        # Ветка засчитывается своему каналу (у ветки свой ID).
        channel_id = getattr(message.channel, "parent_id", None) or message.channel.id
        self.note(person.id, channel_id, "messages")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if self.own_guild(member.guild) and not member.bot and member.joined_at:
            self.db.note_member_joins([(member.id, int(member.joined_at.timestamp()))])

    def note_joins(self, members) -> None:
        self.db.note_member_joins([
            (member.id, int(member.joined_at.timestamp()))
            for member in members
            if not member.bot and getattr(member, "joined_at", None)
        ])

    def count_voice(self, guild) -> None:
        """+1 минута каждому человеку в голосовом канале, кроме AFK. Твинки в войсе одновременно — одна минута."""
        afk = guild.afk_channel
        counted = set()
        for channel in [*guild.voice_channels, *getattr(guild, "stage_channels", [])]:
            if afk is not None and channel.id == afk.id:
                continue
            for member in channel.members:
                if member.bot:
                    continue
                person = self.bot.db_ensure_user(member).id
                if person in counted:
                    continue
                counted.add(person)
                self.note(person, channel.id, "voice_presence")

    @tasks.loop(minutes=1)
    async def stats_tick(self):
        """
        Раз в минуту: войс и запись накопленного. Всё в try: необработанное исключение
        в tasks.loop останавливает цикл навсегда, и сбор тихо прекратился бы.
        """
        try:
            guild = self.bot.guild
            if guild is None:
                return
            if not self.joins_scanned:
                # Один раз после запуска: заходы всех, кто на сервере (кто зашёл, пока бот лежал).
                self.note_joins(guild.members)
                self.joins_scanned = True
            self.count_voice(guild)
            self.flush()
        except Exception:
            print(f"[Статистика] Ошибка цикла:\n{traceback.format_exc()}")

    @stats_tick.before_loop
    async def _before_tick(self):
        await self.bot.wait_until_ready()

    # ---------- плашка ----------

    def server_places(self, person_id: int) -> list[tuple[str, int | None]]:
        """
        Строки плашки «Место на сервере» (решение Alium 2026-11-14): места в таблицах лидеров
        кармы ,lb и ,lbw — у модуля кармы, тем же подсчётом, что у карточки ,rs. Карма
        выключена — места нет («—»). Третья строка — позже.
        """
        karma = self.bot.get_cog("KarmaSistem")
        places = karma.leaderboard_places(person_id) if karma is not None else {}
        return [("Карма", places.get("karma")), ("Карма за неделю", places.get("weekly"))]

    def event_summary(self, person_id: int) -> dict | None:
        """Награды за ивенты — данные кармы; карма выключена — None (страницы нет)."""
        karma = self.bot.get_cog("KarmaSistem")
        return karma.event_awards_summary(person_id) if karma is not None else None

    def available_pages(self, person_id: int) -> list[str]:
        """Страницы человека по порядку. Необязательные — только когда им есть что показать."""
        pages = ["main", "channels", "chart"]
        if has_voices_page(self.db, person_id):
            pages.append("voices")
        summary = self.event_summary(person_id)
        if summary and summary["count"]:
            pages.append("events")
        if self.db.get_twinks(person_id):
            pages.append("guises")
        return pages

    async def get_user_anywhere(self, user_id: int):
        """Участник сервера, а если ушёл — пользователь Discord (запросом, с памятью на 10 минут)."""
        guild = self.bot.guild
        member = guild.get_member(user_id) if guild else None
        if member is not None:
            return member

        saved = _fetched_users.get(user_id)
        if saved is not None and time.monotonic() - saved[0] < FETCHED_USER_TTL:
            return saved[1]
        user = await self.bot.fetch_user(user_id)
        _fetched_users[user_id] = (time.monotonic(), user)
        return user

    async def collect_guises(self, person_id: int) -> dict:
        """Аккаунты человека: основной первым, твинки с датой привязки. Аватары — заранее и одновременно."""
        main = self.db.get_user(person_id)
        links = [(main.discord_id, None)] + self.db.get_twink_links(person_id)

        async def load(n: int, discord_id: int, linked_at: int | None) -> dict:
            try:
                user = await self.get_user_anywhere(discord_id)
            except discord.HTTPException:
                user = None  # аккаунт удалён — Discord его не отдаёт
            return {
                "name": user.display_name if user else f"ID {discord_id}",
                "avatar": await get_avatar(user) if user else None,
                "main": n == 0,
                "since": format_numeric_date(linked_at / 1000) if linked_at else "",
            }

        # gather — все сразу: по очереди скачивания аватаров складывались бы.
        accounts = await asyncio.gather(*(
            load(n, discord_id, linked_at) for n, (discord_id, linked_at) in enumerate(links[:GUISES_ON_PAGE])
        ))
        return {"accounts": list(accounts), "total": len(links)}

    async def build_page(self, user, page: str = "main", period: str = DEFAULT_CHANNEL_PERIOD,
                         invoker_id: int | None = None):
        """
        Картинка страницы и кнопки. user — участник или (ушёл с сервера) пользователь Discord.
        invoker_id — кто вызвал плашку: только он переключает страницы (по умолчанию — сам user).
        """
        self.flush()  # свежие сообщения — в базу, чтобы цифры были на сейчас
        joined_at = getattr(user, "joined_at", None)
        if joined_at is not None:
            self.db.note_member_joins([(user.id, int(joined_at.timestamp()))])

        person_id = self.bot.db_ensure_user(user).id
        pages = self.available_pages(person_id)
        if page not in pages:
            page = "main"  # страницы уже нет (например, кнопка из старого сообщения)
        if period not in CHANNEL_PERIODS:
            period = DEFAULT_CHANNEL_PERIOD
        guild = self.bot.guild

        # Данные — здесь, в основном потоке (база); рисование — в отдельном.
        if page == "main":
            places = self.server_places(person_id)
            data, render = collect_overview(self.db, guild, user, person_id, places, now()), render_overview
        elif page == "channels":
            data, render = collect_channels(self.db, guild, person_id, period, now()), render_channels
        elif page == "chart":
            data, render = collect_chart(self.db, person_id, now()), render_chart
        elif page == "voices":
            data, render = collect_voices(self.db, guild, person_id), render_voices
        elif page == "events":
            data, render = events_page(self.event_summary(person_id)), render_events
        else:
            data, render = await self.collect_guises(person_id), render_guises

        avatar = await get_avatar(user)
        buffer = await asyncio.to_thread(render, avatar, user.display_name, name_color(user), data)
        view = pages_view(user.id, page, pages, invoker_id if invoker_id is not None else user.id,
                          period if page == "channels" else None)
        return discord.File(buffer, filename="stats.png"), view

    async def handle_shortcut(self, message: discord.Message) -> None:
        mentioned = (message.guild.get_member(user.id) for user in getattr(message, "mentions", []))
        target = next((user for user in mentioned if user is not None), message.author)
        no_ping = discord.AllowedMentions(replied_user=False)

        if target.bot:
            await message.reply("У ботов статистики нет.", allowed_mentions=no_ping)
            return

        file, view = await self.build_page(target, invoker_id=message.author.id)
        await message.reply(file=file, view=view, allowed_mentions=no_ping)

    @app_commands.command(name="stats", description="Статистика участника: сообщения, войс, каналы, график")
    @app_commands.describe(member="Чья статистика (по умолчанию — ваша)")
    async def stats(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        if target.bot:
            await interaction.response.send_message("У ботов статистики нет.", ephemeral=True)
            return

        # Картинка готовится дольше 3 секунд, которые Discord ждёт ответа.
        await interaction.response.defer()
        file, view = await self.build_page(target, invoker_id=interaction.user.id)
        await interaction.followup.send(file=file, view=view)

    async def turn_page(self, interaction: discord.Interaction, user_id: int, page: str,
                        period: str = DEFAULT_CHANNEL_PERIOD, invoker_id: int | None = None) -> None:
        """Нажата кнопка страницы или периода (кто нажал — уже проверено в кнопке)."""
        await interaction.response.defer()
        target = await self.get_user_anywhere(user_id)
        file, view = await self.build_page(target, page, period, invoker_id)
        await interaction.edit_original_response(attachments=[file], view=view)


async def setup(bot):
    await bot.add_cog(Stats(bot))
