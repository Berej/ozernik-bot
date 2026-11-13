"""
РСН 2.0 (решения Alium 2026-11-07): дела с чатом, уровни наказания, несколько участников,
лог переписки при закрытии, 0 очков → бан по выбору админа, дела без мута, /rsn_setup.
"""

import io
import time
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import modules.rsn.rsn as rsn
from modules.rsn import _rsn_cases
from conftest import make_interaction
from test_rsn_twinks import (  # noqa: F401 — фикстура rsn_env
    ADMIN_ROLE_ID, ISOLATION_ROLE_ID, MARKER_ROLE_ID, MUTE_ROLE_ID, FakeTextChannel, rsn_env,
)


def roles_of(member) -> set:
    return {role.id for role in member.roles}


async def mute(env, target, points=5, duration="30m", reason="флуд", case=None):
    interaction = make_interaction(env.guild.add_member(900), env.guild)
    await env.cog.mute.callback(env.cog, interaction, target, duration, reason, points, case)
    return interaction


def case_of(env, user_id):
    return env.db.get_record(env.db.get_active_punishment(user_id)["record_id"])["case_id"]


# ---------- УРОВНИ ----------

class TestLevels:
    async def test_high_level_mute_role_x1(self, rsn_env):
        await mute(rsn_env, rsn_env.main, points=5)  # 50 очков до списания

        record = rsn_env.db.get_record(rsn_env.db.get_active_punishment(1)["record_id"])
        assert MUTE_ROLE_ID in roles_of(rsn_env.main) and ISOLATION_ROLE_ID not in roles_of(rsn_env.main)
        assert record["duration_hours"] == 0.5 and record["points_before"] == 50

    async def test_middle_level_isolation_x2(self, rsn_env):
        rsn_env.db.set_points(1, 20)

        await mute(rsn_env, rsn_env.main, points=5)

        record = rsn_env.db.get_record(rsn_env.db.get_active_punishment(1)["record_id"])
        assert ISOLATION_ROLE_ID in roles_of(rsn_env.main) and MUTE_ROLE_ID not in roles_of(rsn_env.main)
        assert record["duration_hours"] == 1.0  # 30m ×2

    async def test_low_level_x3_and_marker(self, rsn_env):
        rsn_env.db.set_points(1, 12)

        await mute(rsn_env, rsn_env.main, points=5)  # 12 → 7: уровень по очкам ДО (11–30, ×2), метка — по очкам ПОСЛЕ

        record = rsn_env.db.get_record(rsn_env.db.get_active_punishment(1)["record_id"])
        assert record["duration_hours"] == 1.0
        assert MARKER_ROLE_ID in roles_of(rsn_env.main)

    async def test_marker_removed_when_points_grow(self, rsn_env):
        rsn_env.db.set_points(1, 10)
        await rsn_env.cog._sync_marker(1)
        assert MARKER_ROLE_ID in roles_of(rsn_env.main)

        rsn_env.db.set_points(1, 11)  # недельный +1
        await rsn_env.cog._sync_all_markers()

        assert MARKER_ROLE_ID not in roles_of(rsn_env.main)

    async def test_no_roles_configured(self, rsn_env, monkeypatch):
        monkeypatch.setitem(rsn.RSN_DATA, "mute_role_id", 0)
        monkeypatch.setitem(rsn.RSN_DATA, "isolation_role_id", 0)

        interaction = await mute(rsn_env, rsn_env.main)

        assert "/rsn_setup" in interaction.response.send_message.call_args.args[0]


# ---------- ЧАТ ДЕЛА И НЕСКОЛЬКО УЧАСТНИКОВ ----------

