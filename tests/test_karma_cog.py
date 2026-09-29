"""Тесты Cog'а кармы: начисление за сообщения и голос, недельный сброс, сообщения о повышениях, роли, команды."""

import asyncio
from unittest.mock import AsyncMock

import discord
import pytest

from conftest import (
    FakeChannel,
    FakeRole,
    add_ozernik,
    create_all_roles,
    enable_karma_channel,
    make_interaction,
    make_message,
)


def karma_of(env, member) -> int:
    ozernik = env.db.get_user_by_discord_id(member.id)
    return env.db.get_karma(ozernik.id).karma if ozernik else 0


def weekly_of(env, member) -> int:
    ozernik = env.db.get_user_by_discord_id(member.id)
    return env.db.get_karma(ozernik.id).weekly_karma if ozernik else 0


def bind_of(env, member_1, member_2) -> int:
    first = env.db.get_user_by_discord_id(member_1.id)
    second = env.db.get_user_by_discord_id(member_2.id)
    if first is None or second is None:
        return 0
    bind = env.db.get_karmic_bind(first.id, second.id)
    return bind.bind_karma if bind else 0


@pytest.fixture
def text_channel():
    return FakeChannel(3000, name="general")


# ---------- ПРОВЕРКИ БЛОКИРОВОК -----------

class TestChecks:
    def test_blocked_channels(self, env):
        env.data.blocked_channels_id = [1, 2]

        assert env.cog.check_blocked_channels(1) is True
        assert env.cog.check_blocked_channels(3) is False

    def test_blocked_roles(self, env):
        env.data.blocked_roles_id = [10]

        assert env.cog.check_blocked_roles([FakeRole(5), FakeRole(10)]) is True
        assert env.cog.check_blocked_roles([FakeRole(5)]) is False
        assert env.cog.check_blocked_roles([]) is False

    def test_blocked_users(self, env):
        env.data.blocked_users_id = [7]

        assert env.cog.check_blocked_users(7) is True
        assert env.cog.check_blocked_users(8) is False

    def test_can_get_karma(self, env):
        env.data.blocked_users_id = [2]
        env.data.blocked_roles_id = [10]

        assert env.cog.can_get_karma(env.guild.add_member(1)) is True
        assert env.cog.can_get_karma(env.guild.add_member(2)) is False
        assert env.cog.can_get_karma(env.guild.add_member(3, roles=[FakeRole(10)])) is False
        assert env.cog.can_get_karma(env.guild.add_member(4, bot=True)) is False

    async def test_check_delay(self, env):
        env.data.karma_message_delay = 0

        assert env.cog.check_delay(1) is False
        await asyncio.sleep(0)  # задача кулдауна стартует
        assert env.cog.check_delay(1) is True
        assert env.cog.check_delay(2) is False

        # Даём отработать задаче снятия кулдауна.
        for _ in range(3):
            await asyncio.sleep(0)

        assert env.cog.check_delay(1) is False

    async def test_check_delay_is_immediate(self, env):
        """Два сообщения подряд без паузы: карму получает только первое."""
        assert env.cog.check_delay(1) is False
        assert env.cog.check_delay(1) is True


# ---------- КАРМА ЗА СООБЩЕНИЯ -----------

