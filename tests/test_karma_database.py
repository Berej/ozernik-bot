"""Тесты слоя базы данных, на котором стоит карма: пользователи, карма, кармические связи."""

import sqlite3
from itertools import count

import pytest

from conftest import add_ozernik


@pytest.fixture
def clock(monkeypatch):
    """
    Управляемые часы для времени изменения кармы.

    Подменяем там, где now_ms используется (_db.py делает `from utilities import now_ms` —
    у него своя ссылка на функцию), а не там, где она определена.
    """
    from modules.karma_sistem import _db

    ticks = count(1_000)
    monkeypatch.setattr(_db, "now_ms", lambda: next(ticks))


# ---------- ПОЛЬЗОВАТЕЛИ -----------

class TestUsers:
    def test_add_and_get_user(self, env):
        user_id = env.db.add_user(discord_id=111, alias="Габ", telegram_id=222)

        ozernik = env.db.get_user(user_id)

        assert ozernik.id == user_id
        assert ozernik.discord_id == 111
        assert ozernik.alias == "Габ"
        assert ozernik.telegram_id == 222
        assert ozernik.discord_twink_id is None

    def test_get_missing_user_returns_none(self, env):
        assert env.db.get_user(999) is None
        assert env.db.get_user_by_discord_id(999) is None
        assert env.db.get_user_by_telegram_id(999) is None

    def test_duplicate_discord_id_rejected(self, env):
        env.db.add_user(discord_id=111)

        with pytest.raises(sqlite3.IntegrityError):
            env.db.add_user(discord_id=111)

    def test_twink_equal_to_main_rejected(self, env):
        with pytest.raises(sqlite3.IntegrityError):
            env.db.add_user(discord_id=111, discord_twink_id=111)

    def test_discord_id_already_used_as_twink_rejected(self, env):
        env.db.add_user(discord_id=111, discord_twink_id=112)

        with pytest.raises(sqlite3.IntegrityError):
            env.db.add_user(discord_id=112)

    def test_find_by_twink_and_telegram(self, env):
        user_id = env.db.add_user(discord_id=111, discord_twink_id=112, telegram_id=333)

        assert env.db.get_user_by_discord_id(112).id == user_id
        assert env.db.get_user_by_telegram_id(333).id == user_id

    def test_update_user_changes_only_given_fields(self, env):
        user_id = env.db.add_user(discord_id=111, telegram_id=333)

        ozernik = env.db.update_user(user_id, telegram_bot_id=444)

        assert ozernik.discord_id == 111
        assert ozernik.telegram_id == 333
        assert ozernik.telegram_bot_id == 444

    def test_update_user_without_fields_returns_current(self, env):
        user_id = env.db.add_user(discord_id=111)

        assert env.db.update_user(user_id).discord_id == 111

    def test_update_missing_user_raises(self, env):
        with pytest.raises(ValueError):
            env.db.update_user(999, telegram_id=1)

    def test_update_user_conflicts(self, env):
        first = env.db.add_user(discord_id=111, telegram_id=333)
        second = env.db.add_user(discord_id=222)

        with pytest.raises(sqlite3.IntegrityError):
            env.db.update_user(second, telegram_id=333)

        with pytest.raises(sqlite3.IntegrityError):
            env.db.update_user(first, discord_twink_id=111)

        with pytest.raises(sqlite3.IntegrityError):
            env.db.update_user(first, telegram_bot_id=333)

    def test_delete_user_cascades_to_karma_and_binds(self, env):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)
        env.db.add_karma(first, 10)
        env.db.add_bind_karma(first, second, 5)

        assert env.db.delete_user(first) is True
        assert env.db.delete_user(first) is False

        assert env.db.fetchone("SELECT * FROM karma WHERE user_id = ?", (first,)) is None
        assert env.db.get_karmic_bind(first, second) is None

    def test_db_ensure_user_creates_once(self, env):
        member = env.guild.add_member(111)

        first = env.bot.db_ensure_user(member)
        second = env.bot.db_ensure_user(member)

        assert first.id == second.id
        assert first.discord_id == 111


# ---------- КАРМА -----------