class TestCaseChat:
    async def test_mute_opens_chat_with_intro(self, rsn_env):
        await mute(rsn_env, rsn_env.main)

        channel = rsn_env.created_channels[0]
        case_id = case_of(rsn_env, 1)
        assert channel.name == f"дело-{case_id}"
        assert channel.overwrites[rsn_env.main].send_messages is True
        assert channel.overwrites[rsn_env.admin_role].view_channel is True
        assert channel.overwrites[rsn_env.guild.default_role].view_channel is False
        intro = channel.send.call_args.args[0]
        assert f"**Дело #{case_id}** · <@1>" in intro and "Причина: флуд" in intro
        assert "чате дела" in rsn_env.main.dm.send.call_args.args[0]

    async def test_second_member_joins_same_case_with_own_reason(self, rsn_env):
        await mute(rsn_env, rsn_env.main, reason="флуд")
        case_id = case_of(rsn_env, 1)

        await mute(rsn_env, rsn_env.twink, points=10, reason="оскорбление", case=case_id)

        assert len(rsn_env.created_channels) == 1  # чат один
        channel = rsn_env.created_channels[0]
        assert channel.overwrites[rsn_env.twink].send_messages is True
        records = rsn_env.db.get_case_records(case_id)
        assert [(r["user_id"], r["reason"], r["points_delta"]) for r in records] == [
            (1, "флуд", -5), (11, "оскорбление", -10),
        ]

    async def test_closed_case_cannot_be_joined(self, rsn_env):
        case_id = rsn_env.db.create_case()
        rsn_env.db.close_case(case_id)

        interaction = await mute(rsn_env, rsn_env.main, case=case_id)

        assert f"Открытого дела #{case_id} нет" in interaction.response.send_message.call_args.args[0]

    async def test_release_one_then_last_closes_with_log(self, rsn_env):
        await mute(rsn_env, rsn_env.main, reason="флуд")
        case_id = case_of(rsn_env, 1)
        await mute(rsn_env, rsn_env.twink, reason="оскорбление", case=case_id)
        channel = rsn_env.created_channels[0]
        channel.messages = [SimpleNamespace(
            content="я не виноват", embeds=[],
            attachments=[SimpleNamespace(filename="скрин.png", size=3, read=AsyncMock(return_value=b"IMG"))],
            author=SimpleNamespace(display_name="User 1", id=1),
            created_at=datetime.now(timezone.utc), edited_at=None,
        )]

        # Первого — досрочно: доступ к чату снят, дело ещё открыто
        unmute = make_interaction(rsn_env.guild.add_member(901), rsn_env.guild)
        await rsn_env.cog.unmute.callback(rsn_env.cog, unmute, rsn_env.main)

        assert rsn_env.main not in channel.overwrites
        assert rsn_env.db.get_case(case_id)["status"] == "open"
        channel.delete.assert_not_awaited()

        # Второго — по сроку: последний освобождён → лог и удаление чата
        await rsn_env.cog._process_expired_punishment(rsn_env.db.get_active_punishment(11))

        assert rsn_env.db.get_case(case_id)["status"] == "closed"
        channel.delete.assert_awaited_once()
        sent = next(call for call in rsn_env.log.send.call_args_list if "files" in call.kwargs)
        assert sent.args[0].startswith(f"📁 **Дело #{case_id} закрыто**")
        files = {f.filename: f for f in sent.kwargs["files"]}
        transcript = files[f"дело-{case_id}.txt"].fp.read().decode("utf-8")
        assert "причина: флуд" in transcript and "причина: оскорбление" in transcript
        assert "досрочно" in transcript and "по сроку" in transcript
        assert "я не виноват" in transcript and "[вложение: 0001_скрин.png]" in transcript
        with zipfile.ZipFile(io.BytesIO(files[f"дело-{case_id}-вложения.zip"].fp.read())) as zf:
            assert zf.read("0001_скрин.png") == b"IMG"


# ---------- 0 ОЧКОВ И БАН ----------

