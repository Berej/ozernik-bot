"""
Твинки: дополнительные Discord-аккаунты человека (сколько угодно), карма с них считается основному.

Решения Alium (2026-10-23): человек сам привязывает твинк, твинк подтверждает в ЛС;
набранное твинком до привязки прибавляется к основному; роли у твинка — как у основного;
отвязать может владелец или админ.
"""

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from conftest import FakeChannel, add_ozernik, create_all_roles, make_interaction, make_message


# ---------- БАЗА -----------

class TestTwinkStorage:
    def test_many_twinks_resolve_to_main(self, env):
        main = add_ozernik(env, 1)
        env.db.link_twink(main, 11)
        env.db.link_twink(main, 12)

        assert env.db.get_twinks(main) == [11, 12]
        assert env.db.get_discord_ids(main) == [1, 11, 12]
        assert env.db.get_user_by_discord_id(11).id == main
        assert env.db.get_user_by_discord_id(12).id == main
        assert env.db.is_twink(11) and not env.db.is_twink(1)

    def test_legacy_single_twink_column_migrated(self, env):
        main = add_ozernik(env, 1)
        env.db.execute("DELETE FROM user_twinks")
        env.db.execute("UPDATE users SET discord_twink_id = 99 WHERE id = ?", (main,))

        env.db._init_db()

        assert env.db.get_twinks(main) == [99]
        assert env.db.get_user(main).discord_twink_id is None

    def test_add_user_with_twink_and_conflicts(self, env):
        main = env.db.add_user(discord_id=1, discord_twink_id=11)

        assert env.db.get_twinks(main) == [11]
        with pytest.raises(sqlite3.IntegrityError):
            env.db.add_user(discord_id=11)  # уже чей-то твинк

    def test_delete_user_removes_twinks(self, env):
        main = add_ozernik(env, 1)
        env.db.link_twink(main, 11)

        env.db.delete_user(main)

        assert not env.db.is_twink(11)


class TestLinkMerge:
    def test_link_fresh_account(self, env):
        main = add_ozernik(env, 1)

        moved = env.db.link_twink(main, 11)

        assert moved == {"karma": 0, "weekly_karma": 0, "binds": 0}

    def test_twink_karma_and_binds_added_to_main(self, env):
        main = add_ozernik(env, 1)
        twink = add_ozernik(env, 11)
        friend = add_ozernik(env, 2)
        other = add_ozernik(env, 3)
        env.db.add_karma(main, 100, weekly=True)
        env.db.add_karma(twink, 40, weekly=True)
        env.db.add_karma(twink, 10)
        env.db.add_bind_karma(main, friend, 30)
        env.db.add_bind_karma(twink, friend, 20)   # общий друг — складываем
        env.db.add_bind_karma(twink, other, 5)     # новый для основного
        env.db.add_bind_karma(twink, main, 99)     # «связь с самим собой» — пропадает

        moved = env.db.link_twink(main, 11)

        karma = env.db.get_karma(main)
        assert (karma.karma, karma.weekly_karma) == (150, 140)
        assert env.db.get_karmic_bind(main, friend).bind_karma == 50
        assert env.db.get_karmic_bind(main, other).bind_karma == 5
        assert moved == {"karma": 50, "weekly_karma": 40, "binds": 2}
        assert env.db.get_user(twink) is None  # запись твинка удалена
        assert env.db.get_user_by_discord_id(11).id == main
        assert len(env.db.get_top_karmic_binds()) == 2

    @pytest.mark.parametrize(
        ("setup", "error"),
        [
            ("self", "к самому себе"),
            ("already", "уже привязан"),
            ("has_twinks", "свои твинки"),
        ],
    )
    def test_link_rejected(self, env, setup, error):
        main = add_ozernik(env, 1)
        target = 11
        if setup == "self":
            target = 1
        elif setup == "already":
            env.db.link_twink(add_ozernik(env, 5), 11)
        elif setup == "has_twinks":
            env.db.link_twink(add_ozernik(env, 11), 111)

        with pytest.raises(ValueError, match=error):
            env.db.link_twink(main, target)

    def test_link_is_atomic(self, env, monkeypatch):
        """Если перенос упал на середине — не меняется ничего."""
        main = add_ozernik(env, 1)
        twink = add_ozernik(env, 11)
        env.db.add_karma(twink, 40)
        env.db.add_bind_karma(twink, add_ozernik(env, 2), 20)

        real_execute = env.db.execute

        def broken(query, params=()):
            if "DELETE FROM users" in query:
                raise sqlite3.OperationalError("диск отвалился")
            return real_execute(query, params)

        monkeypatch.setattr(env.db, "execute", broken)
        with pytest.raises(sqlite3.OperationalError):
            env.db.link_twink(main, 11)
        monkeypatch.undo()

        assert env.db.get_karma(main).karma == 0
        assert env.db.get_karma(twink).karma == 40
        assert not env.db.is_twink(11)

    def test_unlink_keeps_karma_with_main(self, env):
        main = add_ozernik(env, 1)
        env.db.link_twink(main, 11)
        env.db.add_karma(env.db.get_user_by_discord_id(11).id, 30)  # набрано с твинка

        assert env.db.unlink_twink(11) == main
        assert env.db.unlink_twink(11) is None
        assert env.db.get_karma(main).karma == 30
        assert env.db.get_user_by_discord_id(11) is None



