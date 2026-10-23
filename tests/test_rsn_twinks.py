"""
Твинки в РСН: человек со всеми своими аккаунтами — один подсудимый.

Личное дело, баллы и наказание ведутся на основной аккаунт, роль мута ложится
на все аккаунты человека. Какие аккаунты чьи — знает общая база бота
(users + user_twinks), привязку делает модуль кармы.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conftest import FakeBot, FakeGuild, FakeRole, karma, make_interaction
import modules.rsn.rsn as rsn
from modules.rsn._rsn_db import RsnDatabase

MUTE_ROLE_ID = 777


class BotTopRole:
    """Верхняя роль бота: роль мута всегда ниже (mute_role >= top → False)."""

    def __le__(self, other):
        return False


@pytest.fixture
def rsn_env(monkeypatch):
    guild = FakeGuild()
    guild.me = SimpleNamespace(top_role=BotTopRole())
    mute_role = FakeRole(MUTE_ROLE_ID, "Мут", guild=guild)
    guild.roles[MUTE_ROLE_ID] = mute_role

    shared_db = karma.KarmaDatabase()  # общая база бота (в памяти)
    bot = FakeBot(shared_db, guild)
    bot.get_guild = lambda guild_id: guild if guild_id == guild.id else None

    # Фоновые циклы не запускаем: собираем Cog без __init__.
    cog = rsn.RsnCog.__new__(rsn.RsnCog)
    cog.bot = bot
    cog.db = RsnDatabase(":memory:")
    cog._check_permissions = AsyncMock(return_value=True)

    for key, value in {"mute_role_id": MUTE_ROLE_ID, "log_channel_id": 0, "admin_notify_channel_id": 0}.items():
        monkeypatch.setitem(rsn.RSN_DATA, key, value)

    main = guild.add_member(1)
    twink = guild.add_member(11)
    for member in (main, twink):
        member.dm = SimpleNamespace(send=AsyncMock())
        member.create_dm = AsyncMock(return_value=member.dm)

    yield SimpleNamespace(guild=guild, bot=bot, cog=cog, db=cog.db, shared_db=shared_db,
                          mute_role=mute_role, main=main, twink=twink)
    cog.db.close()


def link(rsn_env, main_id=1, twink_id=11):
    user_id = rsn_env.shared_db.get_user_by_discord_id(main_id)
    user_id = user_id.id if user_id else rsn_env.shared_db.add_user(discord_id=main_id)
    rsn_env.shared_db.link_twink(user_id, twink_id)


async def mute(rsn_env, target, points=5, duration="30m"):
    interaction = make_interaction(rsn_env.guild.add_member(900), rsn_env.guild)
    await rsn_env.cog.mute.callback(rsn_env.cog, interaction, target, duration, "флуд", points)
    return interaction


def has_mute(member) -> bool:
    return any(role.id == MUTE_ROLE_ID for role in member.roles)


# ---------- БАЗА: перенос дела твинка на основной -----------

class TestMergeUser:
    def test_nothing_to_merge(self):
        db = RsnDatabase(":memory:")
        assert db.merge_user(11, 1) is False

    def test_points_min_records_moved_active_kept(self):
        db = RsnDatabase(":memory:")
        db.set_points(1, 40)
        db.set_points(11, 20)
        db.set_active(11, 1)
        record_id = db.create_record(11, "mute", "спам", 1.0, -5, 900)

        assert db.merge_user(11, 1) is True

        assert db.get_score(1) == {"user_id": 1, "points": 20, "active": 1}
        assert db.get_score(11) is None
        assert [record["id"] for record in db.get_user_records(1)] == [record_id]
        assert db.get_user_records(11) == []

    def test_main_without_case_gets_twink_case(self):
        db = RsnDatabase(":memory:")
        db.set_points(11, 15)

        db.merge_user(11, 1)

        assert db.get_score(1)["points"] == 15

    @pytest.mark.parametrize(
        ("main_p", "twink_p", "winner"),
        [
            (None, ("mute", 100), "twink"),
            (("mute", 100), ("mute", 200), "twink"),     # позднее строже
            (("mute", 300), ("mute", 200), "main"),
            (("mute", 100), ("mute", None), "twink"),    # бессрочное строже срочного
            (("ban", 100), ("mute", None), "main"),      # бан строже мута
        ],
    )
    def test_stricter_punishment_kept(self, main_p, twink_p, winner):
        db = RsnDatabase(":memory:")
        ids = {}
        for who, user_id, punishment in (("main", 1, main_p), ("twink", 11, twink_p)):
            if punishment:
                kind, expires = punishment
                ids[who] = db.create_record(user_id, kind, "x", None, 0, 900)
                db.create_or_update_punishment(user_id, kind, expires, ids[who])

        db.merge_user(11, 1)

        assert db.get_active_punishment(1)["record_id"] == ids[winner]
        assert db.get_active_punishment(11) is None


# ---------- КОМАНДЫ -----------

class TestRsnCommands:
    async def test_mute_twink_goes_to_main_and_mutes_all(self, rsn_env):
        link(rsn_env)

        interaction = await mute(rsn_env, rsn_env.twink)

        assert rsn_env.db.get_score(1)["points"] == 45
        assert rsn_env.db.get_score(11) is None
        assert rsn_env.db.get_active_punishment(1)["kind"] == "mute"
        assert has_mute(rsn_env.main) and has_mute(rsn_env.twink)
        description = interaction.response.send_message.call_args.kwargs["embed"].description
        assert "Аккаунты: <@1>, <@11>" in description
        assert "Вам выдан мут" in rsn_env.main.dm.send.call_args.args[0]

    async def test_cannot_mute_twice_via_other_account(self, rsn_env):
        link(rsn_env)
        await mute(rsn_env, rsn_env.main)

        interaction = await mute(rsn_env, rsn_env.twink)

        assert "уже наложен мут" in interaction.response.send_message.call_args.args[0]

    async def test_mute_when_only_twink_on_server(self, rsn_env):
        """Основной вышел с сервера — мут всё равно ложится на оставшийся аккаунт."""
        link(rsn_env)
        rsn_env.guild.members.remove(rsn_env.main)

        await mute(rsn_env, rsn_env.main)

        assert has_mute(rsn_env.twink)
        assert rsn_env.db.get_active_punishment(1) is not None

    async def test_unmute_removes_role_from_all(self, rsn_env):
        link(rsn_env)
        await mute(rsn_env, rsn_env.main)
        interaction = make_interaction(rsn_env.main, rsn_env.guild)

        await rsn_env.cog.unmute.callback(rsn_env.cog, interaction, rsn_env.twink)

        assert not has_mute(rsn_env.main) and not has_mute(rsn_env.twink)
        assert rsn_env.db.get_active_punishment(1) is None

    async def test_expired_mute_removes_role_from_all(self, rsn_env):
        link(rsn_env)
        await mute(rsn_env, rsn_env.main)

        await rsn_env.cog._process_expired_punishment(rsn_env.db.get_active_punishment(1))

        assert not has_mute(rsn_env.main) and not has_mute(rsn_env.twink)

    async def test_case_view_from_twink_shows_own_case(self, rsn_env):
        """С твинка своё дело (дело основного) видно без прав модератора."""
        link(rsn_env)
        await mute(rsn_env, rsn_env.main)
        rsn_env.cog._check_permissions = AsyncMock(return_value=False)
        interaction = make_interaction(rsn_env.twink, rsn_env.guild)

        await rsn_env.cog.case_view.callback(rsn_env.cog, interaction, rsn_env.main)

        rsn_env.cog._check_permissions.assert_not_awaited()
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert "Баллы: 45/50" in embed.description
        assert "флуд" in embed.fields[0].value

    async def test_scale_on_twink_changes_main(self, rsn_env):
        link(rsn_env)
        interaction = make_interaction(rsn_env.main, rsn_env.guild)

        await rsn_env.cog.scale_set.callback(rsn_env.cog, interaction, rsn_env.twink, 30)

        assert rsn_env.db.get_score(1)["points"] == 30
        assert rsn_env.db.get_score(11) is None

    async def test_ban_record_by_twink_id(self, rsn_env):
        link(rsn_env)
        interaction = make_interaction(rsn_env.main, rsn_env.guild)

        await rsn_env.cog.ban_record.callback(rsn_env.cog, interaction, "11", "обход", None, None)

        assert rsn_env.db.get_active_punishment(1)["kind"] == "ban"
        description = interaction.response.send_message.call_args.kwargs["embed"].description
        assert "Аккаунты: <@1>, <@11>" in description
        assert "Забаньте все аккаунты" in description

    async def test_without_twinks_nothing_changes(self, rsn_env):
        await mute(rsn_env, rsn_env.twink)

        assert rsn_env.db.get_score(11)["points"] == 45
        assert has_mute(rsn_env.twink) and not has_mute(rsn_env.main)
        assert rsn_env.db.get_score(1) is None


# ---------- ОБХОД МУТА -----------

class TestMuteEvasion:
    async def test_twink_linked_after_mute_gets_role(self, rsn_env):
        """Мут выдан, твинк привязали позже — цикл проверки (раз в 3 минуты) мутит и его."""
        await mute(rsn_env, rsn_env.main)
        assert not has_mute(rsn_env.twink)
        link(rsn_env)

        await rsn_env.cog._sync_twinks()

        assert has_mute(rsn_env.twink)

    async def test_case_opened_on_twink_before_link_moves_to_main(self, rsn_env):
        await mute(rsn_env, rsn_env.twink)
        link(rsn_env)

        await rsn_env.cog._sync_twinks()

        assert rsn_env.db.get_score(11) is None
        assert rsn_env.db.get_score(1)["points"] == 45
        assert rsn_env.db.get_active_punishment(1)["kind"] == "mute"
        assert has_mute(rsn_env.main)

    async def test_rejoin_while_muted(self, rsn_env):
        link(rsn_env)
        await mute(rsn_env, rsn_env.main)
        rsn_env.guild.members.remove(rsn_env.twink)
        rejoined = rsn_env.guild.add_member(11)  # после перезахода ролей нет

        await rsn_env.cog._mute_on_join(rejoined)

        assert has_mute(rejoined)

    async def test_join_without_mute(self, rsn_env):
        link(rsn_env)
        newcomer = rsn_env.guild.add_member(11)

        await rsn_env.cog._mute_on_join(newcomer)

        assert not has_mute(newcomer)

    async def test_person_on_server_by_any_account(self, rsn_env):
        """Основной вышел, твинк остался — человек всё ещё на сервере (недельный балл, снятие мута)."""
        link(rsn_env)
        rsn_env.guild.members.remove(rsn_env.main)

        assert rsn_env.cog._guild_members(1) == [rsn_env.twink]
        assert rsn_env.cog._guild_members(11) == [rsn_env.twink]
