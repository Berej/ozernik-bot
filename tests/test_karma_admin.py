"""Тесты админских команд кармы (/karma_add, /karma_remove, /karma_set, /karma_status) и топа Кубов."""

from types import SimpleNamespace

import pytest

from conftest import FakeChannel, add_ozernik, create_all_roles, make_interaction


def admin(env, member_id: int = 1):
    user = env.guild.add_member(member_id)
    user.guild_permissions.administrator = True
    return user


def karma(env, member):
    return env.db.get_karma(env.db.get_user_by_discord_id(member.id).id)


def answer(interaction) -> str:
    return interaction.followup.send.call_args.args[0]


def status_choice(value: str):
    names = {"asur": "Асур", "deva": "Дэва", "none": "Снять статус"}
    return SimpleNamespace(name=names[value], value=value)


@pytest.fixture
def log_channel(env):
    channel = FakeChannel(7000, name="logs")
    env.bot.channels[channel.id] = channel
    env.data.log_channel_id = channel.id
    return channel


# ---------- ДОБАВИТЬ / ОТНЯТЬ / УСТАНОВИТЬ -----------

class TestKarmaEdit:
    @pytest.mark.parametrize("command", ["karma_add", "karma_remove", "karma_set"])
    async def test_only_admins(self, env, command):
        user = env.guild.add_member(1)
        target = env.guild.add_member(2)
        interaction = make_interaction(user, env.guild)

        await getattr(env.cog, command).callback(env.cog, interaction, target, 10)

        interaction.response.send_message.assert_awaited_once_with("Только для Администраторов.", ephemeral=True)
        assert env.db.get_user_by_discord_id(2) is None

    async def test_add(self, env):
        target = env.guild.add_member(2)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_add.callback(env.cog, interaction, target, 150)

        assert (karma(env, target).karma, karma(env, target).weekly_karma) == (150, 150)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        assert answer(interaction) == (
            "**Карма добавлена:** <@2>\n"
            "Карма: `0` → `150` (уровень 0 → 1)\n"
            "Недельная: `0` → `150`"
        )

    async def test_add_is_not_gift_and_congratulates_once(self, env):
        channel = env.karma_channel
        env.data.karma_channel_id = channel.id
        create_all_roles(env)
        target = env.guild.add_member(2)

        await env.cog.karma_add.callback(env.cog, make_interaction(admin(env), env.guild), target, 1000)

        assert karma(env, target).gift_karma == 0
        # 0 → 10 уровень: одно сообщение о последнем уровне, без промежуточных.
        channel.send.assert_awaited_once()
        assert channel.send.call_args.kwargs["embed"].title == f"**{target.display_name} достигает 10 уровня**"

    async def test_set_up_congratulates_once(self, env):
        channel = env.karma_channel
        env.data.karma_channel_id = channel.id
        create_all_roles(env)
        target = env.guild.add_member(2)

        await env.cog.karma_set.callback(env.cog, make_interaction(admin(env), env.guild), target, 4999)

        channel.send.assert_awaited_once()
        assert channel.send.call_args.kwargs["embed"].title == f"**{target.display_name} достигает 24 уровня**"

    @pytest.mark.parametrize(
        ("command", "amount"),
        [
            ("karma_add", 50),      # 100 → 150: уровень тот же
            ("karma_remove", 50),   # уровень вниз
            ("karma_set", 0),       # обнуление
        ],
    )
    async def test_no_congratulations_without_level_up(self, env, command, amount):
        channel = env.karma_channel
        env.data.karma_channel_id = channel.id
        create_all_roles(env)
        target = env.guild.add_member(2)
        env.db.set_karma(env.bot.db_ensure_user(target).id, 100)

        await getattr(env.cog, command).callback(env.cog, make_interaction(admin(env), env.guild), target, amount)

        channel.send.assert_not_awaited()

    async def test_bot_gets_no_congratulations(self, env):
        channel = env.karma_channel
        env.data.karma_channel_id = channel.id
        bot_member = env.guild.add_member(2, bot=True)

        await env.cog.karma_add.callback(env.cog, make_interaction(admin(env), env.guild), bot_member, 1000)

        channel.send.assert_not_awaited()

    async def test_add_updates_sansara_role(self, env):
        create_all_roles(env)
        roles = env.data.karma_roles
        naraka = env.guild.get_role(roles["naraka"]["role_id"])
        target = env.guild.add_member(2, roles=[naraka])

        await env.cog.karma_add.callback(env.cog, make_interaction(admin(env), env.guild), target, 1000)

        assert [role.id for role in target.roles] == [roles["animal"]["role_id"]]

    async def test_remove_not_below_zero(self, env):
        target = env.guild.add_member(2)
        user_id = env.bot.db_ensure_user(target).id
        env.db.add_karma(user_id, 30, weekly=True)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_remove.callback(env.cog, interaction, target, 100)

        assert (karma(env, target).karma, karma(env, target).weekly_karma) == (0, 0)
        assert answer(interaction).startswith("**Карма отнята:** <@2>\nКарма: `30` → `0`")

    async def test_set(self, env):
        target = env.guild.add_member(2)
        user_id = env.bot.db_ensure_user(target).id
        env.db.add_karma(user_id, 100)
        env.db.add_karma(user_id, 20, weekly=True)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_set.callback(env.cog, interaction, target, 150)

        assert (karma(env, target).karma, karma(env, target).weekly_karma) == (150, 50)
        assert answer(interaction).startswith("**Карма установлена:**")

    async def test_reset_bot(self, env):
        """Главный сценарий: обнулить бота, который успел набрать карму."""
        create_all_roles(env)
        bot_member = env.guild.add_member(2, bot=True)
        user_id = env.bot.db_ensure_user(bot_member).id
        env.db.add_karma(user_id, 500, weekly=True)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_set.callback(env.cog, interaction, bot_member, 0)

        assert (karma(env, bot_member).karma, karma(env, bot_member).weekly_karma) == (0, 0)
        assert answer(interaction).startswith("**Карма обнулена:** <@2>")
        bot_member.add_roles.assert_not_awaited()  # ботам роли Сансары не выдаются
        assert env.db.get_top_weekly_karma()[0][1].weekly_karma == 0

    async def test_log_channel(self, env, log_channel):
        target = env.guild.add_member(2)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_add.callback(env.cog, interaction, target, 5)

        log_channel.send.assert_awaited_once()
        text = log_channel.send.call_args.args[0]
        assert text.startswith("<@1> → **Карма добавлена:** <@2>")

    async def test_no_log_channel_is_fine(self, env):
        target = env.guild.add_member(2)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_add.callback(env.cog, interaction, target, 5)

        interaction.followup.send.assert_awaited_once()

    async def test_role_update_failure_is_reported(self, env, monkeypatch):
        async def broken(member):
            raise RuntimeError("нет прав")

        monkeypatch.setattr(env.cog, "update_sansara_roles", broken)
        target = env.guild.add_member(2)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_add.callback(env.cog, interaction, target, 5)

        assert karma(env, target).karma == 5
        assert answer(interaction).endswith("-# Не удалось обновить роль Сансары, см. консоль.")


