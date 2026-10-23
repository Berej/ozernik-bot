"""
Журнал активности — общая таблица activity в utilities.Database.

По часам: сколько сообщений и минут в голосе. Модуль кармы копит активность в памяти
и пишет пачкой раз в минуту (в голосовом цикле) — запись на каждое сообщение тормозила бы бота.
"""

import pytest

import utilities
from conftest import FakeChannel, add_ozernik, make_message
import modules.karma_sistem.karma_sistem as karma_module

HOUR = 3600
# 2026-10-23 00:00 МСК (= 2026-10-22 21:00 UTC)
OCT_23_MSK = 1_792_702_800


@pytest.fixture
def fixed_hour(monkeypatch):
    """Подменяет «текущий час» там, где он используется (в модуле кармы)."""
    state = {"hour": OCT_23_MSK + 10 * HOUR}
    monkeypatch.setattr(karma_module, "current_hour", lambda: state["hour"])
    return state


class TestActivityDatabase:
    def test_current_hour_is_hour_start(self, monkeypatch):
        monkeypatch.setattr(utilities.time, "time", lambda: OCT_23_MSK + 5 * HOUR + 1234.5)
        assert utilities.current_hour() == OCT_23_MSK + 5 * HOUR

    def test_add_and_get(self, env):
        me = add_ozernik(env, 1)

        env.db.add_activity_many([(me, OCT_23_MSK, "messages", 3), (me, OCT_23_MSK, "messages", 2),
                                  (me, OCT_23_MSK + HOUR, "messages", 1), (me, OCT_23_MSK, "voice_minutes", 40)])

        assert env.db.get_activity(me, "messages", OCT_23_MSK, OCT_23_MSK + 24 * HOUR) == [
            (OCT_23_MSK, 5), (OCT_23_MSK + HOUR, 1),
        ]
        assert env.db.get_activity(me, "voice_minutes", OCT_23_MSK, OCT_23_MSK + HOUR) == [(OCT_23_MSK, 40)]

    def test_by_day_uses_moscow_days(self, env):
        """23:30 МСК — ещё 23 октября, 00:30 МСК — уже 24-е (хотя по UTC оба часа — 23-е)."""
        me = add_ozernik(env, 1)
        env.db.add_activity_many([
            (me, OCT_23_MSK + 23 * HOUR, "messages", 4),
            (me, OCT_23_MSK + 24 * HOUR, "messages", 6),
        ])

        by_day = env.db.get_activity_by_day(me, "messages", OCT_23_MSK, OCT_23_MSK + 48 * HOUR)

        assert by_day == {"2026-10-23": 4, "2026-10-24": 6}

    def test_free_kinds_for_other_systems(self, env):
        """Вид активности — свободная строка: будущие системы пишут свои без изменения таблицы."""
        me = add_ozernik(env, 1)
        env.db.add_activity_many([(me, OCT_23_MSK, "mafia_games", 2)])
        assert env.db.get_activity(me, "mafia_games", 0, OCT_23_MSK + HOUR) == [(OCT_23_MSK, 2)]

    def test_link_twink_merges_activity(self, env):
        main, twink = add_ozernik(env, 1), add_ozernik(env, 11)
        env.db.add_activity_many([(main, OCT_23_MSK, "messages", 2), (twink, OCT_23_MSK, "messages", 3),
                                  (twink, OCT_23_MSK + HOUR, "voice_minutes", 10)])

        env.db.link_twink(main, 11)

        assert env.db.get_activity(main, "messages", 0, OCT_23_MSK + 2 * HOUR) == [(OCT_23_MSK, 5)]
        assert env.db.get_activity(main, "voice_minutes", 0, OCT_23_MSK + 2 * HOUR) == [(OCT_23_MSK + HOUR, 10)]

    def test_deleted_user_activity_removed(self, env):
        me = add_ozernik(env, 1)
        env.db.add_activity_many([(me, OCT_23_MSK, "messages", 1)])

        env.db.delete_user(me)

        assert env.db.fetchall("SELECT * FROM activity") == []