class TestKarma:
    def test_get_karma_creates_zero_row(self, env):
        user_id = add_ozernik(env, 111)

        karma = env.db.get_karma(user_id)

        assert (karma.karma, karma.weekly_karma, karma.status) == (0, 0, None)

    def test_add_karma_returns_old_and_new(self, env):
        user_id = add_ozernik(env, 111)

        old, new = env.db.add_karma(user_id, 5)

        assert old.karma == 0
        assert new.karma == 5
        assert new.weekly_karma == 0

    def test_add_karma_weekly(self, env):
        user_id = add_ozernik(env, 111)

        env.db.add_karma(user_id, 5, weekly=True)
        _, new = env.db.add_karma(user_id, 3, weekly=True)

        assert new.karma == 8
        assert new.weekly_karma == 8

    def test_remove_karma_not_below_zero(self, env):
        user_id = add_ozernik(env, 111)
        env.db.add_karma(user_id, 3)

        karma = env.db.remove_karma(user_id, 5)

        assert karma.karma == 0

    def test_remove_karma_requires_positive_amount(self, env):
        user_id = add_ozernik(env, 111)

        with pytest.raises(ValueError):
            env.db.remove_karma(user_id, 0)

    def test_remove_karma_weekly(self, env):
        user_id = add_ozernik(env, 111)
        env.db.add_karma(user_id, 100)
        env.db.add_karma(user_id, 10, weekly=True)  # 110 / неделя 10

        karma = env.db.remove_karma(user_id, 4, weekly=True)
        assert (karma.karma, karma.weekly_karma) == (106, 6)

        karma = env.db.remove_karma(user_id, 50, weekly=True)
        assert (karma.karma, karma.weekly_karma) == (56, 0)

        # Без weekly недельная не трогается.
        env.db.add_karma(user_id, 5, weekly=True)
        karma = env.db.remove_karma(user_id, 1)
        assert karma.weekly_karma == 5

    def test_set_karma_weekly_follows_difference(self, env):
        user_id = add_ozernik(env, 111)
        env.db.add_karma(user_id, 100)
        env.db.add_karma(user_id, 20, weekly=True)  # 120 / неделя 20

        karma = env.db.set_karma(user_id, 130, weekly=True)
        assert (karma.karma, karma.weekly_karma) == (130, 30)

        karma = env.db.set_karma(user_id, 110, weekly=True)
        assert (karma.karma, karma.weekly_karma) == (110, 10)

        karma = env.db.set_karma(user_id, 0, weekly=True)
        assert (karma.karma, karma.weekly_karma) == (0, 0)

    def test_set_karma_rejects_negative(self, env):
        user_id = add_ozernik(env, 111)
        env.db.add_karma(user_id, 50)

        with pytest.raises(ValueError):
            env.db.set_karma(user_id, -1)

        assert env.db.set_karma(user_id, 20).karma == 20

    def test_set_karma_status(self, env):
        user_id = add_ozernik(env, 111)

        assert env.db.set_karma_status(user_id, "asur").status == "asur"
        assert env.db.set_karma_status(user_id, "deva").status == "deva"
        assert env.db.set_karma_status(user_id, None).status is None

        with pytest.raises(ValueError):
            env.db.set_karma_status(user_id, "human")

    def test_set_karma(self, env):
        user_id = add_ozernik(env, 111)
        env.db.add_karma(user_id, 50, weekly=True)

        karma = env.db.set_karma(user_id, 7)

        assert karma.karma == 7
        assert karma.weekly_karma == 50

    def test_reset_weekly_karma(self, env):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)
        env.db.add_karma(first, 5, weekly=True)
        env.db.add_karma(second, 9, weekly=True)

        env.db.reset_weekly_karma()

        assert env.db.get_karma(first).weekly_karma == 0
        assert env.db.get_karma(second).weekly_karma == 0
        assert env.db.get_karma(second).karma == 9

    def test_karma_rank(self, env, clock):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)
        third = add_ozernik(env, 333)
        env.db.add_karma(first, 5)
        env.db.add_karma(second, 10)
        env.db.add_karma(third, 5)

        assert env.db.get_karma_rank(second) == 1
        # При равной карме выше тот, кто достиг её позже.
        assert env.db.get_karma_rank(third) == 2
        assert env.db.get_karma_rank(first) == 3

    def test_equal_karma_reached_later_is_higher(self, env, clock):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)
        env.db.add_karma(second, 3)
        env.db.add_karma(first, 1)
        env.db.add_karma(first, 2)  # first достиг 3 позже second

        assert env.db.get_karma_rank(first) == 1
        assert env.db.get_karma_rank(second) == 2
        assert [o.id for o, _ in env.db.get_top_karma()] == [first, second]

    def test_rank_matches_top(self, env, clock):
        ids = [add_ozernik(env, 100 + n) for n in range(6)]
        for user_id, amount in zip(ids, [5, 7, 5, 7, 5, 0]):
            if amount:
                env.db.add_karma(user_id, amount)
            else:
                env.db.get_karma(user_id)

        top = [ozernik.id for ozernik, _ in env.db.get_top_karma()]

        assert [env.db.get_karma_rank(user_id) for user_id in top] == list(range(1, 7))

    def test_karma_update_time(self, env, clock):
        user_id = add_ozernik(env, 111)
        assert env.db.get_karma(user_id).karma_updated_at == 0

        _, karma = env.db.add_karma(user_id, 1)
        after_add = karma.karma_updated_at
        assert after_add > 0

        _, karma = env.db.add_karma(user_id, 1)
        assert karma.karma_updated_at > after_add

        karma = env.db.remove_karma(user_id, 1)
        after_remove = karma.karma_updated_at

        # Установка того же значения время не меняет, другого — меняет.
        assert env.db.set_karma(user_id, 1).karma_updated_at == after_remove
        assert env.db.set_karma(user_id, 9).karma_updated_at > after_remove

    def test_old_records_without_time_fall_back_to_id(self, env):
        """Старые записи (время = 0): при равной карме выше больший ID, как было раньше."""
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)
        env.db.add_karma(first, 5)
        env.db.add_karma(second, 5)
        env.db.execute("UPDATE karma SET karma_updated_at = 0")

        assert [o.id for o, _ in env.db.get_top_karma()] == [second, first]
        assert env.db.get_karma_rank(second) == 1

    def test_migration_adds_update_time_column(self, env):
        db = env.db
        db.execute("DROP TABLE karma")
        db.execute("""
            CREATE TABLE karma (
                user_id INTEGER PRIMARY KEY,
                status TEXT DEFAULT NULL,
                karma INTEGER NOT NULL DEFAULT 0,
                gift_karma INTEGER NOT NULL DEFAULT 0,
                weekly_karma INTEGER NOT NULL DEFAULT 0
            )
        """)
        user_id = add_ozernik(env, 111)
        db.execute("INSERT INTO karma (user_id, karma) VALUES (?, 42)", (user_id,))

        db._init_db()

        karma = db.get_karma(user_id)
        assert (karma.karma, karma.karma_updated_at) == (42, 0)

    def test_weekly_karma_rank_tie_prefers_lower_total(self, env):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)
        env.db.add_karma(first, 100)
        env.db.add_karma(first, 5, weekly=True)
        env.db.add_karma(second, 5, weekly=True)

        assert env.db.get_weekly_karma_rank(second) == 1
        assert env.db.get_weekly_karma_rank(first) == 2

    def test_top_karma_order(self, env, clock):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)
        third = add_ozernik(env, 333)
        env.db.add_karma(first, 5)
        env.db.add_karma(second, 10)
        env.db.add_karma(third, 5)

        top = env.db.get_top_karma()

        assert [ozernik.id for ozernik, _ in top] == [second, third, first]
        assert [karma.karma for _, karma in top] == [10, 5, 5]

    def test_top_karma_only_users_with_karma_row(self, env):
        add_ozernik(env, 111)

        assert env.db.get_top_karma() == []

    def test_top_weekly_karma_order(self, env):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)
        third = add_ozernik(env, 333)
        env.db.add_karma(first, 100)
        env.db.add_karma(first, 5, weekly=True)
        env.db.add_karma(second, 5, weekly=True)
        env.db.add_karma(third, 9, weekly=True)

        top = env.db.get_top_weekly_karma()

        assert [ozernik.id for ozernik, _ in top] == [third, second, first]