# ---------- КОМАНДЫ И ПОДТВЕРЖДЕНИЕ В ЛС -----------

@pytest.fixture
def accounts(env):
    """Основной аккаунт, его будущий твинк и посторонний — все на сервере; ЛС у всех открыты."""
    main = env.guild.add_member(1)
    twink = env.guild.add_member(11)
    stranger = env.guild.add_member(2)
    for member in (main, twink, stranger):
        member.send = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
    env.bot.get_user = lambda discord_id: env.guild.get_member(discord_id)
    return SimpleNamespace(main=main, twink=twink, stranger=stranger)


async def request(env, main, twink):
    interaction = make_interaction(main, env.guild)
    await env.cog.twink_add.callback(env.cog, interaction, twink)
    return interaction


def sent_view(member):
    return member.send.call_args.kwargs["view"]


async def press(view, label, user):
    button = next(item for item in view.children if item.label == label)
    interaction = SimpleNamespace(
        user=user,
        response=SimpleNamespace(edit_message=AsyncMock()),
    )
    await button.callback(interaction)
    return interaction.response.edit_message.call_args.kwargs


class TestTwinkCommands:
    async def test_request_sent_to_twink_dm(self, env, accounts):
        interaction = await request(env, accounts.main, accounts.twink)

        text = accounts.twink.send.call_args.args[0]
        assert "хочет привязать этот аккаунт" in text
        assert isinstance(sent_view(accounts.twink), env.karma.TwinkConfirmView)
        assert "Запрос отправлен в ЛС" in interaction.response.send_message.call_args.args[0]
        assert not env.db.is_twink(11)  # до подтверждения ничего не привязано

    async def test_closed_dm(self, env, accounts):
        accounts.twink.send.side_effect = discord.Forbidden(AsyncMock(status=403), "ЛС закрыты")

        interaction = await request(env, accounts.main, accounts.twink)

        assert "Не получилось написать" in interaction.response.send_message.call_args.args[0]

    @pytest.mark.parametrize(
        ("case", "reason"),
        [
            ("bot", "Ботов привязывать нельзя"),
            ("self", "Это и есть ваш аккаунт"),
            ("caller_is_twink", "сам привязан как твинк"),
            ("already_twink", "уже привязан как твинк"),
        ],
    )
    async def test_request_rejected(self, env, accounts, case, reason):
        main, target = accounts.main, accounts.twink
        if case == "bot":
            target = env.guild.add_member(50, bot=True)
        elif case == "self":
            target = main
        elif case == "caller_is_twink":
            env.db.link_twink(add_ozernik(env, 77), main.id)
        elif case == "already_twink":
            env.db.link_twink(add_ozernik(env, 77), target.id)

        interaction = await request(env, main, target)

        assert reason in interaction.response.send_message.call_args.args[0]

    async def test_only_twink_can_press(self, env, accounts):
        await request(env, accounts.main, accounts.twink)
        view = sent_view(accounts.twink)

        assert await view.interaction_check(SimpleNamespace(user=accounts.stranger)) is False
        assert await view.interaction_check(SimpleNamespace(user=accounts.twink)) is True

    async def test_confirm_links_merges_and_syncs_roles(self, env, accounts):
        create_all_roles(env)
        log = FakeChannel(7000)
        env.bot.channels[log.id] = log
        env.data.log_channel_id = log.id
        main_id = env.bot.db_ensure_user(accounts.main).id
        env.db.set_karma(main_id, 900)
        env.db.add_karma(env.bot.db_ensure_user(accounts.twink).id, 200)  # 900 + 200 = Зверь
        await request(env, accounts.main, accounts.twink)

        result = await press(sent_view(accounts.twink), "Подтвердить", accounts.twink)

        assert result["content"].startswith("Готово: этот аккаунт — твинк <@1>.")
        assert "карма `200`" in result["content"]
        assert all(button.disabled for button in result["view"].children)
        assert env.db.get_karma(main_id).karma == 1100
        animal = env.data.karma_roles["animal"]["role_id"]
        assert [role.id for role in accounts.main.roles] == [animal]
        assert [role.id for role in accounts.twink.roles] == [animal]   # роли как у основного
        assert "подтвердил привязку" in accounts.main.send.call_args.args[0]
        assert log.send.call_args.args[0].startswith("**Твинк привязан:** <@11> → <@1>")

    async def test_decline(self, env, accounts):
        await request(env, accounts.main, accounts.twink)

        result = await press(sent_view(accounts.twink), "Отклонить", accounts.twink)

        assert result["content"] == "Привязка отклонена."
        assert not env.db.is_twink(11)
        assert "отклонил привязку" in accounts.main.send.call_args.args[0]

    async def test_confirm_rechecks_state(self, env, accounts):
        """Пока запрос ждал, твинк успели привязать к другому — второй раз не привяжется."""
        await request(env, accounts.main, accounts.twink)
        env.db.link_twink(add_ozernik(env, 77), 11)

        result = await press(sent_view(accounts.twink), "Подтвердить", accounts.twink)

        assert result["content"].startswith("Не привязано:")

    async def test_karma_from_twink_goes_to_main(self, env, accounts):
        main_id = env.bot.db_ensure_user(accounts.main).id
        env.db.link_twink(main_id, 11)

        await env.cog.on_message(make_message(accounts.twink, FakeChannel(3000), env.guild))

        assert env.db.get_karma(main_id).karma == 1

    async def test_level_up_from_twink_updates_both_accounts(self, env, accounts):
        env.data.karma_channel_id = env.karma_channel.id
        create_all_roles(env)
        main_id = env.bot.db_ensure_user(accounts.main).id
        env.db.link_twink(main_id, 11)
        env.db.set_karma(main_id, 99)

        await env.cog.on_message(make_message(accounts.twink, FakeChannel(3000), env.guild))

        preta = env.data.karma_roles["preta"]["role_id"]
        assert preta in [role.id for role in accounts.main.roles]
        assert preta in [role.id for role in accounts.twink.roles]


