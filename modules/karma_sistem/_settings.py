"""
Меню /settings_karma: страницы «Общие», «Сансара», «Кубы», «Текст повышений».
"""

from __future__ import annotations

import asyncio # noqa
import inspect
import random
import traceback # noqa
import typing # noqa
import ast
import json
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from copy import deepcopy
from io import BytesIO
from itertools import combinations
from pathlib import Path # noqa
from typing import Any # noqa
from pprint import pformat # noqa

import discord # noqa
from discord import Interaction, app_commands, Role
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont, ImageOps # noqa
from PIL.ImageFont import FreeTypeFont
from discord.ui import Item, Button, View, LayoutView # noqa

from config import config
from utilities import *

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bot import OzernikiBot

from . import _state
from ._state import *
from ._formulas import *
from ._roles import *


# ---------------------------------------------------------
# ||||||||| Классы страниц у команды настроек |||||||||||||
# ---------------------------------------------------------

class SettingsMainPage(Page):
    """
    Главная страница настроек. Хаб для навигации.
    """
    title = 'Карма'

    def __init__(self, navigator: Navigator, author: discord.Member, bot: OzernikiBot):
        super().__init__(navigator, author)
        self.bot = bot

        self.general_button = discord.ui.Button(
            label="Общие",
            style=discord.ButtonStyle.primary,
        )

        async def general_callback(interaction):
            await self.navigator.push(SettingsGeneralPage, author=self.author, bot=self.bot)
            await interaction.response.defer()

        self.general_button.callback = (
            general_callback
        )

        self.sansara_button = discord.ui.Button(
            label="Сансара",
            style=discord.ButtonStyle.primary,
        )

        async def sansara_callback(interaction):
            await self.navigator.push(SettingsSansaraPage, author=self.author, bot=self.bot)
            await interaction.response.defer()

        self.sansara_button.callback = (
            sansara_callback
        )

        self.cubes_button = discord.ui.Button(
            label="Кубы",
            style=discord.ButtonStyle.primary,
        )

        async def cubes_callback(interaction):
            await self.navigator.push(SettingsCubesPage, author=self.author, bot=self.bot)
            await interaction.response.defer()

        self.cubes_button.callback = (
            cubes_callback
        )

        self.level_up_button = discord.ui.Button(
            label="Текст повышений",
            style=discord.ButtonStyle.primary,
        )

        async def level_up_callback(interaction): # noqa
            await self.navigator.push(SettingsLevelUpPage, author=self.author, bot=self.bot)
            await interaction.response.defer()

        self.level_up_button.callback = (
            level_up_callback
        )

    def build_content(self, container: discord.Container):
        container.add_item(
            discord.ui.TextDisplay(
                '*На создание этого меню у меня ушло неприлично много времени, так что наслаждайтесь этим текстом в назидание о правильном времяпрепровождении, а не этим всем.*\n'
                '*Если возникнут какие-то вопросы, обращайтесь к Габу (если я еще не умер).*'
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.general_button
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.sansara_button
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.cubes_button
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.level_up_button
            )
        )

