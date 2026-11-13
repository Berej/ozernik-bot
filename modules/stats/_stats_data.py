"""
Данные плашки *i. Собираются в основном потоке (база и кэш Discord); рисует _stats_cards —
в отдельном потоке и без базы: соединение SQLite нельзя трогать из другого потока.

Периоды «1 / 7 / 30 дней» и места на сервере — скользящие, по часам журнала activity
(«за последние 24 часа», а не «с полуночи»). Топ каналов и график — по московским суткам.
"""

import time
from datetime import datetime, timedelta, timezone

from utilities import moscow_day_start

HOUR = 3600
DAY = 86400
LOOKBACK_DAYS = 30
PERIODS = (1, 7, 30)
TOP_CHANNEL_ROWS = 4      # строк в «Топе каналов» на главной: по 2 текстовых и голосовых (не хватает одних — больше других)
CHANNELS_ON_PAGE = 5      # строк в колонках страницы «Топ каналов»
TOP_VOICE_PEOPLE = 5      # строк в колонках страницы «Личные войсы»
GUISES_ON_PAGE = 8        # аккаунтов на странице «Другие обличья» (две колонки по четыре)

# Периоды страницы «Топ каналов» (решение Alium 2026-11-14: и за всё время, и за сутки/неделю/30 дней).
# Ключ — то, что пишется в custom_id кнопки; дни None — за всё время.
CHANNEL_PERIODS = {
    "1": (1, "Сутки", "за сутки"),
    "7": (7, "Неделя", "за неделю"),
    "30": (30, "30 дней", "за 30 дней"),
    "all": (None, "Всё время", "за всё время"),
}
DEFAULT_CHANNEL_PERIOD = "30"

KINDS = ("messages", "voice_presence")
KIND_LABELS = {"messages": "сообщения", "voice_presence": "войс"}

# Фиксированный UTC+3, как bot.MOSCOW_TZ: ZoneInfo('Europe/Moscow') на Windows без tzdata падает.
MOSCOW = timezone(timedelta(hours=3))

MONTHS_GENITIVE = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                   "сентября", "октября", "ноября", "декабря")
MONTHS_SHORT = ("янв.", "февр.", "марта", "апр.", "мая", "июня", "июля", "авг.", "сент.", "окт.", "нояб.", "дек.")


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_count(n: int) -> str:
    """149 · 1 020 · 65,4 тыс. · 123 тыс."""
    if n < 10_000:
        return f"{n:,}".replace(",", " ")
    if n < 100_000:
        return f"{n / 1000:.1f}".replace(".", ",").replace(",0", "") + " тыс."
    return f"{n // 1000} тыс."


def format_hours(minutes: int) -> str:
    """45 мин · 1,7 ч · 476 ч"""
    if minutes < 60:
        return f"{minutes} мин"
    hours = minutes / 60
    if hours < 100:
        return f"{hours:.1f}".replace(".", ",").replace(",0", "") + " ч"
    return f"{round(hours)} ч"


def format_date(timestamp: float) -> str:
    """15 сентября 2024 (по Москве)."""
    moment = datetime.fromtimestamp(timestamp, MOSCOW)
    return f"{moment.day} {MONTHS_GENITIVE[moment.month - 1]} {moment.year}"


def format_numeric_date(timestamp: float) -> str:
    """23.10.2026 (по Москве)."""
    return datetime.fromtimestamp(timestamp, MOSCOW).strftime("%d.%m.%Y")


def format_short_date(timestamp: float) -> str:
    moment = datetime.fromtimestamp(timestamp, MOSCOW)
    return f"{moment.day} {MONTHS_SHORT[moment.month - 1]}"


def format_tenure(joined: float, now: float) -> str:
    """Сколько на сервере: «1 год 2 мес.», «3 мес.», «12 дней», «меньше дня». Календарно, по Москве."""
    start = datetime.fromtimestamp(joined, MOSCOW)
    end = datetime.fromtimestamp(now, MOSCOW)

    months = (end.year - start.year) * 12 + end.month - start.month
    if (end.day, end.time()) < (start.day, start.time()):
        months -= 1

    if months >= 12:
        years, rest = divmod(months, 12)
        text = f"{years} {plural(years, 'год', 'года', 'лет')}"
        return f"{text} {rest} мес." if rest else text
    if months >= 1:
        return f"{months} мес."

    days = (end - start).days
    if days >= 1:
        return f"{days} {plural(days, 'день', 'дня', 'дней')}"
    return "меньше дня"


