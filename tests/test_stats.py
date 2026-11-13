"""
Плашка статистики *i (решения Alium 2026-11-14): сбор сообщений и войса, первый заход,
данные страниц, картинки, кнопки. Полная замена страниц статистики ,rs: «Топ каналов»
(вместо «Где пишу/сижу»), «Ивенты» и «Другие обличья» переехали сюда.
"""

from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from PIL import Image

import modules.stats.stats as stats
from modules.stats import _stats_cards as cards
from modules.stats import _stats_data as data
from conftest import FakeChannel, make_interaction, make_message

HOUR = 3600
DAY = 86400
# 2026-11-14 00:00 МСК (= 2026-11-13 21:00 UTC)
NOV_14_MSK = 1_794_603_600
NOW = NOV_14_MSK + 15 * HOUR + 20 * 60   # 14 ноября, 15:20 МСК


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(stats, "now", lambda: state["now"])
    return state


@pytest.fixture
def stats_env(env, clock):
    env.guild.channels = {}
    env.guild.get_channel = env.guild.channels.get
    env.bot.get_cog = lambda name: None
    stats._avatar_cache.clear()
    stats._fetched_users.clear()
    return SimpleNamespace(db=env.db, guild=env.guild, bot=env.bot, cog=stats.Stats(env.bot), clock=clock, karma_env=env)


def person(stats_env, discord_id):
    return stats_env.db.get_user_by_discord_id(discord_id).id


def channel(stats_env, channel_id, name, parent_id=None):
    ch = FakeChannel(channel_id, name=name)
    ch.parent_id = parent_id
    stats_env.guild.channels[channel_id] = ch
    return ch


def joined(member, timestamp):
    member.joined_at = datetime.fromtimestamp(timestamp, timezone.utc)
    return member


# ---------- СБОР: СООБЩЕНИЯ ----------