class SettingsGeneralPage(Page):
    """
    Страница общих настроек.
    """
    title = 'Карма -> Общие'

    def __init__(self, navigator: Navigator, author: discord.Member, bot: OzernikiBot):
        super().__init__(navigator, author)
        self.bot = bot

        # Изменяемые значения
        self.karma_channel_id = _state.data.karma_channel_id
        self.log_channel_id = _state.data.log_channel_id
        self.blocked_channels_id = set(_state.data.blocked_channels_id)
        self.blocked_roles_id = set(_state.data.blocked_roles_id)
        self.blocked_users_id = set(_state.data.blocked_users_id)

        # Флаги изменений
        self.original_karma_channel_id = _state.data.karma_channel_id
        self.original_log_channel_id = _state.data.log_channel_id
        self.original_blocked_channels_id = set(_state.data.blocked_channels_id)
        self.original_blocked_roles_id = set(_state.data.blocked_roles_id)
        self.original_blocked_users_id = set(_state.data.blocked_users_id)

        # -------------------------------------------------
        # Блокировка каналов
        # -------------------------------------------------

        self.blocked_channels_select = discord.ui.ChannelSelect(
            placeholder="Изменить каналы"
        )
        self.blocked_channels_select.callback = (
            self.blocked_channels_callback
        )

        # -------------------------------------------------
        # Блокировка ролей
        # -------------------------------------------------

        self.blocked_roles_select = discord.ui.RoleSelect(
            placeholder="Изменить роли"
        )
        self.blocked_roles_select.callback = (
            self.blocked_roles_callback
        )

        # -------------------------------------------------
        # Блокировка участников
        # -------------------------------------------------

        self.blocked_users_select = discord.ui.UserSelect(
            placeholder="Изменить участников"
        )
        self.blocked_users_select.callback = (
            self.blocked_users_callback
        )

        # -------------------------------------------------
        # Канал оповещений
        # -------------------------------------------------

        self.karma_channel_select = discord.ui.ChannelSelect(
            placeholder="Изменить канал",
            channel_types=[
                discord.ChannelType.text,
            ],
        )
        self.karma_channel_select.callback = (
            self.karma_channel_callback
        )

        # -------------------------------------------------
        # Канал логов
        # -------------------------------------------------

        self.log_channel_select = discord.ui.ChannelSelect(
            placeholder="Изменить канал",
            channel_types=[
                discord.ChannelType.text,
            ],
        )
        self.log_channel_select.callback = (
            self.log_channel_callback
        )

        # -------------------------------------------------
        # Кнопки
        # -------------------------------------------------

        self.confirm_button = discord.ui.Button(
            label="Подтвердить",
            style=discord.ButtonStyle.primary,
        )
        self.confirm_button.callback = self.confirm_callback

    def build_content(self, container: discord.Container):
        self.update_buttons()

        container.add_item(
            discord.ui.TextDisplay(
                '*Этим меню будут пользоваться одновременно чаще всего и одновременно никогда. И то, наверное, только Дракон (долгих лет ему жизни.)\n\n'
                'Чтобы использовать это меню, нужно изменить канал или каналы на нужные значения, а затем нажать «Подтвердить». Тут всё довольно наглядно, поэтому не побоюсь этого страшного слова — «интуитивно».\n\n'
                'Если вы мисскликнули по каналу и после этого не можете выбрать его снова, то это потому, что в подобных выпадающих списках нельзя нажать на один и тот же канал дважды. Я долго пытался решить эту проблему, но в итоге забил и оставил хотя бы это сообщение.\n\n'
                '-# Во избежание проблемы выше можно не мисскликать. Ну или нажать по очереди на другие каналы, чтобы сбросить «хвост».*'
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                self.get_blocked_channels_text()
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.blocked_channels_select
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                self.get_roles_channels_text()
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.blocked_roles_select
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                self.get_blocked_users_text()
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.blocked_users_select
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                self.get_karma_channel_text()
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.karma_channel_select
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                self.get_log_channel_text()
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.log_channel_select
            )
        )

    def build_footer_buttons(self):
        footer_buttons = [self.confirm_button]
        return footer_buttons

    def get_karma_channel_text(self) -> str:
        if self.karma_channel_id is None:
            return "### Канал оповещений: не выбран"

        if self.karma_channel_id == self.original_karma_channel_id:
            return (
                f"### Канал оповещений: "
                f"<#{self.karma_channel_id}>"
            )
        else:
            return (
                f"### Канал оповещений: "
                f"__<#{self.karma_channel_id}>__*"
            )

    def get_log_channel_text(self) -> str:
        if self.log_channel_id is None:
            return "### Канал логов: не выбран"

        if self.log_channel_id == self.original_log_channel_id:
            return (
                f"### Канал логов: "
                f"<#{self.log_channel_id}>"
            )
        else:
            return (
                f"### Канал логов: "
                f"__<#{self.log_channel_id}>__*"
            )

    def get_blocked_channels_text(self) -> str:
        if not self.blocked_channels_id and (
                self.original_blocked_channels_id
                == self.blocked_channels_id
        ):
            return "### Заблокированные каналы: не выбраны"

        parts = []

        for channel_id in (self.original_blocked_channels_id | self.blocked_channels_id):
            removed = (
                    channel_id in self.original_blocked_channels_id
                    and channel_id not in self.blocked_channels_id
            )

            added = (
                    channel_id not in self.original_blocked_channels_id
                    and channel_id in self.blocked_channels_id
            )

            if removed:
                parts.append(f"~~<#{channel_id}>~~\\*")
            elif added:
                parts.append(f"__<#{channel_id}>__\\*")
            else:
                parts.append(f"<#{channel_id}>")

        if not parts:
            return "### Заблокированные каналы: не выбраны"

        return (
                "### Заблокированные каналы: "
                + ", ".join(parts)
        )

    def get_roles_channels_text(self) -> str:
        if not self.blocked_roles_id and (
                self.original_blocked_roles_id
                == self.blocked_roles_id
        ):
            return "### Заблокированные роли: не выбраны"

        parts = []

        for role_id in (self.original_blocked_roles_id | self.blocked_roles_id):
            removed = (
                    role_id in self.original_blocked_roles_id
                    and role_id not in self.blocked_roles_id
            )

            added = (
                    role_id not in self.original_blocked_roles_id
                    and role_id in self.blocked_roles_id
            )

            if removed:
                parts.append(f"~~<@&{role_id}>~~\\*")
            elif added:
                parts.append(f"<@&{role_id}>\\*")
            else:
                parts.append(f"<@&{role_id}>")

        if not parts:
            return "### Заблокированные роли: не выбраны"

        return (
                "### Заблокированные роли: "
                + ", ".join(parts)
        )

    async def karma_channel_callback(self, interaction: discord.Interaction) -> None:
        channel = self.karma_channel_select.values[0]

        self.karma_channel_id = channel.id

        self.update_buttons()

        await self.navigator.render()
        await interaction.response.defer()

    async def log_channel_callback(self, interaction: discord.Interaction) -> None:
        channel = self.log_channel_select.values[0]

        self.log_channel_id = channel.id

        self.update_buttons()

        await self.navigator.render()
        await interaction.response.defer()

    async def blocked_channels_callback(self, interaction: discord.Interaction) -> None:
        channels = [channel.id for channel in self.blocked_channels_select.values]

        self.blocked_channels_id.symmetric_difference_update(channels)

        await self.navigator.render()
        await interaction.response.defer()

    def get_blocked_users_text(self) -> str:
        """Как у ролей: зачёркнут — будет снят, со звёздочкой — будет добавлен после «Подтвердить»."""
        if not self.blocked_users_id and not self.original_blocked_users_id:
            return "### Заблокированные участники: не выбраны"

        parts = []

        for user_id in (self.original_blocked_users_id | self.blocked_users_id):
            removed = user_id in self.original_blocked_users_id and user_id not in self.blocked_users_id
            added = user_id not in self.original_blocked_users_id and user_id in self.blocked_users_id

            if removed:
                parts.append(f"~~<@{user_id}>~~\\*")
            elif added:
                parts.append(f"<@{user_id}>\\*")
            else:
                parts.append(f"<@{user_id}>")

        return "### Заблокированные участники: " + ", ".join(parts)

    async def blocked_users_callback(self, interaction: discord.Interaction) -> None:
        users = [user.id for user in self.blocked_users_select.values]

        self.blocked_users_id.symmetric_difference_update(users)

        self.update_buttons()

        await self.navigator.render()
        await interaction.response.defer()

    async def blocked_roles_callback(self, interaction: discord.Interaction) -> None:
        roles = [role.id for role in self.blocked_roles_select.values]

        self.blocked_roles_id.symmetric_difference_update(roles)

        await self.navigator.render()
        await interaction.response.defer()

    async def confirm_callback(self, interaction: discord.Interaction) -> None:
        if self.karma_channel_id != self.original_karma_channel_id:
            _state.data.karma_channel_id = self.karma_channel_id
            self.original_karma_channel_id = self.karma_channel_id

        if self.log_channel_id != self.original_log_channel_id:
            _state.data.log_channel_id = self.log_channel_id
            self.original_log_channel_id = self.log_channel_id

        if self.blocked_channels_id != self.original_blocked_channels_id:
            _state.data.blocked_channels_id = list(self.blocked_channels_id)
            self.original_blocked_channels_id = set(self.blocked_channels_id)

        if self.blocked_roles_id != self.original_blocked_roles_id:
            _state.data.blocked_roles_id = list(self.blocked_roles_id)
            self.original_blocked_roles_id = set(self.blocked_roles_id)

        if self.blocked_users_id != self.original_blocked_users_id:
            _state.data.blocked_users_id = list(self.blocked_users_id)
            self.original_blocked_users_id = set(self.blocked_users_id)

        await self.navigator.render()
        await interaction.response.defer()

    def update_buttons(self) -> None:
        edited = (
            self.karma_channel_id != self.original_karma_channel_id
            or self.log_channel_id != self.original_log_channel_id
            or self.blocked_channels_id != self.original_blocked_channels_id
            or self.blocked_roles_id != self.original_blocked_roles_id
            or self.blocked_users_id != self.original_blocked_users_id
        )

        self.confirm_button.disabled = not edited