class TestOnMessage:
    async def test_message_gives_karma_and_weekly(self, env, text_channel):
        member = env.guild.add_member(1)

        await env.cog.on_message(make_message(member, text_channel, env.guild))

        assert karma_of(env, member) == 1
        assert weekly_of(env, member) == 1

    async def test_cooldown_blocks_second_message(self, env, text_channel):
        member = env.guild.add_member(1)

        await env.cog.on_message(make_message(member, text_channel, env.guild))
        await asyncio.sleep(0)
        await env.cog.on_message(make_message(member, text_channel, env.guild))

        assert karma_of(env, member) == 1

    async def test_direct_message_ignored(self, env, text_channel):
        member = env.guild.add_member(1)

        await env.cog.on_message(make_message(member, text_channel, None))

        assert env.db.get_user_by_discord_id(1) is None

    async def test_bot_message_ignored(self, env, text_channel):
        member = env.guild.add_member(1, bot=True)

        await env.cog.on_message(make_message(member, text_channel, env.guild))

        assert env.db.get_user_by_discord_id(1) is None

    async def test_non_member_ignored(self, env, text_channel):
        stranger = env.guild.add_member(1)
        env.guild.members.remove(stranger)

        await env.cog.on_message(make_message(stranger, text_channel, env.guild))

        assert env.db.get_user_by_discord_id(1) is None

    async def test_blocked_channel_gives_no_karma_and_no_cooldown(self, env, text_channel):
        member = env.guild.add_member(1)
        blocked = FakeChannel(4000)
        env.data.blocked_channels_id = [blocked.id]

        await env.cog.on_message(make_message(member, blocked, env.guild))
        assert karma_of(env, member) == 0

        await env.cog.on_message(make_message(member, text_channel, env.guild))
        assert karma_of(env, member) == 1

    async def test_blocked_role_gives_no_karma(self, env, text_channel):
        env.data.blocked_roles_id = [10]
        member = env.guild.add_member(1, roles=[FakeRole(10)])

        await env.cog.on_message(make_message(member, text_channel, env.guild))

        assert karma_of(env, member) == 0

    async def test_blocked_user_gives_no_karma(self, env, text_channel):
        env.data.blocked_users_id = [1]
        member = env.guild.add_member(1)

        await env.cog.on_message(make_message(member, text_channel, env.guild))

        assert karma_of(env, member) == 0

    async def test_rank_shortcut_replies_with_card_without_karma(self, env, text_channel):
        member = env.guild.add_member(1)
        message = make_message(member, text_channel, env.guild, content=",r")

        await env.cog.on_message(message)

        message.reply.assert_awaited_once()
        assert isinstance(message.reply.call_args.kwargs["file"], discord.File)
        assert karma_of(env, member) == 0

    async def test_level_up_sends_message(self, env, text_channel):
        channel = enable_karma_channel(env)
        create_all_roles(env)
        member = env.guild.add_member(1)
        env.db.set_karma(env.bot.db_ensure_user(member).id, 99)

        await env.cog.on_message(make_message(member, text_channel, env.guild))

        assert karma_of(env, member) == 100
        channel.send.assert_awaited_once()
        embed = channel.send.call_args.kwargs["embed"]
        assert embed.title == f"**{member.display_name} достигает 1 уровня**"
        # Выдана роль Преты (100 кармы).
        assert env.data.karma_roles["preta"]["role_id"] in [role.id for role in member.roles]

    async def test_no_level_up_message_without_level_change(self, env, text_channel):
        channel = enable_karma_channel(env)
        member = env.guild.add_member(1)

        await env.cog.on_message(make_message(member, text_channel, env.guild))

        channel.send.assert_not_awaited()


# ---------- ГОЛОСОВАЯ КАРМА -----------

