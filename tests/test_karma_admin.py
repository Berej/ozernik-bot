"""Тесты админских команд кармы (/karma_add, /karma_remove, /karma_set, /karma_status)."""

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

        # Недельная карма не меняется: недельный топ — только за собственную активность.
        assert (karma(env, target).karma, karma(env, target).weekly_karma) == (150, 0)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        assert answer(interaction) == (
            "**Карма добавлена:** <@2>\n"
            "Карма: `0` → `150` (уровень 0 → 1)"
        )

    async def test_add_congratulates_once(self, env):
        channel = env.karma_channel
        env.data.karma_channel_id = channel.id
        create_all_roles(env)
        target = env.guild.add_member(2)

        await env.cog.karma_add.callback(env.cog, make_interaction(admin(env), env.guild), target, 1000)

        # 0 → 10 уровень: одно сообщение о последнем уровне, без промежуточных.
        channel.send.assert_awaited_once()
        assert channel.send.call_args.kwargs["embed"].title == f"**{target.display_name} повышает уровень!**"
        assert channel.send.call_args.kwargs["embed"].description.startswith("**Вы достигли 10 уровня.**")

    async def test_set_up_congratulates_once(self, env):
        channel = env.karma_channel
        env.data.karma_channel_id = channel.id
        create_all_roles(env)
        target = env.guild.add_member(2)

        await env.cog.karma_set.callback(env.cog, make_interaction(admin(env), env.guild), target, 4999)

        channel.send.assert_awaited_once()
        assert channel.send.call_args.kwargs["embed"].title == f"**{target.display_name} повышает уровень!**"
        assert channel.send.call_args.kwargs["embed"].description.startswith("**Вы достигли 24 уровня.**")

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

        assert (karma(env, target).karma, karma(env, target).weekly_karma) == (0, 30)
        assert answer(interaction).startswith("**Карма отнята:** <@2>\nКарма: `30` → `0`")

    async def test_set(self, env):
        target = env.guild.add_member(2)
        user_id = env.bot.db_ensure_user(target).id
        env.db.add_karma(user_id, 100)
        env.db.add_karma(user_id, 20, weekly=True)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_set.callback(env.cog, interaction, target, 150)

        assert (karma(env, target).karma, karma(env, target).weekly_karma) == (150, 20)
        assert answer(interaction).startswith("**Карма установлена:**")
        assert "Недельная" not in answer(interaction)

    async def test_reset_bot(self, env):
        """Главный сценарий: обнулить бота, который успел набрать карму."""
        create_all_roles(env)
        bot_member = env.guild.add_member(2, bot=True)
        user_id = env.bot.db_ensure_user(bot_member).id
        env.db.add_karma(user_id, 500, weekly=True)
        interaction = make_interaction(admin(env), env.guild)

        await env.cog.karma_set.callback(env.cog, interaction, bot_member, 0)

        # Недельная карма не меняется даже при обнулении (решение Alium 2026-10-03).
        assert (karma(env, bot_member).karma, karma(env, bot_member).weekly_karma) == (0, 500)
        assert answer(interaction).startswith("**Карма обнулена:** <@2>")
        assert "Недельная" not in answer(interaction)
        bot_member.add_roles.assert_not_awaited()  # ботам роли Сансары не выдаются

    async def test_case_remove_all_then_add_keeps_weekly(self, env):
        """Случай Alium: отнял себе всю карму, потом выдал обратно — недельный топ не должен меняться."""
        target = env.guild.add_member(2)
        user_id = env.bot.db_ensure_user(target).id
        env.db.add_karma(user_id, 300)
        env.db.add_karma(user_id, 40, weekly=True)  # 340 / неделя 40

        await env.cog.karma_remove.callback(env.cog, make_interaction(admin(env), env.guild), target, 340)
        await env.cog.karma_add.callback(env.cog, make_interaction(admin(env), env.guild), target, 340)

        assert (karma(env, target).karma, karma(env, target).weekly_karma) == (340, 40)

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


@pytest.mark.parametrize(
    ("number", "word"),
    [(1, "связь"), (2, "связи"), (5, "связей"), (11, "связей"), (21, "связь"), (104, "связи")],
)
def test_plural_ru(env, number, word):
    assert env.karma.plural_ru(number, ("связь", "связи", "связей")) == word
