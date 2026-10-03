"""Тесты таблиц Связи (/leaderboard_cubes, ,lbc): Пары · Со всеми · Цветные — и вкладок."""

from unittest.mock import AsyncMock

import discord
import pytest

from conftest import FakeChannel, add_ozernik, make_interaction, make_message


def bind(env, first: int, second: int, amount: int):
    """Связь между discord_id first и second (создаёт озерников при необходимости)."""
    ids = []
    for discord_id in (first, second):
        ozernik = env.db.get_user_by_discord_id(discord_id)
        ids.append(ozernik.id if ozernik else add_ozernik(env, discord_id))
    env.db.add_bind_karma(*ids, amount)


def only(rows, *discord_ids, key="ozernik"):
    return [row for row in rows if row[key].discord_id in discord_ids]


# ---------- ДАННЫЕ -----------

class TestPairs:
    def test_order_and_levels(self, env):
        bind(env, 1, 2, 5760)
        bind(env, 3, 4, 400)
        bind(env, 1, 3, 30)

        rows = env.karma.get_pairs_leaderboard()

        assert [
            (row["first"].discord_id, row["second"].discord_id, row["bind"].bind_karma, row["level"])
            for row in rows
        ] == [
            (1, 2, 5760, "Золотая"),
            (3, 4, 400, "Белая"),
            (1, 3, 30, "Чёрная"),
        ]

    def test_empty(self, env):
        assert env.karma.get_pairs_leaderboard() == []


class TestTogether:
    def test_sum_and_count(self, env):
        bind(env, 1, 2, 60)
        bind(env, 1, 3, 60)
        bind(env, 1, 4, 60)  # у 1: три связи по часу → 3 часа «со всеми»
        bind(env, 5, 6, 150)

        rows = env.karma.get_together_leaderboard()
        top = [(row["ozernik"].discord_id, row["total_bind_karma"], row["binds"]) for row in rows[:3]]

        assert top[0] == (1, 180, 3)
        assert top[1:] == [(5, 150, 1), (6, 150, 1)]

    def test_number_of_binds_does_not_matter(self, env):
        for n in range(20):
            bind(env, 1, 100 + n, 5)  # 20 связей, 100 минут
        bind(env, 2, 3, 500)          # 1 связь, 500 минут

        rows = only(env.karma.get_together_leaderboard(), 1, 2)

        assert [row["ozernik"].discord_id for row in rows] == [2, 1]


class TestColored:
    def test_sorted_by_gold_then_blue_then_white(self, env):
        # 1: 1 золотая
        bind(env, 1, 100, 5760)
        # 2: 0 золотых, 5 синих
        for n in range(5):
            bind(env, 2, 200 + n, 1440)
        # 3: 0 золотых, 5 синих, 1 белая
        for n in range(5):
            bind(env, 3, 300 + n, 1440)
        bind(env, 3, 310, 360)
        # 4: 30 белых
        for n in range(30):
            bind(env, 4, 400 + n, 360)

        rows = only(env.karma.get_colored_leaderboard(), 1, 2, 3, 4)

        assert [(row["ozernik"].discord_id, row["gold"], row["blue"], row["white"]) for row in rows] == [
            (1, 1, 0, 0),
            (3, 0, 5, 1),
            (2, 0, 5, 0),
            (4, 0, 0, 30),
        ]

    def test_counts_are_exact_not_cumulative(self, env):
        """Золотая связь — только золотая, в синие и белые не идёт."""
        bind(env, 1, 2, 5760)
        bind(env, 1, 3, 1440)
        bind(env, 1, 4, 360)
        bind(env, 1, 5, 10)  # чёрная не считается

        row = only(env.karma.get_colored_leaderboard(), 1)[0]

        assert (row["gold"], row["blue"], row["white"]) == (1, 1, 1)

    def test_full_tie_broken_by_time_together(self, env):
        bind(env, 1, 10, 360)
        bind(env, 1, 11, 5)
        bind(env, 2, 20, 360)
        bind(env, 2, 21, 50)

        rows = only(env.karma.get_colored_leaderboard(), 1, 2)

        assert [row["ozernik"].discord_id for row in rows] == [2, 1]

    def test_only_black_binds_not_listed(self, env):
        bind(env, 1, 2, 359)

        assert env.karma.get_colored_leaderboard() == []