def messages_of(env, discord_id):
    ozernik = env.db.get_user_by_discord_id(discord_id)
    return env.db.get_activity(ozernik.id, "messages", 0, 2**40) if ozernik else []


class TestActivityCollection:
    async def test_every_message_counted_even_on_cooldown(self, env, fixed_hour):
        member = env.guild.add_member(1)
        for _ in range(3):  # карма — только за первое (задержка), активность — за все
            await env.cog.on_message(make_message(member, FakeChannel(3000), env.guild))

        assert messages_of(env, 1) == []  # пока только в памяти
        env.cog.flush_activity()

        assert messages_of(env, 1) == [(fixed_hour["hour"], 3)]
        assert env.db.get_karma(env.db.get_user_by_discord_id(1).id).karma == 1

    @pytest.mark.parametrize("case", ["blocked_channel", "blocked_user", "shortcut", "bot"])
    async def test_not_counted(self, env, fixed_hour, case):
        member = env.guild.add_member(1, bot=case == "bot")
        content = ",lb" if case == "shortcut" else "привет"
        if case == "blocked_channel":
            env.data.blocked_channels_id = [3000]
        if case == "blocked_user":
            env.data.blocked_users_id = [1]

        await env.cog.on_message(make_message(member, FakeChannel(3000), env.guild, content=content))
        env.cog.flush_activity()

        assert messages_of(env, 1) == []

    async def test_hours_kept_apart(self, env, fixed_hour):
        member = env.guild.add_member(1)
        await env.cog.on_message(make_message(member, FakeChannel(3000), env.guild))
        fixed_hour["hour"] += HOUR
        env.cog.delays.clear()
        await env.cog.on_message(make_message(member, FakeChannel(3000), env.guild))

        env.cog.flush_activity()

        start = OCT_23_MSK + 10 * HOUR
        assert messages_of(env, 1) == [(start, 1), (start + HOUR, 1)]

    async def test_voice_minutes_flushed_by_voice_loop(self, env, fixed_hour):
        a, b, alone = env.guild.add_member(1), env.guild.add_member(2), env.guild.add_member(3)
        env.guild.voice_channels.append(FakeChannel(6000, members=[a, b]))
        env.guild.voice_channels.append(FakeChannel(6001, members=[alone]))

        await env.cog.voice_karma_check()  # сама сбрасывает накопленное в базу

        me = env.db.get_user_by_discord_id(1).id
        assert env.db.get_activity(me, "voice_minutes", 0, 2**40) == [(fixed_hour["hour"], 1)]
        lonely = env.db.get_user_by_discord_id(3)
        assert lonely is None or env.db.get_activity(lonely.id, "voice_minutes", 0, 2**40) == []

    async def test_twink_activity_goes_to_main(self, env, fixed_hour):
        main = env.guild.add_member(1)
        twink = env.guild.add_member(11)
        env.db.link_twink(env.bot.db_ensure_user(main).id, 11)

        await env.cog.on_message(make_message(twink, FakeChannel(3000), env.guild))
        env.cog.flush_activity()

        assert messages_of(env, 1) == [(fixed_hour["hour"], 1)]

    async def test_failed_write_kept_for_retry(self, env, fixed_hour):
        member = env.guild.add_member(1)
        await env.cog.on_message(make_message(member, FakeChannel(3000), env.guild))

        def broken(rows):
            raise RuntimeError("database is locked")

        # Не monkeypatch.undo(): он откатил бы и подмену базы из фикстуры env.
        env.db.add_activity_many = broken
        env.cog.flush_activity()
        assert env.cog.activity  # не потеряно

        del env.db.add_activity_many  # снова метод класса
        env.cog.flush_activity()

        assert messages_of(env, 1) == [(fixed_hour["hour"], 1)]
        assert env.cog.activity == {}

    async def test_flushed_on_unload(self, env, fixed_hour):
        member = env.guild.add_member(1)
        await env.cog.on_message(make_message(member, FakeChannel(3000), env.guild))

        await env.cog.cog_unload()

        assert messages_of(env, 1) == [(fixed_hour["hour"], 1)]