class TestMessages:
    async def test_messages_counted_by_hour_and_channel(self, stats_env):
        me = stats_env.guild.add_member(1)
        chat = channel(stats_env, 3000, "основной")
        for _ in range(3):
            await stats_env.cog.on_message(make_message(me, chat, stats_env.guild))

        assert stats_env.db.get_activity_total(person(stats_env, 1), "messages", 0, 2**40) == 0  # пока в памяти
        stats_env.cog.flush()

        me_id = person(stats_env, 1)
        assert stats_env.db.get_activity(me_id, "messages", 0, 2**40) == [(NOW // HOUR * HOUR, 3)]
        assert stats_env.db.get_top_channels(me_id, "messages", NOV_14_MSK) == [(3000, 3)]

    async def test_thread_counted_to_its_channel(self, stats_env):
        me = stats_env.guild.add_member(1)
        thread = channel(stats_env, 3500, "ветка", parent_id=3000)

        await stats_env.cog.on_message(make_message(me, thread, stats_env.guild))
        stats_env.cog.flush()

        assert stats_env.db.get_top_channels(person(stats_env, 1), "messages", 0) == [(3000, 1)]

    async def test_karma_blocked_channel_and_user_still_counted(self, stats_env):
        """Решение Alium 2026-11-14: в плашку — всё, не только то, что даёт карму."""
        stats_env.karma_env.data.blocked_channels_id = [3000]
        stats_env.karma_env.data.blocked_users_id = [1]
        me = stats_env.guild.add_member(1)

        await stats_env.cog.on_message(make_message(me, channel(stats_env, 3000, "флуд"), stats_env.guild))
        stats_env.cog.flush()

        assert stats_env.db.get_activity_total(person(stats_env, 1), "messages", 0, 2**40) == 1

    @pytest.mark.parametrize("case", ["bot", "karma_shortcut", "other_guild"])
    async def test_not_counted(self, stats_env, case):
        stats_env.bot.get_cog = lambda name: SimpleNamespace(SHORTCUTS={",lb": "leaderboard"}) if name == "KarmaSistem" else None
        member = stats_env.guild.add_member(1, bot=case == "bot")
        message = make_message(member, channel(stats_env, 3000, "чат"), stats_env.guild,
                               content=",LB" if case == "karma_shortcut" else "привет")
        if case == "other_guild":
            message.guild = SimpleNamespace(id=4242)

        await stats_env.cog.on_message(message)
        stats_env.cog.flush()

        assert stats_env.db.fetchall("SELECT * FROM activity") == []

    async def test_twink_counted_to_main(self, stats_env):
        main = stats_env.guild.add_member(1)
        twink = stats_env.guild.add_member(11)
        stats_env.db.link_twink(stats_env.bot.db_ensure_user(main).id, 11)

        await stats_env.cog.on_message(make_message(twink, channel(stats_env, 3000, "чат"), stats_env.guild))
        stats_env.cog.flush()

        assert stats_env.db.get_activity_total(person(stats_env, 1), "messages", 0, 2**40) == 1

    async def test_failed_write_kept_for_retry(self, stats_env):
        me = stats_env.guild.add_member(1)
        await stats_env.cog.on_message(make_message(me, channel(stats_env, 3000, "чат"), stats_env.guild))

        def broken(rows):
            raise RuntimeError("database is locked")

        stats_env.db.add_channel_activity_many = broken
        stats_env.cog.flush()
        assert stats_env.cog.pending_channels and not stats_env.cog.pending_hours  # часы записались, каналы ждут

        del stats_env.db.add_channel_activity_many
        stats_env.cog.flush()

        assert stats_env.db.get_top_channels(person(stats_env, 1), "messages", 0) == [(3000, 1)]
        assert stats_env.db.get_activity_total(person(stats_env, 1), "messages", 0, 2**40) == 1  # не задвоилось

    async def test_flushed_on_unload(self, stats_env):
        me = stats_env.guild.add_member(1)
        await stats_env.cog.on_message(make_message(me, channel(stats_env, 3000, "чат"), stats_env.guild))
        stats_env.cog.stats_tick.cancel = MagicMock()

        await stats_env.cog.cog_unload()

        assert stats_env.db.get_activity_total(person(stats_env, 1), "messages", 0, 2**40) == 1


# ---------- СБОР: ВОЙС И ЗАХОДЫ ----------

class TestVoiceAndJoins:
    async def test_voice_everything_but_afk(self, stats_env):
        alone, muted, afk_one = (stats_env.guild.add_member(n) for n in (1, 2, 3))
        muted.voice.self_mute = True
        music = stats_env.guild.add_member(60, bot=True)
        afk = FakeChannel(6999, members=[afk_one])
        stats_env.guild.afk_channel = afk
        stats_env.guild.voice_channels += [FakeChannel(6000, members=[alone, music]), FakeChannel(6001, members=[muted]), afk]

        await stats_env.cog.stats_tick()

        totals = {n: stats_env.db.get_activity_total(person(stats_env, n), "voice_presence", 0, 2**40)
                  for n in (1, 2)}
        assert totals == {1: 1, 2: 1}  # один и заглушённый — считаются
        afk_person = stats_env.db.get_user_by_discord_id(3)
        assert afk_person is None or stats_env.db.get_activity_total(afk_person.id, "voice_presence", 0, 2**40) == 0
        assert stats_env.db.get_user_by_discord_id(60) is None
        assert stats_env.db.get_top_channels(person(stats_env, 1), "voice_presence", 0) == [(6000, 1)]

    async def test_main_and_twink_in_voice_one_minute(self, stats_env):
        main = stats_env.guild.add_member(1)
        twink = stats_env.guild.add_member(11)
        stats_env.db.link_twink(stats_env.bot.db_ensure_user(main).id, 11)
        stats_env.guild.voice_channels += [FakeChannel(6000, members=[main]), FakeChannel(6001, members=[twink])]

        await stats_env.cog.stats_tick()

        assert stats_env.db.get_activity_total(person(stats_env, 1), "voice_presence", 0, 2**40) == 1

    async def test_tick_error_does_not_stop_loop(self, stats_env, monkeypatch, capsys):
        monkeypatch.setattr(stats_env.cog, "count_voice", MagicMock(side_effect=RuntimeError("сбой")))

        await stats_env.cog.stats_tick()

        assert "сбой" in capsys.readouterr().out

    async def test_first_join_kept_on_rejoin(self, stats_env):
        me = joined(stats_env.guild.add_member(1), NOW - 400 * DAY)
        await stats_env.cog.stats_tick()  # первый проход — заходы всех на сервере

        joined(me, NOW - 2 * DAY)  # вышел и вернулся
        await stats_env.cog.on_member_join(me)

        assert stats_env.db.get_first_join([1]) == NOW - 400 * DAY

    def test_first_join_across_twinks(self, stats_env):
        main = stats_env.guild.add_member(1)
        main_id = stats_env.bot.db_ensure_user(main).id
        stats_env.db.link_twink(main_id, 11)
        stats_env.db.note_member_joins([(1, NOW - 10 * DAY), (11, NOW - 300 * DAY)])

        assert data.first_join(stats_env.db, main_id, main) == NOW - 300 * DAY


# ---------- БАЗА ----------

class TestDatabase:
    def test_totals(self, stats_env):
        db = stats_env.db
        a = db.add_user(discord_id=1)
        db.add_activity_many([(a, NOW - HOUR, "messages", 5), (a, NOW - 40 * DAY, "messages", 100)])

        assert db.get_activity_total(a, "messages", NOW - 30 * DAY, NOW + HOUR) == 5
        assert db.get_activity_total(a, "voice_presence", 0, 2**40) == 0

    def test_top_channels_period(self, stats_env):
        db = stats_env.db
        me = db.add_user(discord_id=1)
        db.add_channel_activity_many([(me, 3000, NOV_14_MSK, "messages", 2), (me, 3001, NOV_14_MSK - DAY, "messages", 7),
                                      (me, 3000, NOV_14_MSK - 40 * DAY, "messages", 50)])

        assert db.get_top_channels(me, "messages", NOV_14_MSK - 29 * DAY) == [(3001, 7), (3000, 2)]
        assert db.get_top_channels(me, "messages", NOV_14_MSK - DAY, NOV_14_MSK) == [(3001, 7)]
        assert db.get_top_channels(me, "messages") == [(3000, 52), (3001, 7)]  # за всё время
        assert db.get_channel_activity_start() == NOV_14_MSK - 40 * DAY

    def test_link_twink_merges_channel_activity(self, stats_env):
        db = stats_env.db
        main = db.add_user(discord_id=1)
        twink = db.add_user(discord_id=11)
        db.add_channel_activity_many([(main, 3000, NOV_14_MSK, "messages", 2), (twink, 3000, NOV_14_MSK, "messages", 3)])

        db.link_twink(main, 11)

        assert db.get_top_channels(main, "messages", 0) == [(3000, 5)]

    def test_moscow_day_start(self):
        import utilities
        assert utilities.moscow_day_start(NOW) == NOV_14_MSK
        assert utilities.moscow_day_start(NOV_14_MSK - 1) == NOV_14_MSK - DAY


# ---------- ДАННЫЕ СТРАНИЦ ----------

class TestData:
    @pytest.mark.parametrize(("days_ago", "text"), [
        (0.5, "меньше дня"), (1, "1 день"), (3, "3 дня"), (25, "25 дней"),
        (45, "1 мес."), (365, "1 год"), (427, "1 год 2 мес."), (800, "2 года 2 мес."), (1900, "5 лет 2 мес."),
    ])
    def test_tenure(self, days_ago, text):
        assert data.format_tenure(NOW - days_ago * DAY, NOW) == text

    def test_join_line(self):
        assert data.join_line(NOW - 427 * DAY, NOW) == "На сервере с 13 сентября 2025 · 1 год 2 мес."
        assert data.join_line(None, NOW) == "Дата захода неизвестна"

    @pytest.mark.parametrize(("n", "text"), [(149, "149"), (1020, "1 020"), (65_400, "65,4 тыс."), (12_000, "12 тыс."),
                                            (123_456, "123 тыс.")])
    def test_format_count(self, n, text):
        assert data.format_count(n) == text

    @pytest.mark.parametrize(("minutes", "text"), [(45, "45 мин"), (102, "1,7 ч"), (600, "10 ч"), (28_560, "476 ч")])
    def test_format_hours(self, minutes, text):
        assert data.format_hours(minutes) == text

    def test_pick_top_channels(self):
        text = [("a", 9), ("b", 8), ("c", 7)]
        assert [n for _, n, _ in data.pick_top_channels(text, [("v", 5), ("w", 4), ("x", 3)])] == ["a", "b", "v", "w"]
        assert [n for _, n, _ in data.pick_top_channels(text, [("v", 5)])] == ["a", "b", "c", "v"]
        assert [n for _, n, _ in data.pick_top_channels([], [("v", 5), ("w", 4)])] == ["v", "w"]

    def test_days_before_start_are_none(self, stats_env):
        db = stats_env.db
        me = db.add_user(discord_id=1)
        db.add_activity_many([(me, NOV_14_MSK - 5 * DAY + 10 * HOUR, "messages", 4),
                              (me, NOV_14_MSK + 10 * HOUR, "voice_presence", 30)])

        days = data.collect_days(db, me, NOW)

        assert len(days) == 30 and days[-1]["day"] == NOV_14_MSK
        assert days[-7]["messages"] is None and days[-6]["messages"] == 4 and days[-5]["messages"] == 0
        assert days[-2]["voice"] is None and days[-1]["voice"] == 30
        assert data.data_notes(db, NOW) == ["сообщения — с 9 нояб.", "войс — с 14 нояб."]

    def test_overview(self, stats_env):
        db, guild = stats_env.db, stats_env.guild
        me_member = joined(guild.add_member(1), NOW - 427 * DAY)
        me = stats_env.bot.db_ensure_user(me_member).id
        host = db.add_user(discord_id=2)
        guild.add_member(2)
        channel(stats_env, 3000, "основной")
        db.add_personal_voice(7001, host, None)
        db.add_personal_voice(7002, host, None)
        db.add_activity_many([(me, NOW - HOUR, "messages", 7), (me, NOW - 3 * DAY, "messages", 10),
                              (me, NOW - HOUR, "voice_presence", 90)])
        db.add_channel_activity_many([(me, 3000, NOV_14_MSK, "messages", 17), (me, 9999, NOV_14_MSK, "messages", 99),
                                      (me, 7001, NOV_14_MSK, "voice_presence", 50), (me, 7002, NOV_14_MSK, "voice_presence", 40)])

        page = data.collect_overview(db, guild, me_member, me, [("Карма", 4)], NOW)

        assert page["join_line"].startswith("На сервере с 13 сентября 2025")
        assert page["totals"]["messages"] == {1: 7, 7: 17, 30: 17}
        assert page["places"] == [("Карма", 4)]
        # Удалённый обычный канал (9999) не виден; оба войса Габа — одной строкой
        assert page["channels"] == [("text", "основной", 17), ("voice", "Войс User 2", 90)]

    def test_channels_page_periods(self, stats_env):
        db, guild = stats_env.db, stats_env.guild
        me = db.add_user(discord_id=1)
        host = db.add_user(discord_id=2)
        guild.add_member(2)
        for n in range(7):
            channel(stats_env, 3000 + n, f"канал{n}")
        db.add_personal_voice(7001, host, None)
        db.add_personal_voice(7002, host, None)
        hour = NOW // HOUR * HOUR
        rows = [(me, 3000 + n, hour - 2 * HOUR, "messages", 10 - n) for n in range(7)]
        rows += [(me, 3000, hour - 3 * DAY, "messages", 100),          # неделю назад — нет, за всё время — да
                 (me, 9999, hour, "messages", 500),                    # удалённый канал — не виден
                 (me, 7001, hour - 30 * HOUR, "voice_presence", 50),   # больше суток назад
                 (me, 7002, hour, "voice_presence", 40)]               # два войса Габа — одной строкой
        db.add_channel_activity_many(rows)

        day = data.collect_channels(db, guild, me, "1", NOW)
        week = data.collect_channels(db, guild, me, "7", NOW)
        everything = data.collect_channels(db, guild, me, "all", NOW)

        assert [name for name, _ in day["text"]] == [f"канал{n}" for n in range(5)]  # топ-5
        assert day["voice"] == [("Войс User 2", 40)]
        assert week["voice"] == [("Войс User 2", 90)] and week["text"][0] == ("канал0", 110)
        assert everything["title"] == "за всё время" and everything["note"] == "учёт с 11 нояб."
        assert day["note"] == ""  # учёт идёт дольше суток — пометка не нужна
        assert data.collect_channels(db, guild, me, "мусор", NOW)["period"] == "30"

    def test_events_page(self):
        summary = {"total": 70, "count": 7, "awards": [{"id": 7, "reason": "Квиз", "amount": 10, "created_at": NOW}]}

        assert data.events_page(summary) == {"total": 70, "count": 7, "awards": [("Квиз", 10, "14.11.2026", 7)]}

    def test_voices(self, stats_env):
        db, guild = stats_env.db, stats_env.guild
        me, friend, gone = (db.add_user(discord_id=n) for n in (1, 2, 3))
        guild.add_member(1)
        guild.add_member(2)
        db.add_personal_voice_minutes_many([(me, me, 60), (friend, me, 30), (me, friend, 20), (me, gone, 5)])

        page = data.collect_voices(db, guild, me)

        assert page == {"own": 60, "hosts": [("Войс User 2", 30)], "guests": [("User 2", 20), ("ушедший", 5)]}
        assert data.has_voices_page(db, me) and not data.has_voices_page(db, db.add_user(discord_id=4))


# ---------- КАРТИНКИ ----------

AVATAR = Image.new("RGBA", (200, 200), (90, 140, 200, 255))


def empty_days():
    return [{"day": NOV_14_MSK - (29 - n) * DAY, "messages": None, "voice": None} for n in range(30)]


class TestCards:
    def test_overview_renders(self):
        days = empty_days()
        days[-1].update(messages=5, voice=None)
        page = {"join_line": "На сервере с 1 мая 2024 · 2 года", "notes": ["войс — с 14 нояб."],
                "totals": {"messages": {1: 1, 7: 2, 30: 3}, "voice_presence": {1: 0, 7: 0, 30: 0}},
                "places": [("Карма", None), ("Карма за неделю", 4), ("Третья", 120)],
                "channels": [("text", "💬・чат", 3), ("voice", "x" * 80, 61)], "days": days}

        image = Image.open(cards.render_overview(AVATAR.copy(), "Имя 🐉", "#FFFFFF", page))

        assert image.size == (cards.WIDTH, cards.OVERVIEW_HEIGHT)

    def test_overview_empty(self):
        page = {"join_line": "Дата захода неизвестна", "notes": [],
                "totals": {kind: {d: 0 for d in data.PERIODS} for kind in data.KINDS},
                "places": [], "channels": [], "days": empty_days()}

        assert Image.open(cards.render_overview(AVATAR.copy(), "Имя", "#FFFFFF", page)).size[0] == cards.WIDTH

    @pytest.mark.parametrize("filled", [False, True])
    def test_chart_renders(self, filled):
        days = empty_days()
        if filled:
            for n, day in enumerate(days):
                day.update(messages=n * 7, voice=n * 13)
        page = {"days": days, "messages": 0, "voice": 0, "notes": []}

        assert Image.open(cards.render_chart(AVATAR.copy(), "Имя", "#FFFFFF", page)).size == (cards.WIDTH, cards.CHART_HEIGHT)

    def test_voices_renders(self):
        page = {"own": 0, "hosts": [], "guests": [("🐉", 5)]}
        assert Image.open(cards.render_voices(AVATAR.copy(), "Имя", "#FFFFFF", page)).size == (cards.WIDTH, cards.CHART_HEIGHT)

    @pytest.mark.parametrize("filled", [False, True])
    def test_channels_renders(self, filled):
        rows = [("💬・очень-длинное-название-канала-" * 3, 1500), ("чат", 3)] if filled else []
        page = {"period": "all", "title": "за всё время", "note": "учёт с 14 нояб.", "text": rows,
                "voice": [("Войс Габ", 900)] if filled else []}

        assert Image.open(cards.render_channels(AVATAR.copy(), "Имя", "#FFFFFF", page)).size == (cards.WIDTH, cards.CHART_HEIGHT)

    def test_events_renders(self):
        page = {"total": 155, "count": 2, "awards": [("🎉 Квиз", 150, "23.10.2026", 1), ("x" * 120, 5, "01.01.2026", 2)]}
        assert Image.open(cards.render_events(AVATAR.copy(), "Имя", "#FFFFFF", page)).size == (cards.WIDTH, cards.CHART_HEIGHT)

    @pytest.mark.parametrize("count", [2, 8])
    def test_guises_renders(self, count):
        accounts = [{"name": f"Аккаунт {n} с длинным-длинным именем", "avatar": AVATAR.copy() if n % 3 else None,
                     "main": n == 0, "since": "23.10.2026" if n else ""} for n in range(count)]
        page = {"accounts": accounts, "total": count + 3}
        assert Image.open(cards.render_guises(AVATAR.copy(), "Имя", "#FFFFFF", page)).size == (cards.WIDTH, cards.CHART_HEIGHT)

    @pytest.mark.parametrize(("value", "whole", "top"), [(250, True, 300), (3, True, 4), (0, True, 4), (9.7, False, 10),
                                                         (1.2, False, 1.2), (5, True, 8)])
    def test_nice_top(self, value, whole, top):
        assert cards.nice_top(value, whole) == pytest.approx(top)


# ---------- ПЛАШКА: КОМАНДЫ И КНОПКИ ----------

def label(item):
    """Подпись кнопки: у DynamicItem она у вложенной кнопки (.item)."""
    return getattr(item, "label", None) or item.item.label


def page_buttons(view):
    """Кнопки страниц (с номерами) — без кнопок периода."""
    return [item for item in view.children if label(item).isdigit()]


def current(view):
    """Номер открытой страницы — подсвеченная неактивная кнопка с номером."""
    return [label(item) for item in page_buttons(view) if getattr(item, "disabled", False)]


class TestCard:
    async def test_shortcut_with_mention(self, stats_env):
        me = stats_env.guild.add_member(1)
        other = joined(stats_env.guild.add_member(2), NOW - 50 * DAY)
        message = make_message(me, channel(stats_env, 3000, "чат"), stats_env.guild, content="*I <@2>")
        message.mentions = [other]

        await stats_env.cog.on_message(message)
        stats_env.cog.flush()

        kwargs = message.reply.call_args.kwargs
        assert kwargs["file"].filename == "stats.png"
        view = kwargs["view"]
        assert [label(item) for item in view.children] == ["1", "2", "3"]  # по кнопке на страницу
        assert current(view) == ["1"]
        # Чья плашка (2), какая страница и кто вызвал (1) — в custom_id
        assert [item.custom_id for item in view.children] == ["stats:i_now:2", "stats:i:2:channels:1", "stats:i:2:chart:1"]
        assert stats_env.db.fetchall("SELECT * FROM activity WHERE kind = 'messages'") == []  # команда — не сообщение
        assert stats_env.db.get_first_join([2]) == NOW - 50 * DAY

    async def test_slash_bot_refused(self, stats_env):
        bot_member = stats_env.guild.add_member(60, bot=True)
        interaction = make_interaction(stats_env.guild.add_member(1), stats_env.guild)

        await stats_env.cog.stats.callback(stats_env.cog, interaction, bot_member)

        assert "У ботов" in interaction.response.send_message.call_args.args[0]

    async def test_places_from_karma_leaderboards(self, stats_env):
        """Места — как в ,lb и ,lbw: у равных по общей карме выше достигший позже, у равных по недельной — с меньшей общей."""
        karma_env = stats_env.karma_env
        stats_env.bot.get_cog = lambda name: karma_env.cog if name == "KarmaSistem" else None
        db = stats_env.db
        a, b, c = (db.add_user(discord_id=n) for n in (1, 2, 3))
        db.add_karma(a, 50, True)
        db.add_karma(b, 30, True)
        db.add_karma(c, 10)
        db.add_karma(c, 40)  # общая 50, как у a, но позже; недельная 0

        top = [ozernik.id for ozernik, _ in db.get_top_karma()]
        weekly = [ozernik.id for ozernik, _ in db.get_top_weekly_karma()]
        for person_id in (a, b):
            places = dict(stats_env.cog.server_places(person_id))
            assert places == {"Карма": top.index(person_id) + 1, "Карма за неделю": weekly.index(person_id) + 1}
        assert stats_env.cog.server_places(c) == [("Карма", 1), ("Карма за неделю", None)]  # за неделю — ноль

    async def test_no_place_with_zero_karma(self, stats_env):
        """Как у ,rs: при нуле кармы (или нуле за неделю) — «—», а не номер в конце таблицы."""
        stats_env.bot.get_cog = lambda name: stats_env.karma_env.cog if name == "KarmaSistem" else None
        db = stats_env.db
        a, b = (db.add_user(discord_id=n) for n in (1, 2))
        db.add_karma(a, 5)  # недельной нет

        assert stats_env.cog.server_places(a) == [("Карма", 1), ("Карма за неделю", None)]
        assert stats_env.cog.server_places(b) == [("Карма", None), ("Карма за неделю", None)]

    async def test_places_without_karma_module(self, stats_env):
        assert stats_env.cog.server_places(stats_env.db.add_user(discord_id=1)) == [("Карма", None), ("Карма за неделю", None)]

    async def test_optional_pages_only_with_data(self, stats_env):
        """Личные войсы, Ивенты, Другие обличья — только когда есть что показать."""
        stats_env.bot.get_cog = lambda name: stats_env.karma_env.cog if name == "KarmaSistem" else None
        me = stats_env.guild.add_member(1)
        me_id = stats_env.bot.db_ensure_user(me).id
        assert stats_env.cog.available_pages(me_id) == ["main", "channels", "chart"]

        stats_env.db.add_personal_voice_minutes_many([(me_id, me_id, 5)])
        stats_env.db.add_event_karma(me_id, 10, "Квиз")
        stats_env.db.link_twink(me_id, 11)

        assert stats_env.cog.available_pages(me_id) == ["main", "channels", "chart", "voices", "events", "guises"]

    async def test_no_events_page_without_karma_module(self, stats_env):
        me_id = stats_env.bot.db_ensure_user(stats_env.guild.add_member(1)).id
        stats_env.db.add_event_karma(me_id, 10, "Квиз")  # награда есть, но карма выключена

        assert "events" not in stats_env.cog.available_pages(me_id)

    async def press(self, stats_env, button, presser_id=5):
        presser = stats_env.guild.get_member(presser_id) or stats_env.guild.add_member(presser_id)
        interaction = make_interaction(presser, stats_env.guild)
        interaction.client = SimpleNamespace(get_cog=lambda name: stats_env.cog if name == "Stats" else None)
        interaction.edit_original_response = AsyncMock()
        await button.callback(interaction)
        self.last_interaction = interaction
        call = interaction.edit_original_response.call_args
        return call.kwargs if call else None

    async def test_button_turns_page(self, stats_env):
        stats_env.guild.add_member(1)

        kwargs = await self.press(stats_env, stats.StatsPageButton(1, "chart", 5))

        assert kwargs["attachments"][0].filename == "stats.png"
        assert current(kwargs["view"]) == ["3"]
        assert "stats:i:1:main:5" in [item.custom_id for item in kwargs["view"].children]  # листает всё тот же вызвавший

    @pytest.mark.parametrize("button", [stats.StatsPageButton(1, "chart", 5), stats.StatsPeriodButton(1, "all", 5)])
    async def test_only_invoker_switches(self, stats_env, button):
        """Решение Alium 2026-11-14: переключать может только вызвавший (даже владелец плашки — нет)."""
        stats_env.guild.add_member(1)

        for presser in (1, 7):
            assert await self.press(stats_env, button, presser_id=presser) is None
            reply = self.last_interaction.response.send_message.call_args
            assert reply.args[0] == stats.NOT_YOURS and reply.kwargs["ephemeral"] is True

    async def test_from_custom_id(self, stats_env):
        page = stats.StatsPageButton(42, "events", 50, label="5")
        period = stats.StatsPeriodButton(42, "7", 50)

        for button, cls in ((page, stats.StatsPageButton), (period, stats.StatsPeriodButton)):
            match = cls.__discord_ui_compiled_template__.fullmatch(button.custom_id)
            restored = await cls.from_custom_id(None, None, match)
            assert (restored.user_id, restored.invoker_id) == (42, 50)
        assert page.custom_id == "stats:i:42:events:50" and period.custom_id == "stats:period:42:7:50"

    async def test_channels_page_has_period_row(self, stats_env):
        stats_env.guild.add_member(1)

        kwargs = await self.press(stats_env, stats.StatsPageButton(1, "channels", 5))

        view = kwargs["view"]
        assert current(view) == ["2"]
        periods = [item for item in view.children if not label(item).isdigit()]
        assert [label(item) for item in periods] == ["Сутки", "Неделя", "30 дней", "Всё время"]
        assert [getattr(item, "disabled", False) for item in periods] == [False, False, True, False]  # 30 дней — выбран
        assert {item.row for item in page_buttons(view)} == {0} and {item.row for item in periods} == {1}

    async def test_period_button(self, stats_env, monkeypatch):
        stats_env.guild.add_member(1)
        seen = {}
        original = stats.collect_channels
        monkeypatch.setattr(stats, "collect_channels", lambda *args: seen.setdefault("period", args[3]) and original(*args))

        kwargs = await self.press(stats_env, stats.StatsPeriodButton(1, "all", 5))

        assert seen["period"] == "all"
        selected = [label(item) for item in kwargs["view"].children
                    if not label(item).isdigit() and getattr(item, "disabled", False)]
        assert selected == ["Всё время"]

    async def test_six_pages_in_two_rows(self, stats_env):
        """В ряду Discord принимает не больше пяти кнопок: шесть страниц — два ряда по три."""
        view = stats.pages_view(1, "channels", list(stats.PAGES), 5, "30")

        assert [(label(item), item.row) for item in page_buttons(view)] == [
            ("1", 0), ("2", 0), ("3", 0), ("4", 1), ("5", 1), ("6", 1)]
        assert {item.row for item in view.children if not label(item).isdigit()} == {2}

    async def test_vanished_page_opens_main(self, stats_env):
        """Кнопка ведёт на страницу, которой у человека уже нет (твинк отвязан) — открывается главная."""
        stats_env.guild.add_member(1)

        kwargs = await self.press(stats_env, stats.StatsPageButton(1, "guises", 5))

        assert current(kwargs["view"]) == ["1"] and len(page_buttons(kwargs["view"])) == 3

    async def test_events_page_from_karma(self, stats_env, monkeypatch):
        stats_env.bot.get_cog = lambda name: stats_env.karma_env.cog if name == "KarmaSistem" else None
        me = stats_env.guild.add_member(1)
        me_id = stats_env.bot.db_ensure_user(me).id
        stats_env.db.add_event_karma(me_id, 150, "Победа в квизе")
        captured = {}
        monkeypatch.setattr(stats, "render_events", lambda *args: captured.update(data=args[3]) or BytesIO(b"png"))

        file, view = await stats_env.cog.build_page(me, "events")

        assert (captured["data"]["total"], captured["data"]["count"]) == (150, 1)
        assert captured["data"]["awards"][0][:2] == ("Победа в квизе", 150)
        assert current(view) == ["4"] and len(page_buttons(view)) == 4

    async def test_guises_page_accounts(self, stats_env, monkeypatch):
        main = stats_env.guild.add_member(1)
        stats_env.guild.add_member(11)
        gone = stats_env.guild.add_member(12)
        main_id = stats_env.bot.db_ensure_user(main).id
        for twink_id in (11, 12, 55):
            stats_env.db.link_twink(main_id, twink_id)
        stats_env.guild.members.remove(gone)  # ушёл — берётся через fetch_user

        async def fetch(user_id):
            if user_id == 12:
                return gone
            raise discord.NotFound(MagicMock(status=404), "Unknown User")  # 55 — аккаунт удалён

        stats_env.bot.fetch_user = AsyncMock(side_effect=fetch)
        captured = {}
        monkeypatch.setattr(stats, "render_guises", lambda *args: captured.update(data=args[3]) or BytesIO(b"png"))

        _, view = await stats_env.cog.build_page(main, "guises")

        accounts = captured["data"]["accounts"]
        assert [(a["name"], a["main"]) for a in accounts] == [("User 1", True), ("User 11", False), ("User 12", False),
                                                             ("ID 55", False)]
        assert accounts[1]["since"] and not accounts[0]["since"]
        assert accounts[3]["avatar"] is None and all(a["avatar"] is not None for a in accounts[:3])
        assert current(view) == ["4"] and len(page_buttons(view)) == 4

    async def test_twink_card_is_persons_card(self, stats_env):
        """Плашка, открытая по твинку, — плашка человека: обличья тоже есть, имя — твинка."""
        main = stats_env.guild.add_member(1)
        twink = stats_env.guild.add_member(11)
        stats_env.db.link_twink(stats_env.bot.db_ensure_user(main).id, 11)

        _, view = await stats_env.cog.build_page(twink)

        assert current(view) == ["1"] and len(page_buttons(view)) == 4
        assert "stats:i:11:guises:11" in [item.custom_id for item in view.children]

    async def test_shortcut_gives_no_karma(self, stats_env):
        """Карма не начисляет за чужие сокращения: спрашивает модуль статистики (Stats.SHORTCUTS)."""
        karma_env = stats_env.karma_env
        stats_env.bot.get_cog = lambda name: stats_env.cog if name == "Stats" else None
        me = stats_env.guild.add_member(1)

        await karma_env.cog.on_message(make_message(me, channel(stats_env, 3000, "чат"), stats_env.guild, content="*i"))

        ozernik = stats_env.db.get_user_by_discord_id(1)
        assert ozernik is None or stats_env.db.get_karma(ozernik.id).karma == 0

        await karma_env.cog.on_message(make_message(me, channel(stats_env, 3000, "чат"), stats_env.guild, content="привет"))
        assert stats_env.db.get_karma(person(stats_env, 1)).karma == 1

    async def test_slash_sends_card(self, stats_env):
        me = stats_env.guild.add_member(1)
        interaction = make_interaction(me, stats_env.guild)

        await stats_env.cog.stats.callback(stats_env.cog, interaction, None)

        interaction.response.defer.assert_awaited_once()
        assert interaction.followup.send.call_args.kwargs["file"].filename == "stats.png"