class TestBan:
    async def test_zero_points_asks_admins(self, rsn_env):
        rsn_env.db.set_points(1, 5)

        await mute(rsn_env, rsn_env.main, points=10)

        assert rsn_env.db.get_active_punishment(1)["expires_at"] is None  # до решения — в муте
        message = rsn_env.notify.send.call_args.kwargs
        labels = [item.item.label for item in message["view"].children]
        assert labels == ["7 дней", "30 дней", "Навсегда", "Свой срок…"]
        assert "<@1>" in message["embed"].description

    async def press_ban(self, env, term, admin=True):
        case_id = case_of(env, 1)
        button = _rsn_cases.BanTermButton(1, case_id, term)
        presser = env.guild.add_member(950)
        if admin:
            presser.roles.append(env.admin_role)
        interaction = make_interaction(presser, env.guild)
        interaction.client = SimpleNamespace(get_cog=lambda name: env.cog if name == "RsnCog" else None)
        interaction.message = SimpleNamespace(edit=AsyncMock())
        await button.callback(interaction)
        return interaction, case_id

    async def test_admin_bans_all_accounts(self, rsn_env):
        from test_rsn_twinks import link
        link(rsn_env)
        rsn_env.db.set_points(1, 5)
        await mute(rsn_env, rsn_env.main, points=10)

        interaction, case_id = await self.press_ban(rsn_env, "7d")

        punishment = rsn_env.db.get_active_punishment(1)
        assert punishment["kind"] == "ban"
        assert punishment["expires_at"] == pytest.approx(time.time() + 7 * 86400, abs=10)
        banned = sorted(call.args[0].id for call in rsn_env.guild.ban.call_args_list)
        assert banned == [1, 11]  # все аккаунты человека
        assert rsn_env.db.get_case(case_id)["status"] == "closed"  # из чата убран, дело закрыто
        assert "забанен: 7д" in interaction.message.edit.call_args.kwargs["content"]

    async def test_moderator_cannot_ban(self, rsn_env):
        rsn_env.db.set_points(1, 5)
        await mute(rsn_env, rsn_env.main, points=10)

        interaction, _ = await self.press_ban(rsn_env, "perm", admin=False)

        assert "только администрация" in interaction.response.send_message.call_args.args[0]
        rsn_env.guild.ban.assert_not_awaited()

    async def test_ban_expiry_unbans_with_ten_points(self, rsn_env):
        await rsn_env.cog._ban(1, 60, "обход", 900)

        await rsn_env.cog._process_expired_punishment(rsn_env.db.get_active_punishment(1))

        assert rsn_env.db.get_active_punishment(1) is None
        assert rsn_env.db.get_score(1)["points"] == 10
        assert rsn_env.guild.unban.call_args.args[0].id == 1
        assert MARKER_ROLE_ID in roles_of(rsn_env.main)  # 10 очков — уровень 1–10

    async def test_ban_command_admin_only(self, rsn_env):
        interaction = make_interaction(rsn_env.guild.add_member(960), rsn_env.guild)

        await rsn_env.cog.ban.callback(rsn_env.cog, interaction, rsn_env.main, "обход", "7d")

        assert "Только для администраторов" in interaction.response.send_message.call_args.args[0]

    async def test_ban_and_unban_commands(self, rsn_env):
        admin = rsn_env.guild.add_member(961)
        admin.roles.append(rsn_env.admin_role)

        await rsn_env.cog.ban.callback(rsn_env.cog, make_interaction(admin, rsn_env.guild), rsn_env.main, "обход", None)
        assert rsn_env.db.get_active_punishment(1)["expires_at"] is None  # без срока — навсегда

        await rsn_env.cog.unban.callback(rsn_env.cog, make_interaction(admin, rsn_env.guild), rsn_env.main)

        assert rsn_env.db.get_active_punishment(1) is None
        assert rsn_env.db.get_score(1)["points"] == 10

    @pytest.mark.parametrize(("text", "minutes"), [("30m", 30), ("12h", 720), ("14d", 20160), ("0d", None), ("abc", None)])
    def test_parse_term(self, text, minutes):
        assert _rsn_cases.parse_term(text) == minutes


# ---------- ДЕЛО БЕЗ МУТА ----------

class TestCaseWithoutMute:
    async def case(self, env, points=0, chat=False, case=None):
        interaction = make_interaction(env.guild.add_member(900), env.guild)
        await env.cog.case_record.callback(env.cog, interaction, env.main, "спам", points, chat, case)
        return interaction

    async def test_warning_is_closed_record(self, rsn_env):
        interaction = await self.case(rsn_env, points=3)

        record = rsn_env.db.get_user_records(1)[0]
        assert record["kind"] == "warn" and record["points_delta"] == -3
        assert rsn_env.db.get_score(1)["points"] == 47
        assert rsn_env.db.get_case(record["case_id"])["status"] == "closed"
        assert rsn_env.created_channels == []
        assert "внесена запись" in rsn_env.main.dm.send.call_args.args[0]
        assert "Запись в деле" in interaction.followup.send.call_args.kwargs["embed"].title

    async def test_chat_without_mute_closed_by_command(self, rsn_env):
        await self.case(rsn_env, chat=True)
        record = rsn_env.db.get_user_records(1)[0]
        case_id = record["case_id"]
        channel = rsn_env.created_channels[0]
        assert rsn_env.db.get_case(case_id)["manual_chat"] == 1
        assert MUTE_ROLE_ID not in roles_of(rsn_env.main)  # без мута

        close = make_interaction(rsn_env.guild.add_member(901), rsn_env.guild)
        await rsn_env.cog.case_close.callback(rsn_env.cog, close, case_id)

        assert rsn_env.db.get_case(case_id)["status"] == "closed"
        channel.delete.assert_awaited_once()
        closing = next(call for call in rsn_env.log.send.call_args_list if "files" in call.kwargs)
        assert closing.args[0].startswith(f"📁 **Дело #{case_id} закрыто**")

    async def test_cannot_close_case_with_active_mute(self, rsn_env):
        await mute(rsn_env, rsn_env.main)
        case_id = case_of(rsn_env, 1)
        close = make_interaction(rsn_env.guild.add_member(901), rsn_env.guild)

        await rsn_env.cog.case_close.callback(rsn_env.cog, close, case_id)

        assert "ещё действует наказание: <@1>" in close.response.send_message.call_args.args[0]