def join_line(joined: float | None, now: float) -> str:
    """Строка под ником (решение Alium 2026-11-14: без рода — бот не знает, кто перед ним)."""
    if joined is None:
        return "Дата захода неизвестна"
    return f"На сервере с {format_date(joined)} · {format_tenure(joined, now)}"


def first_join(db, person_id: int, user) -> float | None:
    """Самый ранний заход среди всех аккаунтов человека (с учётом того, что Discord отдаёт сейчас)."""
    candidates = []
    known = db.get_first_join(db.get_discord_ids(person_id) or [user.id])
    if known is not None:
        candidates.append(known)
    joined_at = getattr(user, "joined_at", None)
    if joined_at is not None:
        candidates.append(joined_at.timestamp())
    return min(candidates) if candidates else None


def resolve_channels(db, guild, rows: list[tuple[int, int]]) -> list[tuple[str, int]]:
    """
    (ID канала, сколько) → (название, сколько) от большего. Удалённые каналы пропускаются:
    названия уже не узнать. Личные войсы — все войсы владельца одной строкой «Войс Имя»
    (каждый заход — новый канал с новым ID).
    """
    voices = db.get_personal_voice_owners([channel_id for channel_id, _ in rows])
    entries: dict = {}

    for channel_id, amount in rows:
        voice = voices.get(channel_id)
        if voice is not None:
            key = ("voice", voice["user_id"])
            name = db.personal_voice_title(voice["user_id"], guild)
        else:
            channel = guild.get_channel(channel_id) if guild else None
            if channel is None:
                continue
            key, name = channel_id, channel.name
        entries.setdefault(key, [name, 0])[1] += amount

    return sorted(((name, amount) for name, amount in entries.values()), key=lambda item: item[1], reverse=True)


def pick_top_channels(text: list, voice: list, rows: int = TOP_CHANNEL_ROWS) -> list[tuple[str, str, int]]:
    """По половине строк текстовым и голосовым; не хватает одних — добираются другими."""
    half = rows // 2
    take_text = min(len(text), max(half, rows - len(voice)))
    take_voice = min(len(voice), rows - take_text)
    return [("text", name, amount) for name, amount in text[:take_text]] + \
           [("voice", name, amount) for name, amount in voice[:take_voice]]


def window(now: float, days: int) -> tuple[int, int]:
    """[since, until) по часам журнала: последние days×24 часа, включая текущий."""
    until = int(now) // HOUR * HOUR + HOUR
    return until - days * 24 * HOUR, until


def collect_days(db, person_id: int, now: float) -> list[dict]:
    """
    По московским суткам за LOOKBACK_DAYS дней: [{'day': начало суток, 'messages': n, 'voice': минут}].
    Дни до начала учёта на сервере — None, а не 0: «не считали» ≠ «не писал» (на графике их нет).
    """
    first_day = moscow_day_start(now) - (LOOKBACK_DAYS - 1) * DAY
    until = int(now) // HOUR * HOUR + HOUR
    series = {}
    for kind, key in (("messages", "messages"), ("voice_presence", "voice")):
        start = db.get_activity_start(kind)
        series[key] = (
            db.get_activity_by_day(person_id, kind, first_day, until),
            moscow_day_start(start) if start is not None else None,
        )

    days = []
    for n in range(LOOKBACK_DAYS):
        day = first_day + n * DAY
        date = datetime.fromtimestamp(day, MOSCOW).strftime("%Y-%m-%d")
        entry = {"day": day}
        for key, (by_day, start_day) in series.items():
            entry[key] = by_day.get(date, 0) if start_day is not None and day >= start_day else None
        days.append(entry)
    return days


def data_notes(db, now: float) -> list[str]:
    """«войс — с 14 нояб.»: если учёт начался позже начала периода, цифры за 30 дней неполные."""
    first_day = moscow_day_start(now) - (LOOKBACK_DAYS - 1) * DAY
    notes = []
    for kind in KINDS:
        start = db.get_activity_start(kind)
        if start is None:
            notes.append(f"{KIND_LABELS[kind]} — с обновления бота")
        elif start > first_day:
            notes.append(f"{KIND_LABELS[kind]} — с {format_short_date(start)}")
    return notes