class TestVoiceKarma:
    def voice(self, env, *members, channel_id=6000):
        channel = FakeChannel(channel_id, members=list(members))
        env.guild.voice_channels.append(channel)
        return channel

    async def test_pair_in_voice_gets_bind_karma(self, env):
        a, b = env.guild.add_member(1), env.guild.add_member(2)
        self.voice(env, a, b)

        await env.cog.voice_karma_check()
        await env.cog.voice_karma_check()

        assert bind_of(env, a, b) == 2
        # Обычная карма за голос не начисляется.
        assert karma_of(env, a) == 0

    async def test_three_members_give_three_pairs(self, env):
        a, b, c = (env.guild.add_member(i) for i in (1, 2, 3))
        self.voice(env, a, b, c)

        await env.cog.voice_karma_check()

        assert (bind_of(env, a, b), bind_of(env, a, c), bind_of(env, b, c)) == (1, 1, 1)

    async def test_channels_are_independent(self, env):
        a, b, c, d = (env.guild.add_member(i) for i in (1, 2, 3, 4))
        self.voice(env, a, b, channel_id=6000)
        self.voice(env, c, d, channel_id=6001)

        await env.cog.voice_karma_check()

        assert bind_of(env, a, b) == 1
        assert bind_of(env, c, d) == 1
        assert bind_of(env, a, c) == 0

    async def test_alone_in_voice_gets_nothing(self, env):
        a = env.guild.add_member(1)
        self.voice(env, a)

        await env.cog.voice_karma_check()

        assert env.db.get_user_by_discord_id(1) is None

    async def test_afk_channel_ignored(self, env):
        a, b = env.guild.add_member(1), env.guild.add_member(2)
        env.guild.afk_channel = self.voice(env, a, b)

        await env.cog.voice_karma_check()

        assert bind_of(env, a, b) == 0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"bot": True},
            {"self_deaf": True},
            {"deaf": True},
        ],
    )
    async def test_excluded_members(self, env, kwargs):
        a = env.guild.add_member(1)
        b = env.guild.add_member(2, **kwargs)
        self.voice(env, a, b)

        await env.cog.voice_karma_check()

        assert bind_of(env, a, b) == 0

    async def test_blocked_user_and_role_excluded(self, env):
        env.data.blocked_users_id = [2]
        env.data.blocked_roles_id = [10]
        a = env.guild.add_member(1)
        b = env.guild.add_member(2)
        c = env.guild.add_member(3, roles=[FakeRole(10)])
        self.voice(env, a, b, c)

        await env.cog.voice_karma_check()

        assert env.db.get_top_karmic_binds() == []

    async def test_self_mute_still_counts(self, env):
        a = env.guild.add_member(1)
        b = env.guild.add_member(2, self_mute=True)
        self.voice(env, a, b)

        await env.cog.voice_karma_check()

        assert bind_of(env, a, b) == 1

    async def test_not_ready_does_nothing(self, env):
        a, b = env.guild.add_member(1), env.guild.add_member(2)
        self.voice(env, a, b)
        env.bot.ready = False

        await env.cog.voice_karma_check()

        assert bind_of(env, a, b) == 0

    async def test_first_bind_gives_black_cube_message(self, env):
        channel = enable_karma_channel(env)
        create_all_roles(env)
        a, b = env.guild.add_member(1), env.guild.add_member(2)
        self.voice(env, a, b)

        await env.cog.voice_karma_check()

        # Оба получили Черный Куб: два сообщения и роль.
        assert channel.send.await_count == 2
        black_id = env.data.cube_roles["black_cube"]["role_id"]
        assert black_id in [role.id for role in a.roles]
        assert black_id in [role.id for role in b.roles]

    async def test_bind_up_postcard_when_bind_reaches_new_cube(self, env):
        channel = enable_karma_channel(env)
        create_all_roles(env)
        a, b = env.guild.add_member(1), env.guild.add_member(2)
        first = env.bot.db_ensure_user(a).id
        second = env.bot.db_ensure_user(b).id
        env.db.add_bind_karma(first, second, 359)
        self.voice(env, a, b)

        await env.cog.voice_karma_check()

        texts = [call.args[0] for call in channel.send.await_args_list if call.args]
        assert f"{a.mention} и {b.mention} получили воспоминание!" in texts


# ---------- НЕДЕЛЬНЫЙ СБРОС -----------