class TestTwinkRemoveAndList:
    async def link(self, env, accounts):
        main_id = env.bot.db_ensure_user(accounts.main).id
        env.db.link_twink(main_id, 11)
        return main_id

    def admin(self, env, accounts):
        accounts.stranger.guild_permissions.administrator = True
        return make_interaction(accounts.stranger, env.guild)

    async def test_admin_unlinks_and_twink_starts_from_zero(self, env, accounts):
        create_all_roles(env)
        main_id = await self.link(env, accounts)
        env.db.set_karma(main_id, 1000)
        await env.cog.update_sansara_roles(accounts.main)  # у обоих Зверь
        interaction = self.admin(env, accounts)

        await env.cog.twink_remove.callback(env.cog, interaction, accounts.twink)

        assert not env.db.is_twink(11)
        assert env.db.get_karma(main_id).karma == 1000
        assert "отвязан от <@1>" in interaction.response.send_message.call_args.args[0]
        naraka = env.data.karma_roles["naraka"]["role_id"]
        assert [role.id for role in accounts.twink.roles] == [naraka]  # своя, нулевая карма
        assert "Администрация отвязала <@11>" in accounts.main.send.call_args.args[0]

    @pytest.mark.parametrize("caller", ["main", "twink"])
    async def test_owner_cannot_unlink(self, env, accounts, caller):
        """Только администрация: иначе твинк можно отвязать на время наказания (решение Alium)."""
        await self.link(env, accounts)
        interaction = make_interaction(getattr(accounts, caller), env.guild)

        await env.cog.twink_remove.callback(env.cog, interaction, accounts.twink)

        assert env.db.is_twink(11)
        assert interaction.response.send_message.call_args.args[0] == "Отвязать твинк может только администрация — обратитесь к ней."

    async def test_unlink_not_a_twink(self, env, accounts):
        interaction = self.admin(env, accounts)

        await env.cog.twink_remove.callback(env.cog, interaction, accounts.twink)

        assert interaction.response.send_message.call_args.args[0] == "<@11> не привязан как твинк."

    async def test_list(self, env, accounts):
        await self.link(env, accounts)
        interaction = make_interaction(accounts.stranger, env.guild)

        await env.cog.twinks.callback(env.cog, interaction, accounts.twink)  # по твинку — тоже находит

        assert interaction.response.send_message.call_args.args[0] == "Основной аккаунт: <@1>\nТвинки:\n- <@11>"

    async def test_list_empty(self, env, accounts):
        interaction = make_interaction(accounts.main, env.guild)

        await env.cog.twinks.callback(env.cog, interaction)

        assert interaction.response.send_message.call_args.args[0] == "У <@1> нет твинков."