# ---------- ПРАВКА И УДАЛЕНИЕ ПО НОМЕРУ ДЕЛА ----------

class TestEditDelete:
    async def test_ambiguous_case_needs_user(self, rsn_env):
        await mute(rsn_env, rsn_env.main)
        case_id = case_of(rsn_env, 1)
        await mute(rsn_env, rsn_env.twink, case=case_id)
        interaction = make_interaction(rsn_env.guild.add_member(901), rsn_env.guild)

        await rsn_env.cog.case_edit.callback(rsn_env.cog, interaction, case_id, "новая причина", 7, None, None)

        assert "несколько участников" in interaction.response.send_message.call_args.args[0]

    async def test_edit_member_record(self, rsn_env):
        await mute(rsn_env, rsn_env.main)
        case_id = case_of(rsn_env, 1)
        await mute(rsn_env, rsn_env.twink, case=case_id)
        interaction = make_interaction(rsn_env.guild.add_member(901), rsn_env.guild)

        await rsn_env.cog.case_edit.callback(rsn_env.cog, interaction, case_id, "новая причина", 7, None, rsn_env.twink)

        assert [r["reason"] for r in rsn_env.db.get_case_records(case_id)] == ["флуд", "новая причина"]

    async def test_delete_active_mute_record_lifts_roles(self, rsn_env):
        await mute(rsn_env, rsn_env.main)
        case_id = case_of(rsn_env, 1)
        interaction = make_interaction(rsn_env.guild.add_member(901), rsn_env.guild)

        await rsn_env.cog.case_delete.callback(rsn_env.cog, interaction, case_id, None)

        assert MUTE_ROLE_ID not in roles_of(rsn_env.main)
        assert rsn_env.db.get_case(case_id)["status"] == "closed"


# ---------- /rsn_setup ----------

class TestSetup:
    async def test_creates_missing_roles_plans_and_marks_karma(self, rsn_env, monkeypatch):
        import sys
        karma_data = SimpleNamespace(blocked_roles_id=[5])
        monkeypatch.setattr(sys.modules["modules.karma_sistem._state"], "data", karma_data)
        monkeypatch.setitem(rsn.RSN_DATA, "isolation_role_id", 0)
        monkeypatch.setitem(rsn.RSN_DATA, "marker_role_id", 0)
        monkeypatch.setattr(rsn, "save_rsn_config", lambda: None)
        text_channel = FakeTextChannel(5000, "общий", {}, rsn_env.guild)
        rsn_env.guild.channels = [text_channel]
        admin = rsn_env.guild.add_member(962)
        admin.roles.append(rsn_env.admin_role)
        interaction = make_interaction(admin, rsn_env.guild)

        await rsn_env.cog.setup_roles.callback(rsn_env.cog, interaction)

        assert {r.name for r in rsn_env.guild.roles.values()} >= {"Изоляция", "Порицание"}
        marker_id = rsn.RSN_DATA["marker_role_id"]
        assert karma_data.blocked_roles_id == [5, marker_id]  # карма знает о метке
        message = interaction.followup.send.call_args
        assert "запретов: **2**" in message.args[0]

        view = message.kwargs["view"]
        press = SimpleNamespace(
            user=admin,
            response=SimpleNamespace(edit_message=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        await view.apply.callback(press)

        assert text_channel.overwrites[rsn_env.mute_role].send_messages is False
        isolation = rsn_env.guild.get_role(rsn.RSN_DATA["isolation_role_id"])
        assert text_channel.overwrites[isolation].view_channel is False
        assert "поставлено запретов — 2" in press.edit_original_response.call_args.kwargs["content"]