class TestWeeklyReset:
    def give_weekly(self, env, discord_id: int, amount: int):
        user_id = add_ozernik(env, discord_id)
        env.db.add_karma(user_id, amount, weekly=True)
        return user_id

    async def test_first_run_remembers_week_without_reset(self, env):
        user_id = self.give_weekly(env, 1, 5)

        await env.cog.weekly_countdown()

        assert env.data.weekly_reset_week == env.cog.get_week_start()
        assert env.db.get_karma(user_id).weekly_karma == 5

    async def test_same_week_no_reset(self, env):
        user_id = self.give_weekly(env, 1, 5)
        env.data.weekly_reset_week = env.cog.get_week_start()

        await env.cog.weekly_countdown()

        assert env.db.get_karma(user_id).weekly_karma == 5

    async def test_new_week_announces_and_resets(self, env):
        channel = enable_karma_channel(env)
        ids = [self.give_weekly(env, 1, 30), self.give_weekly(env, 2, 20),
               self.give_weekly(env, 3, 10), self.give_weekly(env, 4, 5)]
        env.data.weekly_reset_week = "2000-01-03"

        await env.cog.weekly_countdown()

        channel.send.assert_awaited_once()
        view = channel.send.call_args.kwargs["view"]
        text = view.children[0].children[0].content
        assert text == (
            "# Победители Недели!\n"
            "## 1. <@1> — `30` к.\n"
            "## 2. <@2> — `20` к.\n"
            "## 3. <@3> — `10` к."
        )
        assert all(env.db.get_karma(user_id).weekly_karma == 0 for user_id in ids)
        assert env.data.weekly_reset_week == env.cog.get_week_start()

    async def test_less_than_three_winners(self, env):
        channel = enable_karma_channel(env)
        self.give_weekly(env, 1, 30)
        self.give_weekly(env, 2, 0)
        env.data.weekly_reset_week = "2000-01-03"

        await env.cog.weekly_countdown()

        text = channel.send.call_args.kwargs["view"].children[0].children[0].content
        assert text == "# Победители Недели!\n## 1. <@1> — `30` к."

    async def test_no_winners_no_message_but_reset(self, env):
        channel = enable_karma_channel(env)
        self.give_weekly(env, 1, 0)
        env.data.weekly_reset_week = "2000-01-03"

        await env.cog.weekly_countdown()

        channel.send.assert_not_awaited()
        assert env.data.weekly_reset_week == env.cog.get_week_start()

    async def test_missing_channel_still_resets(self, env):
        user_id = self.give_weekly(env, 1, 30)
        env.data.weekly_reset_week = "2000-01-03"

        await env.cog.weekly_countdown()

        assert env.db.get_karma(user_id).weekly_karma == 0

    async def test_send_failure_still_resets(self, env):
        channel = enable_karma_channel(env)
        channel.send.side_effect = discord.HTTPException(AsyncMock(status=500), "fail")
        user_id = self.give_weekly(env, 1, 30)
        env.data.weekly_reset_week = "2000-01-03"

        await env.cog.weekly_countdown()

        assert env.db.get_karma(user_id).weekly_karma == 0
        assert env.data.weekly_reset_week == env.cog.get_week_start()

    def test_week_start_is_monday(self, env):
        from datetime import date

        week_start = date.fromisoformat(env.cog.get_week_start())

        assert week_start.weekday() == 0


# ---------- СООБЩЕНИЯ О ПОВЫШЕНИЯХ -----------

class TestLevelUpMessages:
    async def test_no_channel_no_message(self, env):
        create_all_roles(env)
        member = env.guild.add_member(1)
        env.bot.db_ensure_user(member)

        await env.cog.give_level_up_message(member, 99, 100)

        assert member.add_roles.await_count == 0

    async def test_no_increase_no_message(self, env):
        channel = enable_karma_channel(env)
        member = env.guild.add_member(1)

        await env.cog.give_level_up_message(member, 100, 100)

        channel.send.assert_not_awaited()

    async def test_one_message_per_level(self, env):
        channel = enable_karma_channel(env)
        create_all_roles(env)
        member = env.guild.add_member(1)
        env.db.set_karma(env.bot.db_ensure_user(member).id, 300)
        env.levels["1"] = "Первый {role}"
        env.levels["3"] = "Третий"

        await env.cog.give_level_up_message(member, 99, 300)

        assert channel.send.await_count == 3
        embeds = [call.kwargs["embed"] for call in channel.send.await_args_list]
        preta_id = env.data.karma_roles["preta"]["role_id"]
        assert [embed.description for embed in embeds] == [f"Первый <@&{preta_id}>", "", "Третий"]
        assert [embed.title for embed in embeds] == [
            f"**{member.display_name} достигает {level} уровня**" for level in (1, 2, 3)
        ]

    async def test_member_without_avatar_and_guild_without_icon(self, env):
        channel = enable_karma_channel(env)
        create_all_roles(env)
        member = env.guild.add_member(1, avatar=False)
        env.guild.icon = None
        env.db.set_karma(env.bot.db_ensure_user(member).id, 100)

        await env.cog.give_level_up_message(member, 99, 100)

        channel.send.assert_awaited_once()
        embed = channel.send.call_args.kwargs["embed"]
        assert embed.thumbnail.url == member.display_avatar.url
        assert embed.footer.icon_url is None

    async def test_cube_up_same_cube_no_message(self, env):
        channel = enable_karma_channel(env)
        member = env.guild.add_member(1)
        cube = env.data.cube_roles["black_cube"]

        await env.cog.give_cube_up_message(member, cube, cube)

        channel.send.assert_not_awaited()

    @pytest.mark.parametrize(
        ("bind_karma", "quote"),
        [
            (1, "«Столкнись со своими демонами»"),
            (360, "«Переживи свою прошлую жизнь вновь»"),
            (1440, "«Прошлое никогда не умирает, оно даже не прошлое»"),
            (5760, "«Воспоминания — это не только ключ к прошлому, но и к будущему»"),
        ],
    )
    async def test_cube_up_message_text(self, env, bind_karma, quote):
        channel = enable_karma_channel(env)
        create_all_roles(env)
        member = env.guild.add_member(1)
        me = env.bot.db_ensure_user(member).id
        for n in range(10):
            env.db.add_bind_karma(me, add_ozernik(env, 100 + n), bind_karma)

        await env.cog.give_cube_up_message(member, None, env.karma.get_cube_status(me))

        embed = channel.send.call_args.kwargs["embed"]
        assert embed.title == f"**{member.display_name} получил новый Куб!**"
        assert quote in embed.description

    async def test_bind_up_only_on_cube_change(self, env):
        channel = enable_karma_channel(env)
        a, b = env.guild.add_member(1), env.guild.add_member(2)

        await env.cog.give_bind_up_message(a, b, 10, 11)

        channel.send.assert_not_awaited()