class SettingsLevelUpPage(Page):
    """
    Страница настроек текстов повышений.
    """
    title = 'Карма -> Текст повышений'

    def __init__(self, navigator: Navigator, author: discord.Member, bot: OzernikiBot):
        super().__init__(navigator, author)
        self.bot = bot
        self.buttons = []

        self.update_buttons()

    def update_buttons(self):
        self.buttons = []
        texts = _state.db.get_level_texts()
        # Ключи — строки: в окне правки тексты показываются как {'5': 'текст'}.
        levels = [(str(level), texts.get(level, '')) for level in range(1, LEVEL_TEXTS_COUNT + 1)]

        for i in range(0, len(levels), 20):
            group = tuple(levels[i:i + 20])

            button = discord.ui.Button(
                label=f"Изменить ({i + 1} - {i + 20})",
                style=discord.ButtonStyle.primary,
            )

            async def callback(
                    interaction: discord.Interaction,
                    _group=group,
            ):
                try:
                    await interaction.response.send_modal(self.LevelUpTextModal(_group, self))
                except Exception:
                    traceback.print_exc()

            button.callback = callback

            self.buttons.append(button)

    class LevelUpTextModal(discord.ui.Modal):
        def __init__(self, levels_tuple: tuple, page):
            super().__init__(title="Текст повышения")
            self.page = page

            str_levels = collapse_dict(dict(levels_tuple))

            self.text_input = discord.ui.TextInput(
                label="Текст",
                style=discord.TextStyle.long,
                default=str_levels,
                placeholder="Введите текст повышения...",
                required=True,
                max_length=4000,
            )

            self.add_item(self.text_input)

        async def on_submit(self, interaction: discord.Interaction):
            await interaction.response.defer()
            try:
                new_levels_data = expand_dict(self.text_input.value)

                try:
                    texts = parse_level_texts(new_levels_data)
                except ValueError as error:
                    await interaction.followup.send(f'Не сохранено. {error}', ephemeral=True)
                    return

                _state.db.set_level_texts(texts)

                self.page.update_buttons()
                await self.page.navigator.render()

                await interaction.followup.send('Изменено.', ephemeral=True)
            except SyntaxError as error:
                message = str(error)

                if "unterminated string literal" in message:
                    explanation = "Строка не закрыта. Проверьте кавычки."
                elif "unterminated triple-quoted string literal" in message:
                    explanation = "Многострочная строка не закрыта. Проверьте тройные кавычки."
                elif "unexpected EOF while parsing" in message:
                    explanation = "Выражение неожиданно закончилось. Возможно, не хватает закрывающей скобки или кавычки."
                elif "invalid syntax" in message:
                    explanation = "Обнаружена ошибка в синтаксисе."
                else:
                    explanation = message

                text = self.text_input.value

                if error.lineno is not None:
                    explanation += f"\nСтрока: {error.lineno}"
                    lines = text.splitlines()
                    if 1 <= error.lineno <= len(lines):
                        # Подчёркиваем строку с ошибкой.
                        lines.insert(error.lineno, '^' * len(lines[error.lineno - 1]))
                    text = "\n".join(lines)


                if error.offset is not None:
                    explanation += f"\nПозиция: {error.offset}"

                await interaction.followup.send(
                    f'{explanation}\n```{text}```',
                    ephemeral=True
                )

    def build_content(self, container: discord.Container):
        container.add_item(
            discord.ui.TextDisplay(
                '*Речь о текстах которые показываются при повышении уровней Сансары.*\n'
                '*Изменяются только __явно__ измененные значения. То есть, при удалении строки {\'n\': \'qwerty\'} из блока изменений целиком, её содержимое изменено не будет.*\n'
                '*Для упоминания ролей, каналов, пользователей, игр, времени или чего-бы то ни было в каком-то из уровней, используйте стандартную нотацию Discord через айди или ключевое слово. (пример мне делать лень)*'
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                '-# *Тут планируется показ уровней, но Габу его делать лень, остальная Сансара не ждет (она ждёт).*'
            )
        )

        for button in self.buttons:
            container.add_item(
                discord.ui.ActionRow(
                    button
                )
            )