# ---------- АСУР / ДЭВА -----------

class TestKarmaStatus:
    def target_with_karma(self, env, amount: int):
        target = env.guild.add_member(2)
        env.db.set_karma(env.bot.db_ensure_user(target).id, amount)
        return target

    async def test_only_admins(self, env):
        user = env.guild.add_member(1)
        target = self.target_with_karma(env, 5000)
        interaction = make_interaction(user, env.guild)

        await env.cog.karma_status.callback(env.cog, interaction, target, status_choice("asur"))

        interaction.response.send_message.assert_awaited_once_with("Только для Администраторов.", ephemeral=True)
        assert karma(env, target).status is None

    @pytest.mark.parametrize(("value", "name"), [("asur", "Асур"), ("deva", "Дэва")])
    async def test_give_status_to_human(self, env, value, name):
        create_all_roles(env)
        target = self.target_with_karma(env, 5000)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_status.callback(env.cog, interaction, target, status_choice(value))

        assert karma(env, target).status == value
        assert answer(interaction) == f"**Статус изменён:** <@2>\n`Человек` → `{name}`"
        assert [role.id for role in target.roles] == [env.data.karma_roles[value]["role_id"]]

    async def test_status_requires_human(self, env):
        target = self.target_with_karma(env, 4999)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_status.callback(env.cog, interaction, target, status_choice("deva"))

        assert karma(env, target).status is None
        message = interaction.response.send_message.call_args.args[0]
        assert message == "Статус можно выдать только достигшему Человека (`5000` кармы). У <@2> `4999`."

    async def test_remove_status(self, env):
        target = self.target_with_karma(env, 6000)
        env.db.set_karma_status(karma(env, target).user_id, "asur")
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_status.callback(env.cog, interaction, target, status_choice("none"))

        assert karma(env, target).status is None
        assert answer(interaction) == "**Статус изменён:** <@2>\n`Асур` → `Человек`"

    async def test_same_status(self, env):
        target = self.target_with_karma(env, 6000)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_status.callback(env.cog, interaction, target, status_choice("none"))

        interaction.response.send_message.assert_awaited_once_with("У <@2> уже статус `Человек`.", ephemeral=True)

    @pytest.mark.parametrize(
        ("command", "amount"),
        [
            ("karma_remove", 1001),  # 6000 → 4999
            ("karma_set", 4999),
            ("karma_set", 0),
        ],
    )
    async def test_status_dropped_below_human(self, env, command, amount):
        create_all_roles(env)
        target = self.target_with_karma(env, 6000)
        env.db.set_karma_status(karma(env, target).user_id, "deva")
        interaction = make_interaction(admin(env), env.guild)

        await getattr(env.cog, command).callback(env.cog, interaction, target, amount)

        assert karma(env, target).status is None
        assert answer(interaction).endswith("\nСтатус `Дэва` снят: карма ниже Человека (`5000`).")
        # Роль Дэвы заменена обычной ролью по карме.
        deva_id = env.data.karma_roles["deva"]["role_id"]
        assert deva_id not in [role.id for role in target.roles]
        assert len(target.roles) == 1

    @pytest.mark.parametrize(
        ("command", "amount"),
        [
            ("karma_remove", 1000),  # 6000 → 5000: всё ещё Человек
            ("karma_set", 5000),
            ("karma_add", 10),
        ],
    )
    async def test_status_kept_while_human(self, env, command, amount):
        target = self.target_with_karma(env, 6000)
        env.db.set_karma_status(karma(env, target).user_id, "asur")
        interaction = make_interaction(admin(env), env.guild)

        await getattr(env.cog, command).callback(env.cog, interaction, target, amount)

        assert karma(env, target).status == "asur"
        assert "снят" not in answer(interaction)

    async def test_switch_asur_to_deva(self, env):
        target = self.target_with_karma(env, 6000)
        env.db.set_karma_status(karma(env, target).user_id, "asur")

        await env.cog.karma_status.callback(env.cog, make_interaction(admin(env), env.guild), target, status_choice("deva"))

        assert karma(env, target).status == "deva"