@pytest.mark.parametrize(
    ("minutes", "text"),
    [(0, "0 мин"), (45, "45 мин"), (60, "1 ч"), (360, "6 ч"), (2054, "34 ч 14 мин")],
)
def test_format_hours(env, minutes, text):
    assert env.karma.format_hours(minutes) == text


# ---------- ТАБЛИЦЫ -----------

def description(env, tab, viewer):
    return env.cog.build_bind_tab(tab, viewer, env.guild).description


class TestEmbeds:
    def test_pairs(self, env):
        bind(env, 1, 2, 5760)
        viewer = env.guild.add_member(1)

        embed = env.cog.build_bind_tab("pairs", viewer, env.guild)

        assert embed.title == "Пары"
        assert embed.description.startswith(
            "*Ты должен собрать воспоминания. Они нужны нам для нашего будущего, для полноценного эликсира.*\n\n"
        )
        assert (
            "**🥇 <:cigar:1208007437639225415> <@1> + <@2>**\n"
            "ㅤ  Связь: 🟡 Золотая\n"
            "ㅤ  Вместе: `96 ч`"
        ) in embed.description

    def test_together(self, env):
        for n in range(29):
            bind(env, 1, 100 + n, 70)
        viewer = env.guild.add_member(1)

        embed = env.cog.build_bind_tab("together", viewer, env.guild)

        assert embed.title == "Время"
        assert embed.description.startswith("*Балансируй субстанцию своих прошлых жизней.*\n\n")
        assert (
            "**🥇 <:cigar:1208007437639225415> <@1>**\n"
            "ㅤ  Время: `33 ч 50 мин`\n"
            "ㅤ  Связей: `29`"
        ) in embed.description

    def test_colored(self, env):
        bind(env, 1, 2, 5760)
        bind(env, 1, 3, 400)
        viewer = env.guild.add_member(1)

        embed = env.cog.build_bind_tab("colored", viewer, env.guild)

        assert embed.title == "Кубы"
        assert embed.description.startswith("*Я вижу свои воспоминания... Пойманные в маленькие кубики...*\n\n")
        assert (
            "**🥇 <:cigar:1208007437639225415> <@1>**\n"
            "ㅤ  Связи: 🟡 `1` · 🔵 `0` · ⚪ `1`"
        ) in embed.description

    @pytest.mark.parametrize("tab", ["pairs", "together", "colored"])
    def test_places_like_karma_leaderboards(self, env, tab):
        """Как ,lb и ,lbw: цитата, затем места через пустую строку, подробности — строками с отступом."""
        for n in range(5):
            bind(env, 1, 100 + n, 400 * (n + 1))
        viewer = env.guild.add_member(1)

        blocks = description(env, tab, viewer).split("\n\n")

        assert blocks[0].startswith("*") and blocks[0].endswith("*")  # цитата курсивом
        places = blocks[1:]
        assert len(places) >= 2
        for place in places:
            header, *details = place.split("\n")
            assert header.startswith("**") and "<:cigar:" in header
            assert details and all(line.startswith("ㅤ  ") for line in details)

    def test_karma_leaderboards_stay_multiline(self, env):
        env.db.set_karma(add_ozernik(env, 10), 150)
        viewer = env.guild.add_member(1)

        embed = env.cog.build_leaderboard(viewer, env.guild)

        assert "**🥇 <:cigar:1208007437639225415> <@10>**\nㅤ  Уровень: `1`" in embed.description

    def test_empty_tab_has_placeholder(self, env):
        bind(env, 1, 2, 10)  # только чёрная связь
        viewer = env.guild.add_member(1)

        embed = env.cog.build_bind_tab("colored", viewer, env.guild)

        assert embed.title == "Кубы"
        assert embed.description == "Цветных связей пока ни у кого нет."

    def test_top_10(self, env):
        for n in range(12):
            bind(env, 1000 + n, 2000 + n, n + 1)
        viewer = env.guild.add_member(1)

        assert description(env, "pairs", viewer).count("<:cigar:") == 10