class TestKarmaLogs:
    def test_karma_log_rejects_unknown_reason(self, env):
        user_id = add_ozernik(env, 111)

        with pytest.raises(ValueError):
            env.db._add_karma_log(user_id, 1, 0, reason="voice")

        # Подарочной кармы нет.
        with pytest.raises(ValueError):
            env.db._add_karma_log(user_id, 1, 0, reason="gift")

    def test_karma_logs_period_validation(self, env):
        with pytest.raises(ValueError):
            env.db.get_karma_logs(10, 5)

        with pytest.raises(ValueError):
            env.db.get_user_karma_logs(1, 10, 5)

    def test_karma_logs_empty(self, env):
        assert env.db.get_karma_logs(0, 10) == []

    def test_karmic_bind_logs_empty(self, env):
        assert env.db.get_karmic_bind_logs(0, 10) == []

    def test_add_karma_log(self, env):
        user_id = add_ozernik(env, 111)
        other = add_ozernik(env, 222)

        first_id = env.db._add_karma_log(user_id, 5, 100, reason="message")
        second_id = env.db._add_karma_log(other, 2, 150)
        env.db._add_karma_log(user_id, 1, 300)

        assert second_id > first_id
        logs = env.db.get_karma_logs(0, 200)
        assert [(log["id"], log["user_id"], log["added_karma"], log["reason"]) for log in logs] == [
            (first_id, user_id, 5, "message"),
            (second_id, other, 2, "n/a"),
        ]
        assert [log["added_at"] for log in env.db.get_user_karma_logs(user_id, 0, 1000)] == [100, 300]

    def test_bind_log_rejects_unknown_reason(self, env):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)

        # Бывшие причины: 'reply' (Связь за ответы) и 'gift' (подарочная Связь) — механик нет.
        for reason in ("message", "reply", "gift"):
            with pytest.raises(ValueError):
                env.db._add_karmic_bind_log(first, second, 1, 0, reason=reason)

    def test_bind_logs_period_validation(self, env):
        with pytest.raises(ValueError):
            env.db.get_users_karmic_bind_logs(1, 2, 10, 5)
        with pytest.raises(ValueError):
            env.db.get_user_karmic_bind_logs(1, 10, 5)
        with pytest.raises(ValueError):
            env.db.get_karmic_bind_logs(10, 5)

    def test_add_karmic_bind_log(self, env):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)
        third = add_ozernik(env, 333)

        log_id = env.db._add_karmic_bind_log(second, first, 1, 100, reason="voice")
        env.db._add_karmic_bind_log(first, third, 2, 150)

        logs = env.db.get_karmic_bind_logs(0, 200)
        assert logs[0]["id"] == log_id
        assert (logs[0]["user1_id"], logs[0]["user2_id"]) == (first, second)
        assert len(env.db.get_users_karmic_bind_logs(first, second, 0, 200)) == 1
        assert len(env.db.get_user_karmic_bind_logs(first, 0, 200)) == 2
        # Конец периода для логов связей не включается.
        assert env.db.get_karmic_bind_logs(0, 100) == []