# ---------- ТОП КУБОВ -----------

def bind(env, first: int, second: int, amount: int):
    ids = []
    for discord_id in (first, second):
        ozernik = env.db.get_user_by_discord_id(discord_id)
        ids.append(ozernik.id if ozernik else add_ozernik(env, discord_id))
    env.db.add_bind_karma(*ids, amount)


class TestCubeLeaderboard:
    def test_empty(self, env):
        assert env.karma.get_cube_leaderboard() == []

    def test_order_cube_then_total(self, env):
        # 1: белый куб (10 белых связей)
        for n in range(10):
            bind(env, 1, 100 + n, 360)
        # 2: чёрный куб, 3 связи, много минут
        for n in range(3):
            bind(env, 2, 200 + n, 300)
        # 3: чёрный куб, 3 связи, мало минут
        for n in range(3):
            bind(env, 3, 300 + n, 10)

        rows = [row for row in env.karma.get_cube_leaderboard() if row["ozernik"].discord_id in (1, 2, 3)]
        top = [(row["ozernik"].discord_id, row["cube"]["tag_name"], row["cube_binds"], row["total_bind_karma"]) for row in rows]

        assert top == [
            (1, "white_cube", 10, 3600),
            (2, "black_cube", 3, 900),
            (3, "black_cube", 3, 30),
        ]

    def test_same_cube_ordered_by_time_not_by_number_of_binds(self, env):
        """Случай со скриншота: у всех Чёрный Куб — решает время, а не число людей."""
        for n in range(29):
            bind(env, 1, 100 + n, 70)   # 29 связей, 2030 минут
        for n in range(5):
            bind(env, 2, 200 + n, 500)  # 5 связей, 2500 минут
        # Одна белая связь (из 10 нужных) Куб не повышает и места не даёт.
        bind(env, 3, 300, 360)
        bind(env, 3, 301, 1)

        rows = [row for row in env.karma.get_cube_leaderboard() if row["ozernik"].discord_id in (1, 2, 3)]
        top = [(row["ozernik"].discord_id, row["cube"]["tag_name"], row["total_bind_karma"]) for row in rows]

        assert top == [
            (2, "black_cube", 2500),
            (1, "black_cube", 2030),
            (3, "black_cube", 361),
        ]

    def test_higher_cube_beats_more_time(self, env):
        for n in range(10):
            bind(env, 1, 100 + n, 360)   # Белый Куб, 3600 минут
        for n in range(3):
            bind(env, 2, 200 + n, 5000)  # Чёрный Куб, 15000 минут

        rows = env.karma.get_cube_leaderboard()

        assert [row["ozernik"].discord_id for row in rows[:2]] == [1, 2]

    def test_cube_binds_counts_only_binds_of_cube_level(self, env):
        for n in range(10):
            bind(env, 1, 100 + n, 1440)  # 10 синих
        bind(env, 1, 200, 5)  # чёрная — в счёт синего куба не идёт

        row = env.karma.get_cube_leaderboard()[0]

        assert (row["cube"]["tag_name"], row["cube_binds"], row["total_bind_karma"]) == ("blue_cube", 10, 14405)

    def test_both_sides_of_bind_are_counted(self, env):
        bind(env, 1, 2, 50)

        rows = env.karma.get_cube_leaderboard()

        assert {row["ozernik"].discord_id for row in rows} == {1, 2}

    async def test_command(self, env):
        for n in range(10):
            bind(env, 1, 100 + n, 360)
        bind(env, 2, 3, 400)  # больше, чем у каждого из 10 партнёров первого
        user = env.guild.add_member(1)
        interaction = make_interaction(user, env.guild)

        await env.cog.leaderboard_cubes.callback(env.cog, interaction)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert embed.title == "Таблица лидеров Кубов"
        assert (
            "🥇 <:cigar:1208007437639225415> <@1>**\n"
            "ㅤ  Куб: `Белый Куб` (10 связей)\n"
            "ㅤ  Связь: `3600` ед. с. (2 дня, 12 часов)"
        ) in embed.description
        assert "Куб: `Черный Куб` (1 связь)" in embed.description
        assert "Связь: `400` ед. с. (6 часов, 40 минут)" in embed.description

    async def test_command_top_10(self, env):
        for n in range(12):
            bind(env, 1000 + n, 2000 + n, n + 1)
        interaction = make_interaction(env.guild.add_member(1), env.guild)

        await env.cog.leaderboard_cubes.callback(env.cog, interaction)

        assert interaction.response.send_message.call_args.kwargs["embed"].description.count("<:cigar:") == 10

    async def test_command_empty(self, env):
        interaction = make_interaction(env.guild.add_member(1), env.guild)

        await env.cog.leaderboard_cubes.callback(env.cog, interaction)

        interaction.response.send_message.assert_awaited_once_with("Нет таблицы лидеров Кубов.", ephemeral=True)

    async def test_zero_minutes(self, env):
        env.db._ensure_karmic_bind(add_ozernik(env, 1), add_ozernik(env, 2))
        interaction = make_interaction(env.guild.add_member(5), env.guild)

        await env.cog.leaderboard_cubes.callback(env.cog, interaction)

        assert "Связь: `0` ед. с. (0 минут)" in interaction.response.send_message.call_args.kwargs["embed"].description


@pytest.mark.parametrize(
    ("number", "word"),
    [(1, "связь"), (2, "связи"), (5, "связей"), (11, "связей"), (21, "связь"), (104, "связи")],
)
def test_plural_ru(env, number, word):
    assert env.karma.plural_ru(number, ("связь", "связи", "связей")) == word