# ---------- ВКЛАДКИ -----------

def tab_buttons(view):
    return {button.label: button for button in view.children}


async def open_tabs(env, user):
    interaction = make_interaction(user, env.guild)
    await env.cog.leaderboard_cubes.callback(env.cog, interaction)
    return interaction.response.send_message.call_args.kwargs


async def click(env, view, label, user):
    interaction = make_interaction(user, env.guild)
    interaction.response.edit_message = AsyncMock()
    await tab_buttons(view)[label].callback(interaction)
    return interaction.response.edit_message.call_args.kwargs


class TestTabs:
    async def test_slash_opens_pairs_tab(self, env):
        bind(env, 1, 2, 60)

        kwargs = await open_tabs(env, env.guild.add_member(1))

        view = kwargs["view"]
        assert kwargs["embed"].title == "Пары"
        assert list(tab_buttons(view)) == ["Пары", "Время", "Кубы"]
        assert tab_buttons(view)["Пары"].disabled is True
        assert tab_buttons(view)["Время"].disabled is False

    async def test_switch_tab(self, env):
        bind(env, 1, 2, 60)
        user = env.guild.add_member(1)
        view = (await open_tabs(env, user))["view"]

        kwargs = await click(env, view, "Время", user)

        assert kwargs["embed"].title == "Время"
        new_view = kwargs["view"]
        assert tab_buttons(new_view)["Время"].disabled is True
        assert tab_buttons(new_view)["Время"].style == discord.ButtonStyle.primary
        assert tab_buttons(new_view)["Пары"].disabled is False

    async def test_anyone_can_switch(self, env):
        bind(env, 1, 2, 60)
        view = (await open_tabs(env, env.guild.add_member(1)))["view"]
        stranger = env.guild.add_member(2)

        kwargs = await click(env, view, "Кубы", stranger)

        assert kwargs["embed"].title == "Кубы"  # цветных связей нет — заглушка вкладки

    async def test_buttons_never_expire_and_survive_restart(self, env):
        view = env.karma.BindLeaderboardView(env.cog)

        # Постоянное меню: без таймаута и с фиксированными custom_id у всех кнопок.
        assert view.timeout is None
        assert view.is_persistent()
        assert [button.custom_id for button in view.children] == [
            "karma:bind_tabs:pairs",
            "karma:bind_tabs:together",
            "karma:bind_tabs:colored",
        ]

    async def test_registered_on_cog_load(self, env, monkeypatch):
        from discord.ext import tasks
        from unittest.mock import MagicMock

        monkeypatch.setattr(tasks.Loop, "start", lambda self, *args, **kwargs: None)
        env.bot.add_view = MagicMock()

        env.cog.cog_load()

        view = env.bot.add_view.call_args.args[0]
        assert isinstance(view, env.karma.BindLeaderboardView)
        assert view.is_persistent()

    async def test_slash_empty(self, env):
        interaction = make_interaction(env.guild.add_member(1), env.guild)

        await env.cog.leaderboard_cubes.callback(env.cog, interaction)

        interaction.response.send_message.assert_awaited_once_with("Нет таблиц лидеров Связи.", ephemeral=True)

    async def test_shortcut_opens_tabs(self, env):
        bind(env, 1, 2, 60)
        member = env.guild.add_member(1)
        message = make_message(member, FakeChannel(3000), env.guild, content=",lbc")

        await env.cog.on_message(message)

        kwargs = message.reply.call_args.kwargs
        assert kwargs["embed"].title == "Пары"
        assert isinstance(kwargs["view"], env.karma.BindLeaderboardView)

    async def test_shortcut_empty(self, env):
        member = env.guild.add_member(1)
        message = make_message(member, FakeChannel(3000), env.guild, content=",lbc")

        await env.cog.on_message(message)

        assert message.reply.call_args.args[0] == "Нет таблиц лидеров Связи."