class SettingsSansaraPage(Page):
    """
    Страница настроек Сансары.
    """
    title = 'Карма -> Сансара'
    restore_task: asyncio.Task | None = None

    def __init__(self, navigator: Navigator, author: discord.Member, bot: OzernikiBot):
        super().__init__(navigator, author)
        self.bot = bot
        self.roles = Roles(bot)

        # Кнопка задержки между выдачей опыта сообщений
        self.karma_message_delay_button = discord.ui.Button(
            label="Изменить",
            style=discord.ButtonStyle.primary,
        )

        async def karma_message_delay_button_callback(interaction: discord.Interaction):
            await interaction.response.send_modal(self.DelayModal(self.navigator))

        self.karma_message_delay_button.callback = karma_message_delay_button_callback

        self.karma_edit_roles_by_data_button = self.create_confirm_button(
            label="Восстановить роли",
            confirm_label="Подтвердить восстановление ролей",
            action=self._restore_roles,
        )

    async def _restore_roles(self, interaction: Interaction):
        try:
            if self.restore_task is not None and not self.restore_task.done():
                await interaction.followup.send(
                    'Задача уже выполняется',
                    ephemeral=True
                )
                return

            guild = self.bot.guild
            sansara_roles = await self.roles.get_sansara_roles(recreate=True, restore=True)
            sansara_role_ids = {role.id for role in sansara_roles}

            async def restore_roles():
                message: discord.WebhookMessage = await interaction.followup.send(
                    f'Обновление ролей участников: 0/{len(guild.members)}',
                    ephemeral=True
                )

                n = 0

                for member in guild.members:
                    n += 1

                    if n % 27 == 0:
                        await message.edit(
                            content=f'Обновление ролей участников: {n}/{len(guild.members)}'
                        )

                    if member.bot:
                        # Ботам роли Сансары не положены — снимаем, если были выданы.
                        bot_sansara_roles = [role for role in member.roles if role.id in sansara_role_ids]

                        if bot_sansara_roles:
                            await member.remove_roles(
                                *bot_sansara_roles,
                                reason="Ботам роли Сансары не выдаются",
                            )

                        continue

                    ozernik = self.bot.db_ensure_user(member)

                    karma = _state.db.get_karma(ozernik.id)
                    status = get_status(karma)

                    target_role = await self.roles.get_sansara_role(
                        status["tag_name"]
                    )

                    current_sansara_roles = [
                        role
                        for role in member.roles
                        if role.id in sansara_role_ids
                    ]

                    if (
                        len(current_sansara_roles) == 1
                        and current_sansara_roles[0].id == target_role.id
                    ):
                        continue

                    roles_to_remove = [
                        role
                        for role in current_sansara_roles
                        if role.id != target_role.id
                    ]

                    if roles_to_remove:
                        await member.remove_roles(
                            *roles_to_remove,
                            reason="Восстановление ролей Сансары",
                        )

                    if target_role not in member.roles:
                        await member.add_roles(
                            target_role,
                            reason="Восстановление ролей Сансары",
                        )


                await message.delete()
                await interaction.followup.send(
                    f'Обновление ролей участников завершено.',
                    ephemeral=True
                )

            SettingsSansaraPage.restore_task = asyncio.create_task(restore_roles())
        except Exception as e:
            tb = traceback.format_exc()
            print(tb)
            print(e)

    class DelayModal(discord.ui.Modal):
        def __init__(self, navigator: Navigator):
            super().__init__(title="Задержка между сообщениями")

            self.navigator = navigator

            delay = _state.data.karma_message_delay

            str_delay = str(delay)

            self.text_input = discord.ui.TextInput(
                label="Задержка между сообщениями в секундах",
                style=discord.TextStyle.short,
                default=str_delay,
                placeholder=f"1–9",
                required=True,
                max_length=1,
            )

            self.add_item(self.text_input)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                value = int(self.text_input.value)
            except ValueError:
                await interaction.response.send_message(
                    "Введите целое число.",
                    ephemeral=True,
                )
                return

            if not 1 <= value <= 9:
                await interaction.response.send_message(
                    "Введите число от 1 до 9.",
                    ephemeral=True,
                )
                return

            _state.data.karma_message_delay = value
            await interaction.response.defer()
            await self.navigator.render()

        async def on_error(
                self,
                interaction: discord.Interaction,
                error: Exception,
        ) -> None:
            traceback_text = traceback.format_exc()

            print(
                f"Error:\n"
                f"{traceback_text}"
            )

            if interaction.response.is_done():
                await interaction.followup.send(
                    f"Произошла ошибка:\n```python\n{error}\n```",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"Произошла ошибка:\n```python\n{error}\n```",
                    ephemeral=True,
                )

    def build_content(self, container: discord.Container):
        container.add_item(
            discord.ui.TextDisplay(
                '*Это меню я изначально не хотел делать, но потом подумал, что редактирование ролей Сансары — довольно важная функция.\n'
                'Как ни странно, это меню я сделал третьим — после «Общих» и «Текстов повышений». Поэтому решил совместить в нём подходы, которые использовал в этих двух меню.\n\n'
                'Здравствуй, админ, решивший сюда заглянуть. Вероятно, просто чтобы просто почитать.*\n'
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                self.get_karma_roles_text()
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                f'## Уровни сансары:\n'
                f'*Каждые десять уровней увеличивается количество кармы, необходимое для перехода на следующий уровень: '
                f'сначала на 100 ед. к., затем на 200, затем на 400, затем на 800 и т. д. '
                f'Поэтому чем выше уровень Сансары, тем больше кармы требуется для каждого нового уровня.*'
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                self.get_karma_levels_text()
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                '## Восстановление ролей:\n'
                '*Все роли которые подверглись изменениям будут возвращены в исходное состояние (название и цвет), также будут возвращены удаленные роли.\n'
                'Также все пользователи которым выдана неправильная роль или не выдана роль будут обновлены.\n'
                '-# Может занять некоторое время.*'
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.karma_edit_roles_by_data_button
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                self.get_karma_message_delay_text()
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                '*Карма выдается максимум только раз в определенный промежуток времени — это задержка между выдачей Кармы.*'
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.karma_message_delay_button
            )
        )

    @staticmethod
    def get_karma_message_delay_text() -> str:
        if _state.data.karma_message_delay:
            n = format_duration_seconds(_state.data.karma_message_delay, True)
            return (
                f"## Задержка между выдачей Кармы: "
                f"{n}."
            )
        else:
            return (
                f"## Задержка между выдачей Кармы: "
                f"Не задана"
            )

    @staticmethod
    def get_karma_roles_text() -> str:
        lines = [f'## Роли сансары:']
        for karma_role in _state.data.karma_roles.values():
            required_karma = karma_role['required_karma']
            level = get_level(required_karma)

            if level is None:
                level = '25+'
                required_karma = '5000+'

            lines.append(
                f'- <@&{karma_role['role_id']}>:'
                f' `{karma_role['name']}`,'
                f' `{required_karma}` кармы ({level} ур.)''.'
            )

        return '\n'.join(lines)

    @staticmethod
    def get_karma_levels_text() -> str:
        fin_lines = []

        lines = []
        for level in range(1, 31):
            required_karma = get_karma(level)
            str_level = str(level).zfill(2)

            lines.append(
                f'{str_level} ур: `{required_karma}`'
            )

        third = math.ceil(len(lines) / 3)

        for i in range(third):
            left = lines[i]
            middle = lines[i + third] if i + third < len(lines) else ""
            right = lines[i + third * 2] if i + third * 2 < len(lines) else ""

            left_text = f'{left}' if left else ''
            middle_text = f'{middle}' if middle else ''
            right_text = f'{right}' if right else ''

            line = f"{left_text.ljust(14)} {middle_text.ljust(14)} {right_text}"

            line = line.replace('1 ур: ', '1 ур:  ')
            line = line.replace('07 ур: ', '07 ур:  ')
            line = line.replace('10 ур: ', '10 ур:  ')
            line = line.replace('`1000`  ', '`1000` ')
            line = line.replace('`3000`  ', '`3000` ')

            fin_lines.append(line)

        fin_lines.append('и т.д.')

        return '\n'.join(fin_lines)