# ---------- РОЛИ -----------

class TestRoles:
    async def test_existing_role_is_returned(self, env):
        create_all_roles(env)

        role = await env.cog.roles.get_sansara_role("preta")

        assert role.id == env.data.karma_roles["preta"]["role_id"]
        env.guild.create_role.assert_not_awaited()
        role.edit.assert_awaited_once()  # выставляется иконка

    async def test_fetch_role_when_not_cached(self, env):
        fetched = FakeRole(555)
        env.guild.fetch_role.side_effect = None
        env.guild.fetch_role.return_value = fetched

        role = await env.cog.roles.get_cube_role("black_cube")

        assert role is fetched

    async def test_missing_role_is_recreated_and_saved(self, env):
        role = await env.cog.roles.get_sansara_role("naraka")

        assert role.name == "Нарака"
        assert env.data.karma_roles["naraka"]["role_id"] == role.id

        cube = await env.cog.roles.get_cube_role("gold_cube")
        assert cube.name == "Золотой Куб"
        assert env.data.cube_roles["gold_cube"]["role_id"] == cube.id

    async def test_missing_role_without_recreate(self, env):
        assert await env.cog.roles.get_sansara_role("naraka", recreate=False) is None
        assert await env.cog.roles.get_cube_roles(recreate=False) is None

    async def test_no_guild(self, env):
        env.bot.guild = None

        assert await env.cog.roles.get_sansara_role("naraka") is None

    async def test_get_all_roles(self, env):
        create_all_roles(env)

        assert len(await env.cog.roles.get_sansara_roles()) == 6
        assert len(await env.cog.roles.get_cube_roles()) == 4

    async def test_update_sansara_roles_replaces_old(self, env):
        create_all_roles(env)
        naraka = env.guild.get_role(env.data.karma_roles["naraka"]["role_id"])
        other = FakeRole(1)
        member = env.guild.add_member(1, roles=[naraka, other])
        env.db.set_karma(env.bot.db_ensure_user(member).id, 1000)

        role = await env.cog.update_sansara_roles(member)

        assert role.id == env.data.karma_roles["animal"]["role_id"]
        assert member.roles == [other, role]

    async def test_update_cube_roles_replaces_old(self, env):
        create_all_roles(env)
        black = env.guild.get_role(env.data.cube_roles["black_cube"]["role_id"])
        member = env.guild.add_member(1, roles=[black])
        me = env.bot.db_ensure_user(member).id
        for n in range(10):
            env.db.add_bind_karma(me, add_ozernik(env, 100 + n), 360)

        role = await env.cog.update_cube_roles(member)

        assert role.id == env.data.cube_roles["white_cube"]["role_id"]
        assert member.roles == [role]


# ---------- КОМАНДЫ -----------

