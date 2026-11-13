"""
РСН 2.0, база: дела (rsn_cases) и записи участников дел (rsn_records.case_id, вид 'warn').
Главное — перенос старых баз: номера дел, которые видели люди, не меняются.
"""

import sqlite3

import pytest

from modules.rsn._rsn_db import RsnDatabase

OLD_SCHEMA = """
    CREATE TABLE rsn_score (user_id INTEGER PRIMARY KEY, points INTEGER NOT NULL DEFAULT 50,
                            active INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE rsn_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('mute', 'ban')),
        reason TEXT NOT NULL,
        duration_hours REAL,
        points_delta INTEGER NOT NULL DEFAULT 0,
        moderator_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE rsn_active_punishments (
        user_id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('mute', 'ban')),
        expires_at INTEGER,
        record_id INTEGER NOT NULL REFERENCES rsn_records(id)
    );
"""


@pytest.fixture
def old_db(tmp_path):
    """База по схеме до РСН 2.0: три дела, по второму ещё действует мут."""
    path = tmp_path / "rsn.db"
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.executemany(
        "INSERT INTO rsn_records (id, user_id, kind, reason, duration_hours, points_delta, moderator_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 10, "mute", "флуд", 1.0, -5, 900, 1000),
            (2, 11, "mute", "оскорбление", 2.0, -10, 900, 2000),
            (5, 12, "ban", "обход", None, 0, 901, 3000),  # номера с пропуском — как после удалений
        ],
    )
    con.execute("INSERT INTO rsn_active_punishments VALUES (11, 'mute', 99999999999, 2)")
    con.execute("INSERT INTO rsn_score VALUES (11, 40, 1)")
    con.commit()
    con.close()
    return path


def test_old_records_become_cases_with_same_numbers(old_db):
    db = RsnDatabase(str(old_db))

    cases = {c["id"]: c["status"] for c in map(dict, db.conn.execute("SELECT * FROM rsn_cases"))}
    assert cases == {1: "closed", 2: "open", 5: "closed"}  # открыто то, где ещё действует мут
    assert [(r["id"], r["case_id"], r["reason"]) for r in db.get_all_records()] == [
        (1, 1, "флуд"), (2, 2, "оскорбление"), (5, 5, "обход"),
    ]
    assert db.get_active_punishment(11)["record_id"] == 2  # ссылка не повисла
    assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.get_score(11)["points"] == 40
    db.close()


def test_numbering_continues_after_migration(old_db):
    db = RsnDatabase(str(old_db))

    case_id = db.create_case(created_by=900)
    record_id = db.create_record(13, "warn", "предупреждение", None, 0, 900, case_id=case_id)

    assert case_id == 6 and record_id == 6  # дальше старых номеров
    db.close()


def test_migration_runs_once(old_db):
    RsnDatabase(str(old_db)).close()
    db = RsnDatabase(str(old_db))  # второй запуск ничего не ломает

    assert db.conn.execute("SELECT COUNT(*) FROM rsn_cases").fetchone()[0] == 3
    db.close()


@pytest.fixture
def db():
    database = RsnDatabase(":memory:")
    yield database
    database.close()


def test_warn_kind_allowed(db):
    record_id = db.create_record(10, "warn", "предупреждение", None, 0, 900)

    assert db.get_record(record_id)["kind"] == "warn"


def test_record_without_case_gets_own_case(db):
    first = db.create_record(10, "mute", "флуд", 1.0, -5, 900, points_before=50)
    second = db.create_record(11, "mute", "флуд", 1.0, -5, 900)

    assert db.get_record(first)["case_id"] != db.get_record(second)["case_id"]
    assert db.get_record(first)["points_before"] == 50


def test_case_with_several_members(db):
    case_id = db.create_case(created_by=900)
    a = db.create_record(10, "mute", "флуд", 1.0, -5, 900, case_id=case_id)
    b = db.create_record(11, "mute", "оскорбление", 2.0, -10, 900, case_id=case_id)
    db.create_or_update_punishment(10, "mute", 99999999999, a)
    db.create_or_update_punishment(11, "mute", 99999999999, b)

    assert [r["reason"] for r in db.get_case_records(case_id)] == ["флуд", "оскорбление"]
    assert sorted(db.get_case_active_users(case_id)) == [10, 11]

    db.delete_active_punishment(10)
    db.release_record(a, released_by=901)

    assert db.get_case_active_users(case_id) == [11]
    assert db.get_record(a)["released_by"] == 901
    assert db.get_record(a)["released_at"] is not None


def test_case_channel_and_close(db):
    case_id = db.create_case(created_by=900, manual_chat=True)
    db.set_case_channel(case_id, 555)

    assert db.get_open_case_of_channel(555)["id"] == case_id
    assert db.get_case(case_id)["manual_chat"] == 1

    db.close_case(case_id, closed_by=901)

    assert db.get_case(case_id)["status"] == "closed"
    assert db.get_open_case_of_channel(555) is None


def test_release_only_once(db):
    """Повторное освобождение не перезаписывает, кто и когда освободил."""
    record_id = db.create_record(10, "mute", "флуд", 1.0, -5, 900)
    db.release_record(record_id, released_by=901)
    first = db.get_record(record_id)

    db.release_record(record_id, released_by=None)

    assert db.get_record(record_id)["released_by"] == 901
    assert db.get_record(record_id)["released_at"] == first["released_at"]