def collect_overview(db, guild, user, person_id: int, places: list[tuple[str, int | None]],
                     now: float | None = None) -> dict:
    """
    Главная страница: заход, места, цифры за 1/7/30 дней, топ каналов, мини-график.
    places — строки плашки «Место на сервере»: (подпись, место или None); их даёт Cog
    (места в таблицах лидеров кармы — у модуля кармы).
    """
    now = time.time() if now is None else now
    month = window(now, LOOKBACK_DAYS)

    totals = {
        kind: {days: db.get_activity_total(person_id, kind, *window(now, days)) for days in PERIODS}
        for kind in KINDS
    }

    text = resolve_channels(db, guild, db.get_top_channels(person_id, "messages", *month))
    voice = resolve_channels(db, guild, db.get_top_channels(person_id, "voice_presence", *month))

    return {
        "join_line": join_line(first_join(db, person_id, user), now),
        "totals": totals,
        "places": places,
        "channels": pick_top_channels(text, voice),
        "days": collect_days(db, person_id, now),
        "notes": data_notes(db, now),
    }


def collect_chart(db, person_id: int, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    days = collect_days(db, person_id, now)
    return {
        "days": days,
        "messages": sum(day["messages"] or 0 for day in days),
        "voice": sum(day["voice"] or 0 for day in days),
        "notes": data_notes(db, now),
    }


def collect_channels(db, guild, person_id: int, period: str, now: float | None = None) -> dict:
    """
    Страница «Топ каналов»: топ-5 текстовых (по сообщениям) и топ-5 голосовых (по времени)
    за выбранный период — скользящий, по часам, как цифры на главной.
    """
    now = time.time() if now is None else now
    if period not in CHANNEL_PERIODS:
        period = DEFAULT_CHANNEL_PERIOD
    days, _, title = CHANNEL_PERIODS[period]
    since, until = window(now, days) if days else (0, window(now, 1)[1])

    start = db.get_channel_activity_start()
    if start is None:
        note = "учёт — с обновления бота"
    elif days is None or start > since:
        note = f"учёт с {format_short_date(start)}"
    else:
        note = ""

    return {
        "period": period,
        "title": title,
        "note": note,
        "text": resolve_channels(db, guild, db.get_top_channels(person_id, "messages", since, until))[:CHANNELS_ON_PAGE],
        "voice": resolve_channels(db, guild, db.get_top_channels(person_id, "voice_presence", since, until))[:CHANNELS_ON_PAGE],
    }


def events_page(summary: dict) -> dict:
    """«Ивенты»: данные модуля кармы (KarmaSistem.event_awards_summary) → строки для картинки."""
    return {
        "total": summary["total"],
        "count": summary["count"],
        "awards": [
            (award["reason"], award["amount"], format_numeric_date(award["created_at"]), award["id"])
            for award in summary["awards"]
        ],
    }


def person_name(db, guild, person_id: int) -> str:
    """Имя человека по основному аккаунту: на сервере — как сейчас; ушёл — прозвище из базы."""
    user = db.get_user(person_id)
    member = guild.get_member(user.discord_id) if guild and user else None
    if member is not None:
        return member.display_name
    if user is not None and user.alias:
        return user.alias
    return "ушедший"


def has_voices_page(db, person_id: int) -> bool:
    """Страница «Личные войсы» — только если человек где-то сидел или у него кто-то сидел."""
    return bool(db.get_personal_voice_hosts(person_id) or db.get_personal_voice_visitors(person_id))


def collect_voices(db, guild, person_id: int) -> dict:
    """
    Личные войсы за всё время: в чьих войсах человек сидел (свой — отдельно) и кто сидел у него.
    """
    hosts = db.get_personal_voice_hosts(person_id)
    visitors = db.get_personal_voice_visitors(person_id)
    own = next((minutes for owner, minutes in hosts if owner == person_id), 0)

    return {
        "own": own,
        "hosts": [
            (db.personal_voice_title(owner, guild), minutes)
            for owner, minutes in hosts if owner != person_id
        ][:TOP_VOICE_PEOPLE],
        "guests": [
            (person_name(db, guild, visitor), minutes)
            for visitor, minutes in visitors if visitor != person_id
        ][:TOP_VOICE_PEOPLE],
    }
