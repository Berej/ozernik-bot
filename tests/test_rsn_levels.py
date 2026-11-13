"""РСН 2.0: уровни наказания, метка 1–10, план запретов для /rsn_setup, права чата дела."""

from types import SimpleNamespace

import discord
import pytest

from modules.rsn import _rsn_levels as levels


@pytest.mark.parametrize(("points", "multiplier", "isolation"), [
    (50, 1, False), (31, 1, False),
    (30, 2, True), (11, 2, True),
    (10, 3, True), (1, 3, True), (0, 3, True),
    (None, 1, False),  # старые записи без очков до списания
])
def test_level_by_points_before(points, multiplier, isolation):
    level = levels.level_for(points)

    assert (level.multiplier, level.isolation) == (multiplier, isolation)


@pytest.mark.parametrize(("points", "marker"), [(11, False), (10, True), (1, True), (0, True), (50, False)])
def test_marker(points, marker):
    assert levels.needs_marker(points) is marker


class FakeChannel:
    def __init__(self, channel_id, overwrites=None):
        self.id = channel_id
        self._overwrites = overwrites or {}

    def overwrites_for(self, target):
        return self._overwrites.get(target, discord.PermissionOverwrite())


def test_plan_skips_already_set_and_case_chats():
    mute, isolation = "mute-role", "isolation-role"
    done = FakeChannel(1, {mute: levels.MUTE_OVERWRITE, isolation: levels.ISOLATION_OVERWRITE})
    half = FakeChannel(2, {mute: levels.MUTE_OVERWRITE})
    fresh = FakeChannel(3)
    case_chat = FakeChannel(4)

    plan = levels.plan_overwrites(
        [done, half, fresh, case_chat],
        [(mute, levels.MUTE_OVERWRITE), (isolation, levels.ISOLATION_OVERWRITE)],
        skip_channel_ids={4},
    )

    assert [(channel.id, role) for channel, role, _ in plan] == [
        (2, "isolation-role"), (3, "mute-role"), (3, "isolation-role"),
    ]


def test_mute_role_blocks_writing_and_voice_but_not_viewing():
    overwrite = levels.MUTE_OVERWRITE

    assert overwrite.send_messages is False and overwrite.connect is False and overwrite.add_reactions is False
    assert overwrite.view_channel is None  # видит каналы


def test_case_chat_only_staff_members_and_bot():
    guild = SimpleNamespace(default_role="everyone", me="bot")

    overwrites = levels.case_chat_overwrites(guild, ["admin", "moder"], ["member-1", "member-2"])

    assert overwrites["everyone"].view_channel is False
    for target in ("admin", "moder", "member-1", "member-2", "bot"):
        assert overwrites[target].view_channel is True and overwrites[target].send_messages is True
    assert overwrites["bot"].manage_channels is True
