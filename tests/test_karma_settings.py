"""Тесты меню /settings_karma: страницы, модальные окна, восстановление ролей."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from conftest import OWNER_ID, FakeRole, add_ozernik, create_all_roles, make_interaction


def open_menu(env, page_class, author=None):
    author = author or env.guild.add_member(1)
    navigator = env.karma.Navigator(page_class, author=author, bot=env.bot)
    navigator.message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    return navigator, author


def texts(view) -> list[str]:
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


def buttons(view) -> list[discord.ui.Button]:
    return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]


# ---------- ГЛАВНАЯ СТРАНИЦА -----------

class TestMainPage:
    async def test_build(self, env):
        navigator, _ = open_menu(env, env.karma.SettingsMainPage)

        view = navigator.current_page.build()

        assert texts(view)[0] == "# Карма"
        assert [b.label for b in buttons(view)] == ["Общие", "Сансара", "Кубы", "Текст повышений"]

    @pytest.mark.parametrize(
        ("label", "page"),
        [
            ("Общие", "SettingsGeneralPage"),
            ("Сансара", "SettingsSansaraPage"),
            ("Кубы", "SettingsCubesPage"),
            ("Текст повышений", "SettingsLevelUpPage"),
        ],
    )
    async def test_navigation_and_back(self, env, label, page):
        navigator, author = open_menu(env, env.karma.SettingsMainPage)
        view = navigator.current_page.build()
        button = next(b for b in buttons(view) if b.label == label)
        interaction = make_interaction(author, env.guild)

        await button.callback(interaction)

        assert type(navigator.current_page).__name__ == page
        navigator.message.edit.assert_awaited()

        sub_view = navigator.current_page.build()
        back = next(b for b in buttons(sub_view) if b.label == "Назад")
        await back.callback(interaction)

        assert isinstance(navigator.current_page, env.karma.SettingsMainPage)

    async def test_other_user_cannot_use_menu(self, env):
        navigator, _ = open_menu(env, env.karma.SettingsMainPage)
        stranger = env.guild.add_member(2)
        interaction = make_interaction(stranger, env.guild)

        allowed = await navigator.current_page._interaction_check(interaction)

        assert allowed is False
        interaction.response.send_message.assert_awaited_once_with(
            "Это меню принадлежит другому пользователю.", ephemeral=True
        )


# ---------- ОБЩИЕ -----------

class TestGeneralPage:
    async def test_initial_texts_and_disabled_confirm(self, env):
        navigator, _ = open_menu(env, env.karma.SettingsGeneralPage)
        page = navigator.current_page

        view = page.build()

        assert "### Заблокированные каналы: не выбраны" in texts(view)
        assert "### Заблокированные роли: не выбраны" in texts(view)
        assert "### Канал оповещений: <#0>" in texts(view)
        assert page.confirm_button.disabled is True

    async def test_change_channels_and_confirm(self, env):
        navigator, author = open_menu(env, env.karma.SettingsGeneralPage)
        page = navigator.current_page
        page.build()
        interaction = make_interaction(author, env.guild)

        page.karma_channel_select._values = [SimpleNamespace(id=11)]
        await page.karma_channel_callback(interaction)
        page.log_channel_select._values = [SimpleNamespace(id=12)]
        await page.log_channel_callback(interaction)

        assert page.get_karma_channel_text() == "### Канал оповещений: __<#11>__*"
        assert page.get_log_channel_text() == "### Канал логов: __<#12>__*"
        assert page.confirm_button.disabled is False
        assert env.data.karma_channel_id == 0  # до подтверждения ничего не сохранено

        await page.confirm_callback(interaction)

        assert env.data.karma_channel_id == 11
        assert env.data.log_channel_id == 12
        assert page.get_karma_channel_text() == "### Канал оповещений: <#11>"

    async def test_blocked_channels_toggle(self, env):
        env.data.blocked_channels_id = [1]
        navigator, author = open_menu(env, env.karma.SettingsGeneralPage)
        page = navigator.current_page
        interaction = make_interaction(author, env.guild)

        # Повторный выбор снимает блокировку, новый — добавляет.
        page.blocked_channels_select._values = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        await page.blocked_channels_callback(interaction)

        text = page.get_blocked_channels_text()
        assert "~~<#1>~~\\*" in text
        assert "__<#2>__\\*" in text

        page.build()
        await page.confirm_callback(interaction)

        assert env.data.blocked_channels_id == [2]

    async def test_blocked_roles_toggle(self, env):
        navigator, author = open_menu(env, env.karma.SettingsGeneralPage)
        page = navigator.current_page
        interaction = make_interaction(author, env.guild)

        page.blocked_roles_select._values = [SimpleNamespace(id=7)]
        await page.blocked_roles_callback(interaction)

        assert page.get_roles_channels_text() == "### Заблокированные роли: <@&7>\\*"

        await page.confirm_callback(interaction)

        assert env.data.blocked_roles_id == [7]
        assert page.get_roles_channels_text() == "### Заблокированные роли: <@&7>"


# ---------- ТЕКСТЫ ПОВЫШЕНИЙ -----------

class TestLevelUpPage:
    async def test_five_buttons_for_100_levels(self, env):
        navigator, _ = open_menu(env, env.karma.SettingsLevelUpPage)

        labels = [b.label for b in buttons(navigator.current_page.build()) if b.label.startswith("Изменить")]

        assert labels == [
            "Изменить (1 - 20)",
            "Изменить (21 - 40)",
            "Изменить (41 - 60)",
            "Изменить (61 - 80)",
            "Изменить (81 - 100)",
        ]

    async def test_modal_prefilled_with_group(self, env):
        env.levels["2"] = "два"
        navigator, author = open_menu(env, env.karma.SettingsLevelUpPage)
        interaction = make_interaction(author, env.guild)

        await navigator.current_page.buttons[0].callback(interaction)

        modal = interaction.response.send_modal.call_args.args[0]
        assert modal.text_input.default.startswith("'1': '',\n'2': 'два',\n'3': ''")

    async def test_modal_submit_updates_only_given_levels(self, env):
        env.levels["2"] = "старый"
        navigator, author = open_menu(env, env.karma.SettingsLevelUpPage)
        page = navigator.current_page
        modal = page.LevelUpTextModal((("1", ""), ("2", "старый")), page)
        modal.text_input._value = "'1': 'новый'"
        interaction = make_interaction(author, env.guild)

        await modal.on_submit(interaction)

        assert env.levels["1"] == "новый"
        assert env.levels["2"] == "старый"
        interaction.followup.send.assert_awaited_once_with("Изменено.", ephemeral=True)

    async def test_modal_submit_syntax_error(self, env):
        navigator, author = open_menu(env, env.karma.SettingsLevelUpPage)
        page = navigator.current_page
        modal = page.LevelUpTextModal((("1", ""),), page)
        modal.text_input._value = "'1': 'не закрыто,\n'2': 'ok'"
        interaction = make_interaction(author, env.guild)

        await modal.on_submit(interaction)

        message = interaction.followup.send.call_args.args[0]
        assert message.startswith("Строка не закрыта. Проверьте кавычки.\nСтрока: 1")
        assert env.levels["1"] == ""

    async def test_modal_submit_syntax_error_on_last_line(self, env):
        navigator, author = open_menu(env, env.karma.SettingsLevelUpPage)
        page = navigator.current_page
        modal = page.LevelUpTextModal((("1", ""),), page)
        modal.text_input._value = "'1': 'не закрыто"
        interaction = make_interaction(author, env.guild)

        await modal.on_submit(interaction)

        message = interaction.followup.send.call_args.args[0]
        assert message.startswith("Строка не закрыта. Проверьте кавычки.\nСтрока: 1")
        # Строка с ошибкой подчёркнута.
        assert "'1': 'не закрыто\n" + "^" * len("'1': 'не закрыто") in message


# ---------- САНСАРА -----------

class TestSansaraPage:
    async def test_build_texts(self, env):
        navigator, _ = open_menu(env, env.karma.SettingsSansaraPage)

        all_text = "\n".join(texts(navigator.current_page.build()))

        assert "## Задержка между выдачей Кармы: `1` секунда." in all_text
        assert "`Нарака`, `0` кармы (0 ур.)." in all_text
        assert "`Человек`, `5000` кармы (25 ур.)." in all_text
        assert "`Асур`, `5000+` кармы (25+ ур.)." in all_text

    def test_levels_table(self, env):
        assert env.karma.SettingsSansaraPage.get_karma_levels_text() == (
            "01 ур:  `100`   11 ур:  `1200`  21 ур:  `3400`\n"
            "02 ур: `200`   12 ур: `1400`  22 ур: `3800`\n"
            "03 ур: `300`   13 ур: `1600`  23 ур: `4200`\n"
            "04 ур: `400`   14 ур: `1800`  24 ур: `4600`\n"
            "05 ур: `500`   15 ур: `2000`  25 ур: `5000`\n"
            "06 ур: `600`   16 ур: `2200`  26 ур: `5400`\n"
            "07 ур:  `700`   17 ур: `2400`  27 ур: `5800`\n"
            "08 ур: `800`   18 ур: `2600`  28 ур: `6200`\n"
            "09 ур: `900`   19 ур: `2800`  29 ур: `6600`\n"
            "10 ур:  `1000` 20 ур: `3000` 30 ур: `7000`\n"
            "и т.д."
        )

    def test_delay_text_when_not_set(self, env):
        env.data.karma_message_delay = 0

        assert env.karma.SettingsSansaraPage.get_karma_message_delay_text() == "## Задержка между выдачей Кармы: Не задана"

    @pytest.mark.parametrize(
        ("value", "saved", "answer"),
        [
            ("5", 5, None),
            ("x", 1, "Введите целое число."),
            ("0", 1, "Введите число от 1 до 9."),
        ],
    )
    async def test_delay_modal(self, env, value, saved, answer):
        navigator, author = open_menu(env, env.karma.SettingsSansaraPage)
        modal = env.karma.SettingsSansaraPage.DelayModal(navigator)
        modal.text_input._value = value
        interaction = make_interaction(author, env.guild)

        await modal.on_submit(interaction)

        assert env.data.karma_message_delay == saved
        if answer:
            interaction.response.send_message.assert_awaited_once_with(answer, ephemeral=True)
        else:
            interaction.response.defer.assert_awaited_once()

    async def test_restore_roles_needs_confirmation(self, env):
        navigator, author = open_menu(env, env.karma.SettingsSansaraPage)
        page = navigator.current_page
        interaction = make_interaction(author, env.guild)
        page._restore_roles = AsyncMock()
        button = page.create_confirm_button("Восстановить роли", "Подтвердить", page._restore_roles)

        await button.callback(interaction)
        assert button.label == "Подтвердить"
        page._restore_roles.assert_not_awaited()

        await button.callback(interaction)
        assert button.label == "Восстановить роли"
        page._restore_roles.assert_awaited_once()

    async def test_restore_roles(self, env):
        create_all_roles(env)
        roles = env.data.karma_roles
        naraka = env.guild.get_role(roles["naraka"]["role_id"])
        human = env.guild.get_role(roles["human"]["role_id"])
        wrong = env.guild.add_member(1, roles=[naraka, human])
        env.db.set_karma(env.bot.db_ensure_user(wrong).id, 150)
        empty = env.guild.add_member(2)

        navigator, author = open_menu(env, env.karma.SettingsSansaraPage, author=wrong)
        interaction = make_interaction(author, env.guild)

        await navigator.current_page._restore_roles(interaction)
        await env.karma.SettingsSansaraPage.restore_task

        assert [role.id for role in wrong.roles] == [roles["preta"]["role_id"]]
        assert [role.id for role in empty.roles] == [roles["naraka"]["role_id"]]
        interaction.followup.send.assert_awaited_with("Обновление ролей участников завершено.", ephemeral=True)

    async def test_restore_roles_skips_bots_and_removes_their_roles(self, env):
        create_all_roles(env)
        roles = env.data.karma_roles
        naraka = env.guild.get_role(roles["naraka"]["role_id"])
        other = FakeRole(1)
        clean_bot = env.guild.add_member(10, bot=True)
        dirty_bot = env.guild.add_member(11, bot=True, roles=[naraka, other])
        admin = env.guild.add_member(1)

        navigator, author = open_menu(env, env.karma.SettingsSansaraPage, author=admin)

        await navigator.current_page._restore_roles(make_interaction(author, env.guild))
        await env.karma.SettingsSansaraPage.restore_task

        assert clean_bot.roles == []
        assert dirty_bot.roles == [other]  # чужие роли бота не трогаются
        assert env.db.get_user_by_discord_id(10) is None  # ботов в базу кармы не заносим

    async def test_restore_roles_already_running(self, env):
        navigator, author = open_menu(env, env.karma.SettingsSansaraPage)
        interaction = make_interaction(author, env.guild)
        running = asyncio.get_running_loop().create_future()
        env.karma.SettingsSansaraPage.restore_task = running

        await navigator.current_page._restore_roles(interaction)

        interaction.followup.send.assert_awaited_once_with("Задача уже выполняется", ephemeral=True)
        running.cancel()


# ---------- КУБЫ -----------

class TestCubesPage:
    async def test_build_texts(self, env):
        navigator, _ = open_menu(env, env.karma.SettingsCubesPage)

        all_text = "\n".join(texts(navigator.current_page.build()))

        assert "`Черный Куб`, `0` ед. с. (0 минут) для 1 связи." in all_text
        assert "`Белый Куб`, `360` ед. с. (6 часов) для 1 связи." in all_text
        assert "- Золотая — 5760 ед. с. (4 дня)" in all_text
        assert "## Количество дней для Золотой Связи: `4` дня." in all_text

    async def test_gold_modal_default(self, env):
        navigator, _ = open_menu(env, env.karma.SettingsCubesPage)

        modal = env.karma.SettingsCubesPage.GoldCubeModal(navigator)

        assert modal.text_input.default == "4"

    @pytest.mark.parametrize(
        ("value", "saved", "answer"),
        [
            ("5", 5 * 24 * 60, None),
            ("x", 5760, "Введите целое число."),
            ("9", 5760, "Введите число от 3 до 7."),
        ],
    )
    async def test_gold_modal_for_owner(self, env, value, saved, answer):
        owner = env.guild.add_member(OWNER_ID)
        navigator, _ = open_menu(env, env.karma.SettingsCubesPage, author=owner)
        modal = env.karma.SettingsCubesPage.GoldCubeModal(navigator)
        modal.text_input._value = value
        interaction = make_interaction(owner, env.guild)

        await modal.on_submit(interaction)

        assert env.data.cube_roles["gold_cube"]["required_karma"] == saved
        if answer:
            interaction.response.send_message.assert_awaited_once_with(answer, ephemeral=True)
        else:
            interaction.response.defer.assert_awaited_once()

    async def test_gold_modal_rejects_non_owner(self, env):
        navigator, author = open_menu(env, env.karma.SettingsCubesPage)
        modal = env.karma.SettingsCubesPage.GoldCubeModal(navigator)
        modal.text_input._value = "5"
        interaction = make_interaction(author, env.guild)

        await modal.on_submit(interaction)

        assert env.data.cube_roles["gold_cube"]["required_karma"] == 5760
        message = interaction.response.send_message.call_args.args[0]
        assert message.startswith("Доступно только <@512079329619083291>.")

    async def test_restore_roles(self, env):
        create_all_roles(env)
        cubes = env.data.cube_roles
        gold = env.guild.get_role(cubes["gold_cube"]["role_id"])
        member = env.guild.add_member(1, roles=[gold])
        env.db.add_bind_karma(env.bot.db_ensure_user(member).id, add_ozernik(env, 99), 10)
        lonely = env.guild.add_member(2)

        navigator, author = open_menu(env, env.karma.SettingsCubesPage, author=member)
        interaction = make_interaction(author, env.guild)

        await navigator.current_page._restore_roles(interaction)
        await env.karma.SettingsCubesPage.restore_task

        assert [role.id for role in member.roles] == [cubes["black_cube"]["role_id"]]
        assert lonely.roles == []  # без связей куб не выдаётся

    async def test_restore_roles_skips_bots_and_removes_their_roles(self, env):
        create_all_roles(env)
        cubes = env.data.cube_roles
        black = env.guild.get_role(cubes["black_cube"]["role_id"])
        bot_member = env.guild.add_member(10, bot=True, roles=[black])
        # Даже если у бота откуда-то есть связи — куб ему не положен.
        env.db.add_bind_karma(env.bot.db_ensure_user(bot_member).id, add_ozernik(env, 99), 400)
        admin = env.guild.add_member(1)

        navigator, author = open_menu(env, env.karma.SettingsCubesPage, author=admin)

        await navigator.current_page._restore_roles(make_interaction(author, env.guild))
        await env.karma.SettingsCubesPage.restore_task

        assert bot_member.roles == []

    async def test_cubes_and_sansara_restore_are_independent(self, env):
        navigator, author = open_menu(env, env.karma.SettingsCubesPage)
        running = asyncio.get_running_loop().create_future()
        env.karma.SettingsSansaraPage.restore_task = running

        await navigator.current_page._restore_roles(make_interaction(author, env.guild))

        assert env.karma.SettingsCubesPage.restore_task is not None
        assert env.karma.SettingsSansaraPage.restore_task is running
        await env.karma.SettingsCubesPage.restore_task
        running.cancel()