class TestAllTwinks:
    """/twinks_all — таблица всех твинков сервера для администрации."""

    def test_db_groups_by_main(self, env):
        first, second = add_ozernik(env, 1), add_ozernik(env, 2)
        env.db.link_twink(second, 21)
        env.db.link_twink(first, 11)
        env.db.link_twink(first, 12)

        assert env.db.get_all_twinks() == [(1, [11, 12]), (2, [21])]

    def test_db_empty(self, env):
        assert env.db.get_all_twinks() == []

    async def test_only_admin(self, env, accounts):
        interaction = make_interaction(accounts.stranger, env.guild)

        await env.cog.twinks_all.callback(env.cog, interaction)

        assert interaction.response.send_message.call_args.args[0] == "Только для Администраторов."

    async def test_empty(self, env, accounts):
        accounts.stranger.guild_permissions.administrator = True
        interaction = make_interaction(accounts.stranger, env.guild)

        await env.cog.twinks_all.callback(env.cog, interaction)

        assert interaction.response.send_message.call_args.args[0] == "Твинков на сервере нет."

    async def test_table(self, env, accounts):
        accounts.stranger.guild_permissions.administrator = True
        env.db.link_twink(env.bot.db_ensure_user(accounts.main).id, 11)
        interaction = make_interaction(accounts.stranger, env.guild)

        await env.cog.twinks_all.callback(env.cog, interaction)

        kwargs = interaction.response.send_message.call_args.kwargs
        assert kwargs["ephemeral"] is True
        assert kwargs["embed"].description == "<@1> → <@11>"
        assert kwargs["embed"].footer.text == "Страница 1/1 · владельцев: 1 · твинков: 1"
        assert all(button.disabled for button in kwargs["view"].children)  # листать некуда

    async def test_pages(self, env):
        per_page = env.karma.TWINK_OWNERS_PER_PAGE
        groups = [(main_id, [main_id * 100]) for main_id in range(1, per_page + 3)]
        view = env.karma.AllTwinksView(author_id=5, groups=groups)
        previous, next_ = view.children

        assert view.pages == 2
        assert previous.disabled and not next_.disabled
        assert len(view.embed().description.splitlines()) == per_page

        result = await press(view, "▶", SimpleNamespace(id=5))

        assert result["embed"].description.splitlines() == [f"<@{per_page + 1}> → <@{(per_page + 1) * 100}>", f"<@{per_page + 2}> → <@{(per_page + 2) * 100}>"]
        assert result["embed"].footer.text.startswith("Страница 2/2")
        assert not previous.disabled and next_.disabled

    async def test_only_author_can_turn_pages(self, env):
        view = env.karma.AllTwinksView(author_id=5, groups=[(1, [11])])

        assert await view.interaction_check(SimpleNamespace(user=SimpleNamespace(id=6))) is False
        assert await view.interaction_check(SimpleNamespace(user=SimpleNamespace(id=5))) is True