class SettingsCubesPage(Page):
    """
    Страница настроек Кубов.
    """
    title = 'Карма -> Кубы'
    restore_task: asyncio.Task | None = None

    def __init__(self, navigator: Navigator, author: discord.Member, bot: OzernikiBot):
        super().__init__(navigator, author)
        self.bot = bot
        self.roles = Roles(bot)

        # Кнопка изменения предела золотого куба
        self.gold_cube_required_karma_button = discord.ui.Button(
            label="Изменить",
            style=discord.ButtonStyle.primary,
        )

        async def gold_cube_required_karma_button_callback(interaction: discord.Interaction):
            await interaction.response.send_modal(self.GoldCubeModal(self.navigator))

        self.gold_cube_required_karma_button.callback = gold_cube_required_karma_button_callback

        self.cubes_edit_roles_by_data_button = self.create_confirm_button(
            label="Восстановить роли",
            confirm_label="Подтвердить восстановление ролей",
            action=self._restore_roles,
        )

    async def _restore_roles(self, interaction: Interaction):
        try:
            if self.restore_task is not None and not self.restore_task.done():
                await interaction.followup.send(
                    'Задача уже выполняется',
                    ephemeral=True
                )
                return

            guild = self.bot.guild
            cube_roles = await self.roles.get_cube_roles(recreate=True, restore=True)

            async def restore_roles():
                message: discord.WebhookMessage = await interaction.followup.send(
                    f'Обновление ролей участников: 0/{len(guild.members)}',
                    ephemeral=True
                )

                n = 0

                for member in guild.members:
                    n += 1

                    if n % 27 == 0:
                        await message.edit(
                            content=(
                                f'Обновление ролей участников: '
                                f'{n}/{len(guild.members)}'
                            )
                        )

                    if member.bot:
                        # Ботам роли Кубов не положены — снимаем, если были выданы.
                        bot_cube_roles = [role for role in member.roles if role in cube_roles]

                        if bot_cube_roles:
                            await member.remove_roles(
                                *bot_cube_roles,
                                reason="Ботам роли Кубов не выдаются",
                            )

                        continue

                    ozernik = self.bot.db_ensure_user(member)

                    member_cube = get_cube_status(ozernik.id)

                    if not member_cube:
                        continue

                    member_cube_role = await self.roles.get_cube_role(
                        member_cube['tag_name']
                    )

                    current_cube_roles = [
                        role
                        for role in member.roles
                        if role in cube_roles
                    ]

                    if (
                            len(current_cube_roles) == 1
                            and current_cube_roles[0] == member_cube_role
                    ):
                        continue

                    roles_to_remove = [
                        role
                        for role in current_cube_roles
                        if role != member_cube_role
                    ]

                    if roles_to_remove:
                        await member.remove_roles(
                            *roles_to_remove,
                            reason="Восстановление ролей Кубов",
                        )

                    if member_cube_role not in member.roles:
                        await member.add_roles(
                            member_cube_role,
                            reason="Восстановление ролей Кубов",
                        )

                await message.delete()
                await interaction.followup.send(
                    'Обновление ролей участников завершено.',
                    ephemeral=True
                )

            SettingsCubesPage.restore_task = asyncio.create_task(restore_roles())
        except Exception as e:
            tb = traceback.format_exc()
            print(tb)
            print(e)

    class GoldCubeModal(discord.ui.Modal):
        def __init__(self, navigator: Navigator):
            super().__init__(title="Необходимая Карма связи для Золотого Куба")

            self.navigator = navigator

            required_days = _state.data.cube_roles['gold_cube']['required_karma'] / 60 / 24

            self.text_input = discord.ui.TextInput(
                label="Необходимая Карма связи для Золотого Куба",
                style=discord.TextStyle.short,
                default=f"{required_days:g}",
                placeholder=f"От 3, до 7.",
                required=True,
                max_length=1,
            )

            self.add_item(self.text_input)

        async def on_submit(self, interaction: discord.Interaction):
            if interaction.user.id not in config.OWNERS_IDS:
                await interaction.response.send_message(
                    "Доступно только <@512079329619083291>. Согласуйте это изменение с ним или другим разработчиком Бота если Габ исчез.",
                    ephemeral=True,
                )
                return

            try:
                value = int(self.text_input.value)
            except ValueError:
                await interaction.response.send_message(
                    "Введите целое число.",
                    ephemeral=True,
                )
                return

            if not 3 <= value <= 7:
                await interaction.response.send_message(
                    "Введите число от 3 до 7.",
                    ephemeral=True,
                )
                return

            required_karma = value * 24 * 60

            cube_roles = _state.data.cube_roles
            cube_roles['gold_cube']['required_karma'] = required_karma
            _state.data.cube_roles = cube_roles

            await interaction.response.defer()
            await self.navigator.render()

        async def on_error(
                self,
                interaction: discord.Interaction,
                error: Exception,
        ) -> None:
            traceback_text = traceback.format_exc()

            print(
                f"Error:\n"
                f"{traceback_text}"
            )

            if interaction.response.is_done():
                await interaction.followup.send(
                    f"Произошла ошибка:\n```python\n{error}\n```",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"Произошла ошибка:\n```python\n{error}\n```",
                    ephemeral=True,
                )

    def build_content(self, container: discord.Container):
        container.add_item(
            discord.ui.TextDisplay(
                '*Если делать меню Сансары, то надо делать меню Кубов.\n'
                'Я просто скопировал меню Сансары и поменял значения.\n\n'
                'Расцветё-ё-ё-ём, на во-одоле-е.*\n'
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                self.get_cubes_roles_text()
            )
        )

        cube_roles = _state.data.cube_roles

        container.add_item(
            discord.ui.TextDisplay(
                '## О Кубах и Связи\n'
                'Кармическая связь или просто "Связь"  — это единица опыта выдаваемая за время в голосовой канале с конкретным человеком.\n'
                '- 1 минута это 1 ед. связи.\n'
                '- У каждой пары юзер-юзер есть отдельный счётчик Связи.\n'
                '- Каждый юзер имеет таблицу Связи со всеми юзерами, с которыми он когда-либо проводил время в голосовых каналах.\n'
                'Уровень воспоминаний — это оценка количества накопленной Связи между двумя конкретными юзерами. Имеет всего четыре уровня:\n'
               f'- Черная — {cube_roles['black_cube']['required_karma']} ед. с.\n'
               f'- Белая — {cube_roles['white_cube']['required_karma']} ед. с. ({format_duration_minutes(cube_roles['white_cube']['required_karma'])})\n'
               f'- Синяя — {cube_roles['blue_cube']['required_karma']} ед. с. ({format_duration_minutes(cube_roles['blue_cube']['required_karma'])})\n'
               f'- Золотая — {cube_roles['gold_cube']['required_karma']} ед. с. ({format_duration_minutes(cube_roles['gold_cube']['required_karma'])})\n'
                'Этап воспоминаний — это общая оценка Связи юзера с другими людьми. Он определяется количеством его связей, достигших определённого уровня. Для повышения этапа необходимо иметь как минимум десять связей соответствующего уровня или выше.\n'
                '- Черный куб — 1 связь являются Черной или выше.\n'
                '- Белый куб — 10 связей являются Белыми или выше.\n'
                '- Синий куб — 10 связей являются Синими или выше.\n'
                '- Золотой куб — 10 связей являются Золотыми.\n'
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                '## Восстановление ролей:\n'
                '*Все роли которые подверглись изменениям будут возвращены в исходное состояние (название и цвет), также будут возвращены удаленные роли.\n'
                'Также все пользователи которым выдана неправильная роль или не выдана роль будут обновлены.\n'
                '-# Может занять некоторое время.*'
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.cubes_edit_roles_by_data_button
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                self.get_gold_cube_required_karma_text()
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                '*Необходимое количество дней для Золотой Связи можно изменить.*'
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.gold_cube_required_karma_button
            )
        )

    @staticmethod
    def get_gold_cube_required_karma_text() -> str:
        format_duration = format_duration_minutes(_state.data.cube_roles['gold_cube']['required_karma'], True)
        return (
            f"## Количество дней для Золотой Связи: "
            f"{format_duration}."
        )

    @staticmethod
    def get_cubes_roles_text() -> str:
        lines = [f'## Роли Кубов:']
        for cube_role in _state.data.cube_roles.values():
            required_karma = cube_role['required_karma']
            format_duration = format_duration_minutes(required_karma)

            if not format_duration:
                format_duration = '0 минут'

            lines.append(
                f'- <@&{cube_role['role_id']}>:'
                f' `{cube_role['name']}`,'
                f' `{required_karma}` ед. с. ({format_duration}) для 1 связи.'
            )

        return '\n'.join(lines)