class TestCommands:
    async def test_settings_only_for_admins(self, env):
        user = env.guild.add_member(1)
        interaction = make_interaction(user, env.guild)

        await env.cog.settings_karma.callback(env.cog, interaction)

        interaction.response.send_message.assert_awaited_once_with("Только для Администраторов.", ephemeral=True)

    async def test_settings_opens_menu_for_admin(self, env):
        user = env.guild.add_member(1)
        user.guild_permissions.administrator = True
        interaction = make_interaction(user, env.guild)

        await env.cog.settings_karma.callback(env.cog, interaction)

        view = interaction.response.send_message.call_args.kwargs["view"]
        assert isinstance(view, discord.ui.LayoutView)
        assert view.children[0].children[0].content == "# Карма"

    async def test_rank_sansara(self, env):
        user = env.guild.add_member(1)
        env.bot.db_ensure_user(user)
        interaction = make_interaction(user, env.guild)

        await env.cog.rank_sansara.callback(env.cog, interaction)

        file = interaction.response.send_message.call_args.kwargs["file"]
        assert file.filename == "user1_sansara_rank.png"

    async def test_rank_sansara_for_other_user(self, env):
        user = env.guild.add_member(1)
        other = env.guild.add_member(2)
        env.bot.db_ensure_user(other)
        interaction = make_interaction(user, env.guild)

        await env.cog.rank_sansara.callback(env.cog, interaction, other)

        assert interaction.response.send_message.call_args.kwargs["file"].filename == "user2_sansara_rank.png"

    async def test_rank_cube(self, env):
        user = env.guild.add_member(1)
        other = env.guild.add_member(2)
        env.db.add_bind_karma(env.bot.db_ensure_user(user).id, env.bot.db_ensure_user(other).id, 400)
        interaction = make_interaction(user, env.guild)

        await env.cog.rank_cube.callback(env.cog, interaction)

        interaction.response.defer.assert_awaited_once()
        assert interaction.followup.send.call_args.kwargs["file"].filename == "user1_cube_rank.png"

    async def test_leaderboard(self, env):
        user = env.guild.add_member(1)
        env.db.set_karma(add_ozernik(env, 10), 1500)
        env.db.set_karma(add_ozernik(env, 20), 150)
        interaction = make_interaction(user, env.guild)

        await env.cog.leaderboard.callback(env.cog, interaction)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert embed.title == "Таблица лидеров"
        assert "🥇 <:cigar:1208007437639225415> <@10>" in embed.description
        assert "Уровень: `12`" in embed.description
        assert "Карма: `1500/1600`" in embed.description
        assert "🥈 <:cigar:1208007437639225415> <@20>" in embed.description

    async def test_leaderboard_top_10_only(self, env):
        user = env.guild.add_member(1)
        for n in range(12):
            env.db.set_karma(add_ozernik(env, 100 + n), n + 1)
        interaction = make_interaction(user, env.guild)

        await env.cog.leaderboard.callback(env.cog, interaction)

        description = interaction.response.send_message.call_args.kwargs["embed"].description
        assert description.count("<:cigar:") == 10

    async def test_leaderboard_weekly(self, env):
        user = env.guild.add_member(1)
        env.db.add_karma(add_ozernik(env, 10), 42, weekly=True)
        interaction = make_interaction(user, env.guild)

        await env.cog.leaderboard_weekly.callback(env.cog, interaction)

        description = interaction.response.send_message.call_args.kwargs["embed"].description
        assert "🥇 <:cigar:1208007437639225415> <@10>" in description
        assert "Карма: `42/100`" in description

    @pytest.mark.parametrize("command", ["leaderboard", "leaderboard_weekly"])
    async def test_empty_leaderboard_answers_once(self, env, command):
        user = env.guild.add_member(1)
        interaction = make_interaction(user, env.guild)

        await getattr(env.cog, command).callback(env.cog, interaction)

        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.call_args.kwargs == {"ephemeral": True}


# ---------- ЖИЗНЕННЫЙ ЦИКЛ -----------

async def test_setup_registers_cog(env):
    bot = AsyncMock()

    await env.karma.setup(bot)

    cog = bot.add_cog.call_args.args[0]
    assert isinstance(cog, env.karma.KarmaSistem)


def test_cog_commands(env):
    names = {command.name for command in env.cog.get_app_commands()}

    assert names == {"settings_karma", "rank_sansara", "rank_cube", "leaderboard", "leaderboard_weekly"}