class TestTwinkLink:
    """/twink_link — администрация связывает аккаунты сама, без подтверждения."""

    def admin(self, env):
        admin = env.guild.add_member(900)
        admin.guild_permissions.administrator = True
        admin.send = AsyncMock()
        return make_interaction(admin, env.guild)

    async def test_links_merges_and_notifies(self, env, accounts):
        create_all_roles(env)
        log = FakeChannel(7000)
        env.bot.channels[log.id] = log
        env.data.log_channel_id = log.id
        main_id = env.bot.db_ensure_user(accounts.main).id
        env.db.set_karma(main_id, 900)
        env.db.add_karma(env.bot.db_ensure_user(accounts.twink).id, 200)
        interaction = self.admin(env)

        await env.cog.twink_link.callback(env.cog, interaction, accounts.main, accounts.twink)

        assert env.db.is_twink(11)
        assert env.db.get_karma(main_id).karma == 1100
        assert interaction.response.send_message.call_args.args[0].startswith("<@11> привязан как твинк <@1>.")
        animal = env.data.karma_roles["animal"]["role_id"]
        assert [role.id for role in accounts.twink.roles] == [animal]
        assert "Администрация связала аккаунты" in accounts.main.send.call_args.args[0]
        assert "Администрация связала аккаунты" in accounts.twink.send.call_args.args[0]
        assert log.send.call_args.args[0].startswith("<@900> → **Твинк привязан администрацией:**")

    async def test_account_not_on_server(self, env, accounts):
        """Твинк ушёл с сервера — привязать всё равно можно (по ID)."""
        gone = SimpleNamespace(id=55, bot=False, mention="<@55>")

        await env.cog.twink_link.callback(env.cog, self.admin(env), accounts.main, gone)

        assert env.db.is_twink(55)

    async def test_only_admin(self, env, accounts):
        interaction = make_interaction(accounts.stranger, env.guild)

        await env.cog.twink_link.callback(env.cog, interaction, accounts.stranger, accounts.twink)

        assert not env.db.is_twink(11)
        assert interaction.response.send_message.call_args.args[0] == "Только для Администраторов."

    @pytest.mark.parametrize(
        ("case", "reason"),
        [
            ("bot_main", "бот не может быть основным"),
            ("bot_twink", "Ботов привязывать нельзя"),
            ("same", "один и тот же аккаунт"),
            ("main_is_twink", "сам твинк <@77>"),
            ("already_twink", "уже привязан как твинк"),
        ],
    )
    async def test_rejected(self, env, accounts, case, reason):
        main, target = accounts.main, accounts.twink
        if case == "bot_main":
            main = env.guild.add_member(50, bot=True)
        elif case == "bot_twink":
            target = env.guild.add_member(50, bot=True)
        elif case == "same":
            target = main
        elif case == "main_is_twink":
            env.db.link_twink(add_ozernik(env, 77), main.id)
        elif case == "already_twink":
            env.db.link_twink(add_ozernik(env, 77), target.id)
        interaction = self.admin(env)

        await env.cog.twink_link.callback(env.cog, interaction, main, target)

        text = interaction.response.send_message.call_args.args[0]
        assert text.startswith("Не привязано:")
        assert reason in text


# ---------- ОБЪЯВЛЕНИЕ О НОВОМ ОБЛИКЕ -----------

class TestGuiseAnnouncement:
    @pytest.fixture
    def channel(self, env):
        env.data.karma_channel_id = env.karma_channel.id
        return env.karma_channel

    async def test_on_confirm(self, env, accounts, channel):
        await request(env, accounts.main, accounts.twink)

        await press(sent_view(accounts.twink), "Подтвердить", accounts.twink)

        assert channel.send.call_args.args[0] == "<@1> приобретает себе новый облик в лице <@11>"
        assert channel.send.call_args.kwargs["file"].filename == "user1_user11_guise.png"

    async def test_on_admin_link(self, env, accounts, channel):
        admin = env.guild.add_member(900)
        admin.guild_permissions.administrator = True

        await env.cog.twink_link.callback(env.cog, make_interaction(admin, env.guild), accounts.main, accounts.twink)

        assert channel.send.call_args.args[0] == "<@1> приобретает себе новый облик в лице <@11>"

    async def test_request_warns_about_announcement(self, env, accounts):
        await request(env, accounts.main, accounts.twink)

        assert "появится объявление о новом облике <@1>" in accounts.twink.send.call_args.args[0]

    async def test_announced_without_postcard_if_avatar_fails(self, env, accounts, channel):
        accounts.twink.display_avatar.read = AsyncMock(side_effect=discord.HTTPException(AsyncMock(status=500), "cdn"))
        await request(env, accounts.main, accounts.twink)

        await press(sent_view(accounts.twink), "Подтвердить", accounts.twink)

        assert channel.send.call_args.args[0] == "<@1> приобретает себе новый облик в лице <@11>"
        assert "file" not in channel.send.call_args.kwargs

    async def test_declined_not_announced(self, env, accounts, channel):
        await request(env, accounts.main, accounts.twink)

        await press(sent_view(accounts.twink), "Отклонить", accounts.twink)

        channel.send.assert_not_awaited()

    @pytest.mark.parametrize("stage_tag", ["naraka", "human", "asur", "deva"])
    def test_postcard(self, env, stage_tag):
        from io import BytesIO
        from PIL import Image

        avatar = Image.new("RGBA", (220, 220), (90, 140, 200, 255))
        buffer = env.karma.create_guise_postcard(avatar, avatar, stage_tag)

        assert Image.open(BytesIO(buffer.getvalue())).size == (850, 300)