class TestAppendixMigration:
    """
    Старые базы: вычищенные механики (недельная Связь, Связь за ответы, подарочная карма и Связь)
    убираются из схемы, данные сохраняются.
    """

    def make_old_schema(self, env):
        db = env.db
        a, b, c = add_ozernik(env, 111), add_ozernik(env, 222), add_ozernik(env, 333)

        db.execute("DROP TABLE karma")
        db.execute("""
            CREATE TABLE karma (
                user_id INTEGER PRIMARY KEY,
                status TEXT DEFAULT NULL,
                karma INTEGER NOT NULL DEFAULT 0,
                gift_karma INTEGER NOT NULL DEFAULT 0,
                weekly_karma INTEGER NOT NULL DEFAULT 0,
                karma_updated_at INTEGER NOT NULL DEFAULT 0,
                CHECK (status IS NULL OR status IN ('asur', 'deva')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        db.execute("INSERT INTO karma VALUES (?, 'asur', 6000, 300, 40, 123)", (a,))

        db.execute("DROP TABLE karma_logs")
        db.execute("""
            CREATE TABLE karma_logs (
                user_id INTEGER NOT NULL,
                added_at INTEGER NOT NULL,
                added_karma INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT 'n/a',
                CHECK (reason IN ('message', 'gift', 'n/a')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        db.execute("INSERT INTO karma_logs VALUES (?, 100, 1, 'message')", (a,))
        db.execute("INSERT INTO karma_logs VALUES (?, 200, 50, 'gift')", (a,))

        db.execute("DROP TABLE karmic_bind")
        db.execute("""
            CREATE TABLE karmic_bind (
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                bind_karma INTEGER NOT NULL DEFAULT 0,
                gift_bind_karma INTEGER NOT NULL DEFAULT 0,
                weekly_bind_karma INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user1_id, user2_id),
                CHECK (user1_id < user2_id),
                FOREIGN KEY (user1_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (user2_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        db.execute("INSERT INTO karmic_bind VALUES (?, ?, 500, 20, 7)", (a, b))
        db.execute("INSERT INTO karmic_bind VALUES (?, ?, 60, 0, 1)", (b, c))

        db.execute("DROP TABLE karmic_bind_logs")
        db.execute("""
            CREATE TABLE karmic_bind_logs (
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                added_at INTEGER NOT NULL,
                added_karma INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT 'n/a',
                CHECK (reason IN ('voice', 'reply', 'gift', 'n/a')),
                CHECK (user1_id < user2_id),
                FOREIGN KEY (user1_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (user2_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        db.execute("INSERT INTO karmic_bind_logs VALUES (?, ?, 100, 1, 'voice')", (a, b))
        db.execute("INSERT INTO karmic_bind_logs VALUES (?, ?, 200, 1, 'reply')", (a, b))
        db.execute("INSERT INTO karmic_bind_logs VALUES (?, ?, 300, 1, 'gift')", (a, b))
        return a, b, c

    def schema(self, env, table):
        return env.db.fetchone("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))["sql"]

    def columns(self, env, table):
        return {row["name"] for row in env.db.fetchall(f"PRAGMA table_info({table})")}

    def test_migration_keeps_data_and_drops_appendices(self, env):
        a, b, c = self.make_old_schema(env)

        env.db._init_db()

        # Карма и статус на месте, подарочной колонки нет.
        karma = env.db.get_karma(a)
        assert (karma.karma, karma.status, karma.weekly_karma, karma.karma_updated_at) == (6000, "asur", 40, 123)
        assert "gift_karma" not in self.columns(env, "karma")

        # Связи на месте, недельной и подарочной колонок нет.
        assert env.db.get_karmic_bind(a, b).bind_karma == 500
        assert env.db.get_karmic_bind(b, c).bind_karma == 60
        assert {"weekly_bind_karma", "gift_bind_karma"} & self.columns(env, "karmic_bind") == set()

        # Логи с вычищенными причинами удалены, остальные на месте.
        assert [log["reason"] for log in env.db.get_karma_logs(0, 1000)] == ["message"]
        assert [log["reason"] for log in env.db.get_karmic_bind_logs(0, 1000)] == ["voice"]
        assert "'gift'" not in self.schema(env, "karma_logs")
        assert "'reply'" not in self.schema(env, "karmic_bind_logs")
        assert "'gift'" not in self.schema(env, "karmic_bind_logs")

    def test_migrated_tables_still_work(self, env):
        a, b, c = self.make_old_schema(env)
        env.db._init_db()

        assert env.db.add_bind_karma(a, b, 5).bind_karma == 505
        _, karma = env.db.add_karma(a, 10, weekly=True)
        assert (karma.karma, karma.weekly_karma) == (6010, 50)
        env.db._add_karmic_bind_log(a, c, 1, 400, reason="voice")

        # Ограничения новой схемы на месте.
        with pytest.raises(sqlite3.IntegrityError):
            env.db.execute("INSERT INTO karmic_bind_logs VALUES (?, ?, 1, 1, 'reply')", (a, b))
        with pytest.raises(sqlite3.IntegrityError):
            env.db.execute("INSERT INTO karma_logs VALUES (?, 1, 1, 'gift')", (a,))
        with pytest.raises(sqlite3.IntegrityError):
            env.db.execute("INSERT INTO karmic_bind VALUES (?, ?, 1)", (b, a))

        # Каскадное удаление по-прежнему работает.
        env.db.delete_user(a)
        assert env.db.fetchone("SELECT * FROM karma WHERE user_id = ?", (a,)) is None
        assert env.db.get_karmic_bind(b, c).bind_karma == 60

    def test_migration_is_idempotent(self, env):
        a, b, c = self.make_old_schema(env)

        env.db._init_db()
        env.db._init_db()

        assert env.db.get_karmic_bind(a, b).bind_karma == 500
        assert env.db.get_karma(a).karma == 6000
        leftovers = env.db.fetchall("SELECT name FROM sqlite_master WHERE name LIKE '%_old'")
        assert leftovers == []

    def test_log_indexes_recreated(self, env):
        self.make_old_schema(env)

        env.db._init_db()

        indexes = {row["name"] for row in env.db.fetchall("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_karma_logs_user_time", "idx_karmic_bind_logs_pair_time"} <= indexes


# ---------- КАРМИЧЕСКИЕ СВЯЗИ -----------

class TestKarmicBinds:
    def test_normalize_pair(self, env):
        assert env.db._normalize_pair(5, 2) == (2, 5)

        with pytest.raises(ValueError):
            env.db._normalize_pair(3, 3)

    def test_missing_bind_is_none(self, env):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)

        assert env.db.get_karmic_bind(first, second) is None

    def test_add_bind_karma_is_symmetric_and_accumulates(self, env):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)

        env.db.add_bind_karma(first, second, 2)
        bind = env.db.add_bind_karma(second, first, 3)

        assert bind.ids == (first, second)
        assert bind.bind_karma == 5
        assert env.db.get_karmic_bind(second, first).bind_karma == 5

    def test_add_bind_karma_many(self, env):
        a, b, c = (add_ozernik(env, 111 * n) for n in (1, 2, 3))
        env.db.add_bind_karma(a, b, 10)

        binds = env.db.add_bind_karma_many([(b, a), (a, c), (c, b)], karma=2)

        assert [(bind.ids, bind.bind_karma) for bind in binds] == [
            ((a, b), 12),
            ((a, c), 2),
            ((b, c), 2),
        ]
        assert env.db.get_karmic_bind(c, a).bind_karma == 2

    def test_add_bind_karma_many_rejects_self_pair_without_writing(self, env):
        a, b = add_ozernik(env, 111), add_ozernik(env, 222)

        with pytest.raises(ValueError):
            env.db.add_bind_karma_many([(a, b), (a, a)], karma=1)

        assert env.db.get_karmic_bind(a, b) is None

    def test_add_bind_karma_many_empty(self, env):
        assert env.db.add_bind_karma_many([], karma=1) == []

    def test_remove_bind_karma(self, env):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)
        env.db.add_bind_karma(first, second, 10)

        assert env.db.remove_bind_karma(first, second, 4).bind_karma == 6

        with pytest.raises(ValueError):
            env.db.remove_bind_karma(first, second, 0)

    def test_set_bind_karma(self, env):
        first = add_ozernik(env, 111)
        second = add_ozernik(env, 222)
        env.db.add_bind_karma(first, second, 8)

        assert env.db.set_bind_karma(first, second, 3).bind_karma == 3

        with pytest.raises(ValueError):
            env.db.set_bind_karma(first, second, -1)

    def test_user_top_karmic_binds(self, env):
        me = add_ozernik(env, 111)
        a = add_ozernik(env, 222)
        b = add_ozernik(env, 333)
        c = add_ozernik(env, 444)
        env.db.add_bind_karma(me, a, 5)
        env.db.add_bind_karma(b, me, 9)
        env.db.add_bind_karma(a, c, 100)  # чужая связь

        binds = env.db.get_user_top_karmic_binds(me)

        assert [ozernik.id for ozernik, _ in binds] == [b, a]
        assert [bind.bind_karma for _, bind in binds] == [9, 5]

    def test_top_karmic_binds(self, env):
        a = add_ozernik(env, 111)
        b = add_ozernik(env, 222)
        c = add_ozernik(env, 333)
        env.db.add_bind_karma(a, b, 5)
        env.db.add_bind_karma(c, b, 9)

        top = env.db.get_top_karmic_binds()

        assert [(first.id, second.id, bind.bind_karma) for first, second, bind in top] == [
            (b, c, 9),
            (a, b, 5),
        ]
        assert top[0][0].discord_id == 222
