import json
import os # noqa
import random
import inspect
import traceback
import discord
import asyncio # noqa
from copy import deepcopy
from datetime import timedelta # noqa
from discord import app_commands, Interaction, Webhook, InvalidData  # noqa
from discord.ext import commands
from discord.ui import View, Button
from unicodedata import category

from config import config
from utilities import DataWorker, JsonWorker # noqa


data = DataWorker('modules/cold_old_man/data.json')

class ColdOldMan(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guild: discord.Guild = bot.get_guild(config.GUILD_ID)
        self.category_and_roles = self.CategoryAndRoles(cog=self)
        self.buttons = self.Buttons
        self.messages = self.Messages(cog=self)
        self.game = self.Game(cog=self)
        self.table = self.Table(cog=self, json_path='modules/cold_old_man/table.json')
        self.utility = self.Utility(cog=self)
        self.countdown = None

    async def send_log(self, func_name: str, text: str, user: discord.User | None = None):
        name_str = ''
        if user:
            name_str = f' у {user.name}'
        content = f'Произошла ошибка в {func_name}{name_str}:\n```\n{text}\n```'
        await self.category_and_roles.log_channel.send(content)

    async def cog_load(self):
        self.bot.add_view(self.buttons.RulesView(self))

    class CategoryAndRoles:
        def __init__(self, cog: commands.Cog):
            self.cog: ColdOldMan = cog
            self.category: discord.CategoryChannel = None
            self.rules_channel: discord.TextChannel = None
            self.announcements_channel: discord.TextChannel = None
            self.general_channel: discord.TextChannel = None
            self.log_channel: discord.TextChannel = None
            self.player_role: discord.Role = None
            self.frozen_role: discord.Role = None
            self.announce_role: discord.Role = None
            self.get_category_and_roles()

        def get_category_and_roles(self):
            guild = self.cog.guild
            try:
                self.category = guild.get_channel(data.CATEGORY_ID)
            except AttributeError:
                pass
            try:
                self.rules_channel = guild.get_channel(data.RULES_CHANNEL_ID)
            except AttributeError:
                pass
            try:
                self.announcements_channel = guild.get_channel(data.ANNOUNCEMENTS_CHANNEL_ID)
            except AttributeError:
                pass
            try:
                self.general_channel = guild.get_channel(data.GENERAL_CHANNEL_ID)
            except AttributeError:
                pass
            try:
                self.log_channel = guild.get_channel(data.LOG_CHANNEL_ID)
            except AttributeError:
                pass
            try:
                self.player_role = guild.get_role(data.PLAYER_ROLE_ID)
            except AttributeError:
                pass
            try:
                self.frozen_role = guild.get_role(data.FROZEN_ROLE_ID)
            except AttributeError:
                pass
            try:
                self.announce_role = guild.get_role(data.ANNOUNCE_ROLE_ID)
            except AttributeError:
                pass

        async def fetch_category(self):
            guild = self.cog.guild
            try:
                self.category = await guild.fetch_channel(data.CATEGORY_ID)
            except Exception as e:
                await self.cog.send_log(func_name='fetch_category', text=e)
                print('Category not found')
            try:
                self.rules_channel = await guild.fetch_channel(data.RULES_CHANNEL_ID)
            except Exception as e:
                await self.cog.send_log(func_name='fetch_category', text=e)
                print('Rule not found')
            try:
                self.announcements_channel = await guild.fetch_channel(data.ANNOUNCEMENTS_CHANNEL_ID)
            except Exception as e:
                await self.cog.send_log(func_name='fetch_category', text=e)
                print('Announcements not found')
            try:
                self.general_channel = await guild.fetch_channel(data.GENERAL_CHANNEL_ID)
            except Exception as e:
                await self.cog.send_log(func_name='fetch_category', text=e)
                print('General not found')
            try:
                self.log_channel = await guild.fetch_channel(data.LOG_CHANNEL_ID)
            except Exception as e:
                await self.cog.send_log(func_name='fetch_category', text=e)
                print('Log channel not found')

        async def fetch_roles(self):
            guild = self.cog.guild
            try:
                self.player_role = await guild.fetch_role(data.PLAYER_ROLE_ID)
            except Exception as e:
                await self.cog.send_log(func_name='fetch_roles', text=e)
                print('Player role not found')
            try:
                self.frozen_role = await guild.fetch_role(data.FROZEN_ROLE_ID)
            except Exception as e:
                await self.cog.send_log(func_name='fetch_roles', text=e)
                print('Frozen role not found')
            try:
                self.announce_role = await guild.fetch_role(data.ANNOUNCE_ROLE_ID)
            except Exception as e:
                await self.cog.send_log(func_name='fetch_roles', text=e)
                print(f'announce role not found')

        async def create_category_and_roles(self):
            guild = self.cog.guild
            not_view = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
            not_send_and_not_view = {guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False)}
            print(1)
            self.category = await guild.create_category(
                name='Пьяный Алхимик',
                reason='Канал создан автоматически.',
                overwrites=not_view,
            )
            print(2)
            data.CATEGORY_ID = self.category.id
            print(3)
            self.rules_channel = await guild.create_text_channel(
                name='📜🎩-правила',
                category=self.category,
                reason='Канал создан автоматически.',
                overwrites=not_send_and_not_view,
            )
            print(4)
            data.RULES_CHANNEL_ID = self.rules_channel.id
            print(5)
            self.announcements_channel = await guild.create_text_channel(
                name='🚨📢-объявления',
                category=self.category,
                reason='Канал создан автоматически.',
                overwrites=not_send_and_not_view,
            )
            print(6)
            data.ANNOUNCEMENTS_CHANNEL_ID = self.announcements_channel.id
            print(7)
            self.general_channel = await guild.create_text_channel(
                name='🧪📖-обсуждение',
                category=self.category,
                reason='Канал создан автоматически.',
                overwrites=not_view,
            )
            data.GENERAL_CHANNEL_ID = self.general_channel.id

            self.log_channel = await guild.create_text_channel(
                name='log',
                category=self.category,
                reason='Канал создан автоматически.',
                overwrites=not_view,
            )
            data.LOG_CHANNEL_ID = self.log_channel.id

            self.frozen_role = await guild.create_role(
                name="Испорченная душа",
                reason=f"Роль восстановлена автоматически.",
                mentionable=True,
                colour=discord.Colour.from_str("#010101"),
            )
            data.FROZEN_ROLE_ID = self.frozen_role.id

            self.player_role = await guild.create_role(
                name="Алхимик",
                reason=f"Роль восстановлена автоматически.",
                mentionable=True,
                colour=discord.Colour.from_str("#1abc9c")
            )
            data.PLAYER_ROLE_ID = self.player_role.id

            self.announce_role = await guild.create_role(
                name="Страждущий искатель",
                reason=f"Роль восстановлена автоматически.",
                mentionable=True,
                colour=discord.Colour.from_str("#11806a")
            )
            data.ANNOUNCE_ROLE_ID = self.announce_role.id

        async def recreate_category(self):
            guild = self.cog.guild
            not_send = {guild.default_role: discord.PermissionOverwrite(send_messages=False)}
            not_view = {guild.default_role: discord.PermissionOverwrite(view_channel=True)}

            if not self.category:
                self.category = await guild.create_category(
                    name='Пьяный Алхимик',
                    reason='Канал создан автоматически.'
                )
                data.CATEGORY_ID = self.category.id

            if not self.rules_channel:
                self.rules_channel = await guild.create_text_channel(
                    name='📜🎩-правила',
                    category=category,
                    reason='Канал создан автоматически.',
                    overwrites=not_send,
                )
                data.RULES_CHANNEL_ID = self.rules_channel.id

            if not self.announcements_channel:
                self.announcements_channel = await guild.create_text_channel(
                    name='🚨📢-объявления',
                    category=category,
                    reason='Канал создан автоматически.',
                    overwrites=not_send,
                )
                data.ANNOUNCEMENTS_CHANNEL_ID = self.announcements_channel.id

            if not self.general_channel:
                self.general_channel = await guild.create_text_channel(
                    name='🧪📖-обсуждение',
                    category=category,
                    reason='Канал создан автоматически.'
                )
                data.GENERAL_CHANNEL_ID = self.general_channel.id

            if not self.log_channel:
                self.log_channel = await guild.create_text_channel(
                    name='log',
                    category=category,
                    reason='Канал создан автоматически.',
                    overwrites=not_view,
                )
                data.LOG_CHANNEL_ID = self.log_channel.id

        async def recreate_roles(self):
            guild = self.cog.guild
            if not self.frozen_role:
                self.frozen_role = await guild.create_role(
                    name="Испорченная душа",
                    reason=f"Роль восстановлена автоматически.",
                    mentionable=True,
                    colour=discord.Colour.from_str("#010101"),
                )
                data.FROZEN_ROLE_ID = self.frozen_role.id

            if not self.player_role:
                self.player_role = await guild.create_role(
                    name="Алхимик",
                    reason=f"Роль восстановлена автоматически.",
                    mentionable=True,
                    colour=discord.Colour.from_str("#1abc9c")
                )
                data.PLAYER_ROLE_ID = self.player_role.id

            if not self.announce_role:
                self.announce_role = await guild.create_role(
                    name="Страждущий искатель",
                    reason=f"Роль восстановлена автоматически.",
                    mentionable=True,
                    colour=discord.Colour.from_str("#11806a")
                )
                data.ANNOUNCE_ROLE_ID = self.announce_role.id

        async def open_category(self):
            guild = self.cog.guild
            await self.category.set_permissions(guild.default_role, view_channel=True)
            await self.rules_channel.set_permissions(guild.default_role, view_channel=True)
            await self.announcements_channel.set_permissions(guild.default_role, view_channel=True)
            await self.general_channel.set_permissions(guild.default_role, view_channel=True)

        async def close_category(self):
            guild = self.cog.guild
            await self.category.set_permissions(guild.default_role, view_channel=False)
            await self.rules_channel.set_permissions(guild.default_role, view_channel=False)
            await self.announcements_channel.set_permissions(guild.default_role, view_channel=False)
            await self.general_channel.set_permissions(guild.default_role, view_channel=False)

        async def delete_roles(self):
            await self.player_role.delete()
            await self.frozen_role.delete()
            await self.announce_role.delete()

    class Buttons:
        class ConfirmOrCancelView(View):
            def __init__(self, author: discord.User):
                super().__init__(timeout=30)
                self.author = author
                self.value = None

            @discord.ui.button(label="Подтвердить", style=discord.ButtonStyle.green)
            async def confirm_button(self, button_interaction: Interaction, button: Button):  # noqa
                try:
                    if button_interaction.user != self.author:
                        return
                    self.value = True
                    self.stop()
                    await button_interaction.response.defer()
                except Exception as e:
                    func_name = inspect.currentframe().f_code.co_name
                    tb = traceback.format_exc()
                    print(f"Error in {func_name}: {tb}")
                    await self.send_log(user=interaction.user, text=e, func_name=func_name)
                    await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

            @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.gray)
            async def reject_button(self, button_interaction: Interaction, button: Button):  # noqa
                try:
                    if button_interaction.user != self.author:
                        return
                    self.value = False
                    self.stop()
                    await button_interaction.response.defer()
                except Exception as e:
                    func_name = inspect.currentframe().f_code.co_name
                    tb = traceback.format_exc()
                    print(f"Error in {func_name}: {tb}")
                    await self.send_log(user=interaction.user, text=e, func_name=func_name)
                    await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

        class CanceledView(View):
            def __init__(self):
                super().__init__(timeout=None)

            @discord.ui.button(label="Отменено", style=discord.ButtonStyle.secondary, disabled=True)  # noqa
            async def confirm(self, button_interaction: Interaction, button: Button):  # noqa
                pass

        class TimeoutView(View):
            def __init__(self):
                super().__init__(timeout=None)

            @discord.ui.button(label="Время истекло", style=discord.ButtonStyle.secondary, disabled=True)  # noqa
            async def confirm(self, button_interaction: Interaction, button: Button):  # noqa
                pass

        class ConfirmedView(View):
            def __init__(self):
                super().__init__(timeout=None)

            @discord.ui.button(label="Подтверждено", style=discord.ButtonStyle.green, disabled=True)  # noqa
            async def confirm(self, button_interaction: Interaction, button: Button):  # noqa
                pass

        class RulesView(View):
            def __init__(self, cog: commands.Cog):
                self.cog: ColdOldMan = cog
                super().__init__(timeout=None)

            @discord.ui.button(label="УЧАСТВОВАТЬ", style=discord.ButtonStyle.green, custom_id="join_button")
            async def join_button(self, interaction: discord.Interaction, button):  # noqa
                try:
                    await interaction.response.defer(ephemeral=True)
                    await self.cog.category_and_roles.fetch_roles()
                    if self.cog.table.add_player(user_id=interaction.user.id):
                        player = await self.cog.get_player(user_id=interaction.user.id)
                        asyncio.create_task(player.synchronize_roles())
                        asyncio.create_task(self.cog.messages.update_or_send_table())
                        await player.add_announce_role()
                        await interaction.followup.send("Участие зарегистрировано.", ephemeral=True)
                    else:
                        player = await self.cog.get_player(user_id=interaction.user.id)
                        asyncio.create_task(player.synchronize_roles())
                        asyncio.create_task(self.cog.messages.update_or_send_table())
                        await interaction.followup.send('Вы и так участник.', ephemeral=True)
                except Exception as e:
                    func_name = inspect.currentframe().f_code.co_name
                    tb = traceback.format_exc()
                    print(f"Error in {func_name}: {tb}")
                    await self.send_log(user=interaction.user, text=e, func_name=func_name)
                    await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

            @discord.ui.button(label="🔇/🔊", style=discord.ButtonStyle.green, custom_id="announcements_button")
            async def announcements_button(self, interaction: discord.Interaction, button): # noqa
                try:
                    await interaction.response.defer(ephemeral=True)
                    await self.cog.category_and_roles.fetch_roles()
                    announce_role = self.cog.category_and_roles.announce_role
                    if announce_role in interaction.user.roles:
                        asyncio.create_task(interaction.user.remove_roles(announce_role))
                        await interaction.followup.send('Роль уведомления снята.', ephemeral=True)
                    else:
                        asyncio.create_task(interaction.user.add_roles(announce_role))
                        await interaction.followup.send('Роль уведомления получена.', ephemeral=True)
                except Exception as e:
                    func_name = inspect.currentframe().f_code.co_name
                    tb = traceback.format_exc()
                    print(f"Error in {func_name}: {tb}")
                    await self.send_log(user=interaction.user, text=e, func_name=func_name)
                    await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

        class VotesView(View):
            def __init__(self, cog: commands.Cog):
                self.cog: ColdOldMan = cog
                super().__init__(timeout=None)

                self.up_voters = set()
                self.down_voters = set()

                self.up_count = 0
                self.down_count = 0

                self.button_up = discord.ui.Button(
                    emoji='🔼',
                    label=str(self.up_count),
                    style=discord.ButtonStyle.secondary
                )
                self.button_down = discord.ui.Button(
                    emoji='🔽',
                    label=str(self.down_count),
                    style=discord.ButtonStyle.secondary
                )

                self.button_up.callback = self.callback_up
                self.button_down.callback = self.callback_down

                self.add_item(self.button_up)
                self.add_item(self.button_down)

            def update_buttons(self):
                self.button_up.label = str(self.up_count)
                self.button_down.label = str(self.down_count)

                self.button_up.style = (
                    discord.ButtonStyle.green if self.up_count > 0
                    else discord.ButtonStyle.secondary
                )
                self.button_down.style = (
                    discord.ButtonStyle.red if self.down_count > 0
                    else discord.ButtonStyle.secondary
                )

            async def callback_up(self, interaction: discord.Interaction):
                if await self.cog.get_player(user_id=interaction.user.id) is None:
                    await interaction.response.send_message(
                        'Вы не зарегистрированы как игрок.',
                        ephemeral=True
                    )
                    return

                user_id = interaction.user.id

                if user_id in self.up_voters:
                    await interaction.response.defer(ephemeral=True)
                    return

                if user_id in self.down_voters:
                    self.down_voters.remove(user_id)
                    self.down_count -= 1

                self.up_voters.add(user_id)
                self.up_count += 1

                self.update_buttons()
                await interaction.response.edit_message(view=self)

            async def callback_down(self, interaction: discord.Interaction):
                if await self.cog.get_player(user_id=interaction.user.id) is None:
                    await interaction.response.send_message(
                        'Вы не зарегистрированы как игрок.',
                        ephemeral=True
                    )
                    return

                user_id = interaction.user.id

                if user_id in self.down_voters:
                    await interaction.response.defer(ephemeral=True)
                    return

                if user_id in self.up_voters:
                    self.up_voters.remove(user_id)
                    self.up_count -= 1

                self.down_voters.add(user_id)
                self.down_count += 1

                self.update_buttons()
                await interaction.response.edit_message(view=self)

        class RiddleView(View):
            def __init__(self, cog: commands.Cog):
                self.cog: ColdOldMan = cog
                super().__init__(timeout=3600)
                self.message: discord.Message | None = None
                self.riddle: str | None = None
                self.riddle_message: discord.Message | None = None

                self.answer: str | None = None
                self.answer_message: discord.Message | None = None

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user.id != data.PLAYER_MESS_ID:
                    await interaction.response.send_message('Вы не Просветленный.', ephemeral=True)
                    self.stop()
                    return False
                return True

            async def on_timeout(self) -> None:
                self.stop()

                view = self.cog.buttons.TimeoutView()

                if self.message is not None:
                    await self.message.edit(view=view)

            class RiddleTextModal(discord.ui.Modal, title="Загадка"):
                reason = discord.ui.TextInput(
                    label="Текст загадки который будет выложен.",
                    placeholder="Максимальная длина — 1900 символов",
                    style=discord.TextStyle.paragraph,  # многострочное
                    required=True,
                    max_length=1900,
                )
                def __init__(self, view: View):
                    super().__init__()
                    self.view: RiddleView = view

                async def on_submit(self, interaction: discord.Interaction):
                    self.view.riddle = self.reason.value
                    if not self.view.riddle_message:
                        self.view.riddle_message = await interaction.user.send(f'```{self.view.riddle}```')
                    else:
                        await self.view.riddle_message.edit(content=f'```{self.view.riddle}```')
                    await interaction.response.defer(ephemeral=True, thinking=False)

            class AnswerTextModal(discord.ui.Modal, title="Решение"):
                reason = discord.ui.TextInput(
                    label="Решение загадки.",
                    placeholder="Максимальная длина — 1900 символов",
                    style=discord.TextStyle.paragraph,  # многострочное
                    required=True,
                    max_length=1900,
                )
                def __init__(self, view: View):
                    super().__init__()
                    self.view: RiddleView = view

                async def on_submit(self, interaction: discord.Interaction):
                    self.view.answer = self.reason.value
                    if not self.view.answer_message:
                        self.view.answer_message = await interaction.user.send(f'```{self.view.answer}```')
                    else:
                        await self.view.answer_message.edit(content=f'```{self.view.answer}```')
                    await interaction.response.defer(ephemeral=True, thinking=False)

            @discord.ui.button(label="Загадка", style=discord.ButtonStyle.primary)
            async def riddle_form(self, interaction: discord.Interaction, button: Button): # noqa
                await interaction.response.send_modal(self.RiddleTextModal(self))

            @discord.ui.button(label="Решение", style=discord.ButtonStyle.primary)
            async def answer_form(self, interaction: discord.Interaction, button: Button):  # noqa
                await interaction.response.send_modal(self.AnswerTextModal(self))

            @discord.ui.button(label="Подтвердить", style=discord.ButtonStyle.gray)
            async def accept(self, interaction: discord.Interaction, button: Button): # noqa
                try:
                    if self.riddle is None:
                        await interaction.response.send_message('Загадка не введена.', ephemeral=True)
                        return
                    if self.answer is None:
                        await interaction.response.send_message('Решение не введено.', ephemeral=True)
                        return
                    await interaction.response.defer()
                    asyncio.create_task(self.cog.messages.send_riddle(riddle=self.riddle))
                    data.ANS_RIDDLE = self.answer
                    view = self.cog.buttons.ConfirmedView()
                    await interaction.edit_original_response(view=view)
                    self.stop()
                except Exception as e:
                    func_name = inspect.currentframe().f_code.co_name
                    print(f"Error in {func_name}: {e}")
                    await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```\n-# Обратитесь к Габу если это необходимо.", ephemeral=True)

    class Messages:
        def __init__(self, cog: commands.Cog):
            self.cog: ColdOldMan = cog

            self._wh_announce_index = 0
            self.wh_announce_1 = None
            self.wh_announce_2 = None
            self._wh_general_index = 0
            self.wh_general_1 = None
            self.wh_general_2 = None
            self.wh_avatar_url = 'https://cdn.discordapp.com/attachments/1108123669747732483/1477061989476864032/lake.png'

        async def _fetch_webhooks(self):
            announce_whs = await self.cog.category_and_roles.announcements_channel.webhooks()
            self.wh_announce_1 = next((wh for wh in announce_whs if wh.name == "Озеро"), None)
            if self.wh_announce_1 is None:
                self.wh_announce_1 = await self.cog.category_and_roles.announcements_channel.create_webhook(name="Озеро")
            self.wh_announce_2 = next((wh for wh in announce_whs if wh.name == "Oзерo"), None)
            if self.wh_announce_2 is None:
                self.wh_announce_2 = await self.cog.category_and_roles.announcements_channel.create_webhook(name="Oзерo")

            general_whs = await self.cog.category_and_roles.general_channel.webhooks()
            self.wh_general_1 = next((wh for wh in general_whs if wh.name == "Озеро"), None)
            if self.wh_general_1 is None:
                self.wh_general_1 = await self.cog.category_and_roles.general_channel.create_webhook(name="Озеро")
            self.wh_general_2 = next((wh for wh in general_whs if wh.name == "Oзерo"), None)
            if self.wh_general_2 is None:
                self.wh_general_2 = await self.cog.category_and_roles.general_channel.create_webhook(name="Oзерo")

        async def send_death_sign(self, user_id: int | str):
            await self._fetch_webhooks()
            announce_webhooks = [self.wh_announce_1, self.wh_announce_2]
            announce_webhook: Webhook = announce_webhooks[self._wh_announce_index]
            self._wh_announce_index = (self._wh_announce_index + 1) % 2

            general_webhooks = [self.wh_general_1, self.wh_general_2]
            general_webhook: Webhook = general_webhooks[self._wh_general_index]
            self._wh_general_index = (self._wh_general_index + 1) % 2

            death_sign = self.cog.utility.get_death_sign()
            await announce_webhook.send(content=f'**<@{user_id}> {death_sign}**', avatar_url=self.wh_avatar_url,
                                       allowed_mentions=discord.AllowedMentions.none())
            await general_webhook.send(content=f'**<@{user_id}> {death_sign}**', avatar_url=self.wh_avatar_url,
                                       allowed_mentions=discord.AllowedMentions.none())

        async def send_congratulation(self, target_id: int | str, added_score: float):
            announce_role = self.cog.category_and_roles.announce_role
            view = None
            answer_str = ''
            try:
                if data.ANS_RIDDLE:
                    view = self.cog.buttons.VotesView(cog=self.cog)
                    answer_str = f'**Ответ на загадку:**\n```{data.ANS_RIDDLE}```'
            except AttributeError:
                data.ANS_RIDDLE = None
            await self.cog.category_and_roles.announcements_channel.send(content=f'{announce_role.mention}\n# <@{target_id}> найден! (+{added_score})\n{answer_str}', view=view)
            await self.cog.category_and_roles.general_channel.send(content=f'# <@{target_id}> найден! (+{added_score})\n{answer_str}',
                                       allowed_mentions=discord.AllowedMentions.none())

        async def send_riddle(self, riddle: str):
            announce_role = self.cog.category_and_roles.announce_role
            message = await self.cog.category_and_roles.announcements_channel.send(content=f'{announce_role.mention}\n**Пришла загадка:**\n```{riddle}```')
            await asyncio.sleep(30)
            await message.edit(content=f'**Пришла загадка:**\n```{riddle}```')

        async def update_or_send_rules(self):
            rules_channel = self.cog.category_and_roles.rules_channel
            content = (f'# Правила игры "Тайный Просветленный Алхимик"\n'
                f'## <a:Ozerniki:1254352733260222565> Вступление\n'
                f'- Чтобы участвовать, нажмите "**УЧАСТВОВАТЬ**" и получите роль <@&{data.PLAYER_ROLE_ID}>.\n'
                f'- Покинуть ивент нельзя, но можно отключить уведомления, сняв роль <@&{data.ANNOUNCE_ROLE_ID}>.\n'
                f'## <a:AC_good_memory:1468086866610753649> Просветлённый\n'
                f'- В игре всегда один Просветлённый Алхимик.\n'
                f'- Первый выбирается случайно ботом.\n'
                f'- Следующий Просветлённый — угадавший предыдущего.\n'
                f'## <a:AC_bad_memory:1468085767313031474> Угадывание\n'
                f'- Используйте /guess и выберите игрока чтобы попытаться угадать.\n'
                f'- У Алхимиков только одна попытка угадать за круг. Просветленный угадывать не может.\n'
                f'- При ошибке игрок получает роль <@&{data.FROZEN_ROLE_ID}>.\n'
                f'- Если Просветлённого угадали — угаданный замораживается и все кто ошибся возрождаются.\n'
                f'## <a:AC_change_memory:1468088285242065020> Процесс\n'
                f'- При смене Просветленного у него есть 20 минут чтобы написать загадку — она для того чтобы его отгадали пораньше.\n'
                f'- Загадку можно оценить, и именно по этим оценкам впоследствии людям выдадут некоторые награды.\n'
                f'- Оценивать желательно нужно как по качеству загадки, так и по оригинальности.\n'
                f'- Загадка "Я [имя]" или загадка которая разгадана за пару минут, очевидно плохие.\n'
                f'## <a:AC_gold:1468084362825171031> Награды\n'
                f'- 🏅 **Последний Просветленный** — роль `@Истинный Просветленный` до повторения ивента и подарочная карта Стим.\n'
                f'- 🏅 **Лучшие загадки** — роль `@Пьяный Алхимик` до повторения ивента и подарочная карта Стим.' 
                f'- 🥇 **Первое место** — годовая кастомная роль на свой вкус и подарочная карта Стим.\n' 
                f'- 🥈 **Второе место** — годовая кастомная роль на свой вкус.\n' 
                f'- 🥉 **Третье место** — годовая кастомная роль на свой вкус.\n'
                f'- 🎖 **Роли за хорошие загадки.**\n' 
                f'- 🎖 **А также роли за участие.**\n' 
                f'-# Цена подарочных карт определяется бюджетом администрации и может меняться (в большую сторону).\n')
            try:
                rules_message = await rules_channel.fetch_message(data.RULES_MESSAGE_ID)
                await rules_message.edit(content=content)
            except (discord.NotFound, AttributeError):
                view = self.cog.buttons.RulesView(self.cog)
                rules_message = await rules_channel.send(content=content, view=view)
                data.RULES_MESSAGE_ID = rules_message.id

        async def update_or_send_table(self):
            table_channel = self.cog.category_and_roles.rules_channel
            content = self.cog.table.get_formated_table_str()
            try:
                table_message = await table_channel.fetch_message(data.TABLE_MESSAGE_ID)
                await table_message.edit(content=content)
            except (discord.NotFound, AttributeError):
                table_message = await table_channel.send(content=content)
                data.TABLE_MESSAGE_ID = table_message.id

        async def send_riddle_form(self, user: discord.User):
            view = self.cog.buttons.RiddleView(cog=self.cog)
            msg = await user.send(content=f'# ВЫ СТАЛИ ПРОСВЕТЛЕННЫМ!\n'
                                    f'- **У вас есть 20 минут на написание загадки.**\n'
                                    f'- **Запрещено писать что-то вроде "Я {user.display_name}".**\n'
                                    f'- **Запрещен Голландский штурвал.**\n'
                                    f'- **За нарушение правил вы можете быть временно забанены с ивента.**\n'
                                    f'- **Запрещено делиться скриншотами лички с ботом или раскрывать её любым другим способом.**\n'
                                    f'**Внизу находиться формы для загадки и решения. Заполни их и затем нажми "Подтвердить".**', view=view)
            view.message = msg

        async def send_personal_notification(
                self,
                user: discord.User,
                added_score: float,
                old_table: dict[str, dict[str, float]]
        ):
            player_id = str(user.id)
            service_id = '512079329619083291'

            if player_id == service_id:
                await user.send(f"Вас раскрыли! (+{round(added_score, 1)})")
                return

            old_table = old_table.copy()
            new_table_raw = self.cog.table.table_dict.copy()

            old_table.pop(service_id, None)
            new_table_raw.pop(service_id, None)

            if player_id not in old_table or player_id not in new_table_raw:
                await user.send(f"Вас раскрыли! (+{round(added_score, 1)})")
                return

            # Старый порядок нужен как общий тай-брейк и для old, и для new
            base_order = {uid: i for i, uid in enumerate(old_table.keys())}

            old_table = self.cog.table.get_sorted_table(table=old_table, base_order=base_order)
            new_table = self.cog.table.get_sorted_table(table=new_table_raw, base_order=base_order)

            old_pos = list(old_table).index(player_id)
            new_pos = list(new_table).index(player_id)

            if new_pos >= old_pos:
                await user.send(f"Вас раскрыли! (+{round(added_score, 1)})")
                return

            items = list(new_table.items())
            lines = [f"Вас раскрыли! (+{round(added_score, 1)})"]

            start = new_pos
            end = min(len(items), old_pos + 2)

            for i in range(start, end):
                plr_id, _data = items[i]

                if plr_id == player_id:
                    lines.append(f"{i + 1}. <@{player_id}> — {round(_data['score'], 1)} (вы обошли {i} игрока/ов)")
                else:
                    lines.append(f"{i + 1}. ↑ <@{plr_id}> — {round(_data['score'], 1)}")

            await user.send("\n".join(lines))

    class Table:
        def __init__(self, cog: commands.Cog, json_path: str):
            self.cog: ColdOldMan = cog
            self._json_path = json_path
            self.table_dict: dict = None
            self._load_table()

        def _load_table(self) -> dict:
            try:
                with open(self._json_path, "r", encoding="utf-8") as f:
                    self.table_dict = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                self.table_dict = {}
                with open(self._json_path, "w", encoding="utf-8") as f:
                    json.dump(self.table_dict, f, ensure_ascii=False, indent=4)

        def _commit_table(self):
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(self.table_dict, f, ensure_ascii=False, indent=4)

        def add_player(self, user_id: int):
            str_user_id = str(user_id)
            if str_user_id in self.table_dict.keys():
                return False
            self.table_dict[str_user_id] = {
                'score': 0,
                'frozen': False,
                'ban': False
            }
            self._commit_table()
            return True

        def clear_table(self):
            self.table_dict = {}
            self._commit_table()

        def unfroze_all(self):
            for player_data in self.table_dict.values():
                if player_data.get("frozen"):
                    player_data["frozen"] = False
            self._commit_table()

        def froze_player(self, user_id: int):
            try:
                str_user_id = str(user_id)
                if str_user_id not in self.table_dict.keys():
                    return
                self.table_dict[str_user_id]['frozen'] = True
                self._commit_table()
            except Exception as e:
                print('Exception froze_player:', e)

        def unfroze_player(self, user_id: int):
            str_user_id = str(user_id)
            if str_user_id not in self.table_dict.keys():
                return
            self.table_dict[str_user_id]['frozen'] = False
            self._commit_table()

        def ban_player(self, user_id: int):
            str_user_id = str(user_id)
            if str_user_id not in self.table_dict.keys():
                return
            self.table_dict[str_user_id]['ban'] = True
            self._commit_table()

        def unban_player(self, user_id: int):
            str_user_id = str(user_id)
            if str_user_id not in self.table_dict.keys():
                return
            self.table_dict[str_user_id]['ban'] = False
            self._commit_table()

        def _get_top_score(self) -> float:
            table = self.get_sorted_table()

            if table.get('512079329619083291'):
                del table['512079329619083291']

            _data = next(iter(table.values()))
            return _data['score']

        def add_score(self, user_id: int) -> tuple[float, dict | None]:
            str_user_id = str(user_id)
            if str_user_id not in self.table_dict:
                return None

            if str_user_id == '512079329619083291':
                self.table_dict[str_user_id]['score'] += 1
                self._commit_table()
                return 1.0, None

            user_score = self.table_dict[str_user_id]['score']
            top_score = self._get_top_score()
            score_diff = top_score - user_score

            added_score = round(1 + score_diff * 0.2, 1)
            new_score = round(user_score + added_score, 1)

            old_table = deepcopy(self.table_dict)
            old_table.pop('512079329619083291', None)
            self.table_dict[str_user_id]['score'] = new_score

            self._commit_table()

            return added_score, old_table

        def get_player_data(self, user_id: int) -> dict:
            str_user_id = str(user_id)
            if str_user_id not in self.table_dict.keys():
                return None
            return self.table_dict[str_user_id]

        def get_random_player_id(self):
            return random.choice(tuple(self.table_dict))

        def get_sorted_table(self, table: dict | None = None, base_order: dict[str, int] | None = None) -> dict[str, dict]:
            if table is None:
                table = self.table_dict

            if base_order is None:
                base_order = {uid: i for i, uid in enumerate(table.keys())}

            return dict(
                sorted(
                    table.items(),
                    key=lambda item: (-item[1]['score'], base_order.get(item[0], 10 ** 9))
                )
            )

        def get_formated_table_str(self) -> str:
            sorted_table = self.get_sorted_table()
            frozen_role = self.cog.category_and_roles.frozen_role

            lines = ['# Таблица очков']
            for i, (player_id, player_data) in enumerate(sorted_table.items(), start=1):
                frozen_str = ''
                q = ''
                if player_data['frozen']:
                    q = '~~'
                    frozen_str = frozen_role.mention
                if player_id == '512079329619083291':
                    lines.insert(0, f'ㅤ   {q}<@{player_id}>{q} — {player_data['score']} {frozen_str}')
                    continue
                lines.append(f'{i}. {q}<@{player_id}>{q} — {player_data['score']} {frozen_str}')
            return "\n".join(lines)

    async def get_player(self, user_id: int):
        player_data = self.table.get_player_data(user_id)
        if player_data is None:
            return None
        member = await self.bot.fetch_user(user_id)
        player = self.Player(user=member, cog=self)
        return player

    class Player:
        def __init__(self, user: discord.Member | discord.User, cog: commands.Cog):
            self.cog: ColdOldMan = cog
            self.user = user

            player_data = self.cog.table.get_player_data(user.id)
            self.score: float = player_data['score']
            self.frozen: bool = player_data['frozen']
            self.banned: bool = player_data['ban']

        def _update_score(self):
            player_data = self.cog.table.get_player_data(user.id)
            self.score = player_data['score']

        def add_win_score(self) -> tuple[float, dict]:
            return self.cog.table.add_score(user_id=self.user.id)

        def ban(self):
            self.cog.table.ban_player(self.user.id)

        def unban(self):
            self.cog.table.unban_player(self.user.id)

        def ded(self):
            if data.PLAYER_MESS_ID == self.user.id:
                return True
            return False

        async def freeze(self):
            member = await self.cog.guild.fetch_member(self.user.id)
            try:
                self.cog.table.froze_player(self.user.id)
                await self.cog.category_and_roles.fetch_roles()
                frozen_role = self.cog.category_and_roles.frozen_role
                player_role = self.cog.category_and_roles.player_role
                await member.add_roles(frozen_role, reason="Freeze")
                await member.remove_roles(player_role, reason="Freeze")
                await asyncio.sleep(0.2)
            except Exception as e:
                await self.cog.send_log(func_name='freeze', text=e, user=member)
                await asyncio.sleep(1.0)

        async def unfreeze(self):
            self.cog.table.unfroze_player(self.user.id)
            member = await self.cog.guild.fetch_member(self.user.id)
            await member.add_roles(self.cog.category_and_roles.player_role)
            await member.remove_roles(self.cog.category_and_roles.frozen_role)

        async def synchronize_roles(self):
            player_data = self.cog.table.get_player_data(self.user.id)
            member = await self.cog.guild.fetch_member(self.user.id)
            if player_data is not None:
                if not player_data.get('frozen'):
                    await member.add_roles(self.cog.category_and_roles.player_role, reason='Роли синхронизированы.')
                    await member.remove_roles(self.cog.category_and_roles.frozen_role, reason='Роли синхронизированы.')
                else:
                    await member.add_roles(self.cog.category_and_roles.frozen_role, reason='Роли синхронизированы.')
                    await member.remove_roles(self.cog.category_and_roles.player_role, reason='Роли синхронизированы.')
            else:
                return

        async def add_announce_role(self):
            member = await self.cog.guild.fetch_member(self.user.id)
            await member.add_roles(self.cog.category_and_roles.announce_role)

        async def remove_announce_role(self):
            member = await self.cog.guild.fetch_member(self.user.id)
            await member.remove_roles(self.cog.category_and_roles.announce_role)

        def make_dedom(self):
            data.PLAYER_MESS_ID = self.user.id

    class Game:
        def __init__(self, cog: commands.Cog):
            self.cog: ColdOldMan = cog
            try:
                self.game_active = data.GAME_ACTIVE
            except AttributeError:
                self.game_active = data.GAME_ACTIVE = False

        def start(self):
            self.game_active = True
            data.GAME_ACTIVE = True

        def stop(self):
            self.game_active = False
            data.GAME_ACTIVE = False

        async def unfroze_all_players(self):
            self.cog.table.unfroze_all()
            await self.cog.category_and_roles.fetch_roles()
            frozen_role = self.cog.category_and_roles.frozen_role
            player_role = self.cog.category_and_roles.player_role

            members = list(frozen_role.members)

            for member in members:
                try:
                    await member.remove_roles(frozen_role, reason="Unfreeze all players")
                    await member.add_roles(player_role, reason="Unfreeze all players")
                except discord.Forbidden as e:
                    print(f"[403] Нет прав/иерархия для {member}: {e}")
                    await self.cog.send_log(func_name='unfroze_all_players', text=e)
                except discord.HTTPException as e:
                    print(f"[{e.status}] Ошибка API для {member}: {e}")
                    await self.cog.send_log(func_name='unfroze_all_players', text=e)
                    if e.status == 429:
                        await asyncio.sleep(2.0)
                except Exception as e:
                    await self.cog.send_log(func_name='unfroze_all_players', text=e)
                    await asyncio.sleep(1.0)
                await asyncio.sleep(0.2)

    class Utility:
        def __init__(self, cog: commands.Cog):
            self.cog: ColdOldMan = cog
            self._signs = [
                "почти поднялся к просветлению — но пал обратно в Озеро!",
                "стремился к истине, но пал и вернулся в Озеро!",
                "шагнул к просветлению, но оступился и пал в Озеро!",
                "приблизился к свету, однако падение вернуло его в Озеро!",
                "тянулся к вершине — но пал и очутился в Озере!",
                "боролся за просветление, но проиграл и вернулся в Озеро!",
                "хотел разорвать круг, но лишь Озеро приняло его!",
                "искал тропу к свету, однако сорвался в Озеро!",
                "был близок к истине — но снова канул в Озеро!",
                "искал спасения, но судьба вернула его в Озеро!",
                "шагал к просветлению, пока не рухнул обратно в Озеро!",
                "увидел свет вдали, но упал и вернулся в Озеро!",
                "поднял взор к небесам — и оказался в Озере!",
                "приблизился к просветлению, однако лишь пал в Озеро!",
                "искал выход, но дорога вновь его привела к Озеру!",
                "коснулся света — и снова погрузился в Озеро!",
                "пытался вырваться, но оказался обратно в Озере!",
                "поднялся над тьмой, однако снова пал в Озеро!",
                "почти разорвал цепи, но вернулся в Озеро!",
                "был на грани просветления — и снова очутился в Озере!",
                "искал покой, но нашёл лишь возвращение в Озеро!",
                "почти увидел истину, но не удержался и пал в Озеро!",
                "сделал рывок к свету, однако снова оказался в Озере!",
                "шёл к освобождению, но вернулся в Озеро!",
                "на мгновение достиг просветления — лишь чтобы снова пасть в Озеро!",
                "почти вышел из круга, но Озеро приняло его вновь!",
                "приблизился к вершине, но сорвался в Озеро!",
                "пытался покинуть глубины, однако снова вернулся в Озеро!",
                "нашёл луч света, но снова утонул в Озере!",
                "стремился к высоте духа, но пал обратно в Озеро!",
                "был близок к спасению, однако вновь оказался в Озере!",
                "шагнул к истине, но снова погрузился в Озеро!",
                "почти достиг света — и вернулся в Озеро!",
                "почти выбрался из тьмы, однако снова оказался в Озере!",
                "искал путь к свету, но вновь утонул в Озере!",
                "почти освободился, но вновь утонул в Озере!",
                "жаждал истины, но снова стал пленником Озера!",
                "искал высшую мудрость, но путь его вновь привёл к Озеру!",
            ]
            self._signs_index = 0

        def get_death_sign(self):
            sign = self._signs[self._signs_index]
            self._signs_index = (self._signs_index + 1) % len(self._signs)
            return sign

    async def guess_main_task(self, interaction: Interaction, player_id: int, target_id: int):
        if not self.game.game_active:
            await interaction.response.send_message(f'Игра не активна или вы не успели.', ephemeral=True)
            return
        player = await self.get_player(player_id)
        if player.ded():
            await interaction.response.defer(ephemeral=True, thinking=False)
            await interaction.delete_original_response()
            return
        if player.banned:
            await interaction.response.send_message('Вы забанены с игры.', ephemeral=True)
            return
        if player.frozen:
            await interaction.response.send_message(f'{self.category_and_roles.frozen_role.mention} не может угадывать.', ephemeral=True)
            await player.synchronize_roles()
            return
        if player is None:
            await interaction.response.send_message('Вы не зарегистрированы как игрок.', ephemeral=True)
            return
        target = await self.get_player(target_id)
        if target is None:
            await interaction.response.send_message('Цель не зарегистрирована как игрок.', ephemeral=True)
            return
        if target.frozen:
            await interaction.response.send_message(f'Это {self.category_and_roles.frozen_role.mention}.', ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=False)

        if not target.ded():
            if not self.game.game_active:
                await interaction.followup.send(f'Игра не активна или вы не успели.', ephemeral=True)
                return
            await interaction.delete_original_response()
            await self.messages.send_death_sign(user_id=player_id)
            await player.freeze()
            await self.messages.update_or_send_table()
        else:
            if not self.game.game_active:
                await interaction.followup.send(f'Игра не активна или вы не успели.', ephemeral=True)
                return
            await interaction.delete_original_response()
            self.game.stop()
            asyncio.create_task(self.game.unfroze_all_players())
            added_score, old_table = target.add_win_score()
            player.make_dedom()
            await target.freeze()
            asyncio.create_task(self.messages.send_personal_notification(user=target.user, added_score=added_score, old_table=old_table))
            asyncio.create_task(self.messages.send_congratulation(target_id=target_id, added_score=added_score))
            asyncio.create_task(self.messages.send_riddle_form(user=interaction.user))
            asyncio.create_task(self.messages.update_or_send_table())
            await asyncio.sleep(10)
            data.ANS_RIDDLE = None
            self.game.start()
    @app_commands.command(name='guess', description='Угадать цель.')
    @app_commands.describe(target='Выберите игрока.')
    @app_commands.guilds(config.GUILD_ID)
    async def guess(self, interaction: Interaction, target: discord.User):
        try:
            target_id = target.id
            player_id = interaction.user.id
            asyncio.create_task(self.guess_main_task(interaction=interaction, player_id=player_id, target_id=target_id))
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='alchemist_create_event', description='Подготовить категорию ивента Пьяного Алхимика.')
    @app_commands.guilds(config.GUILD_ID)
    async def alchemist_create_event(self, interaction: Interaction):
        try:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "Только для Администраторов.",
                    ephemeral=True
                )
                return

            if not interaction.guild.me.guild_permissions.administrator:
                await interaction.response.send_message(
                    "У бота нет прав Администратора. \n"
                    "-# Мне (Габу) было лень делать конкретный список необходимых прав для ивента.",
                    ephemeral=True
                )
                return

            view = self.buttons.ConfirmOrCancelView(interaction.user)

            await interaction.response.send_message(
                f'##  Подготовить категорию ивента Пьяного Алхимика?\n'
                f'Будет создана категория с пятью каналами:\n'
                f'- **Пьяный Алхимик**\n'
                f'- **┣ 📜🎩-правила**\n'
                f'- **┣ 🚨📢-объявления**\n'
                f'- **┣ 🧪📖-обсуждение**\n'
                f'- **┗ log**\n'
                f'Также будут созданы 3 ивентные роли: `@Алхимик`, `@Испорченная Душа`, `@Искатель`.\n'
                f'-# Название категории, каналов и ролей можно изменить. **Категория и каналы после создания будут скрыты. Открыть их нужно вручную.**\n',
                view=view,
            )

            await view.wait()
            if not view.value:
                canceled_view = self.buttons.CanceledView()
                await interaction.edit_original_response(view=canceled_view)
                return
            else:
                confirmed_view = self.buttons.ConfirmedView()
                await interaction.edit_original_response(view=confirmed_view)
                await self.category_and_roles.create_category_and_roles()
                await self.messages.update_or_send_rules()
                await self.messages.update_or_send_table()
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='alchemist_recreate_roles', description='Обновить роли ивента Пьяного Алхимика.')
    @app_commands.guilds(config.GUILD_ID)
    async def alchemist_recreate_roles(self, interaction: Interaction):
        try:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "Только для Администраторов.",
                    ephemeral=True
                )
                return

            guild = interaction.guild
            me = guild.me

            if not me.guild_permissions.administrator:
                await interaction.response.send_message(
                    "У бота нет прав Администратора. \n"
                    "-# Мне (Габу) было лень делать конкретный список необходимых прав для ивента.",
                    ephemeral=True
                )
                return

            view = self.buttons.ConfirmOrCancelView(interaction.user)
            await interaction.response.send_message(
                f'Воссоздать удаленные Роли ивента Пьяного алхимика? Если старые роли существуют, то они не будут удалены.',
                view=view,
            )

            await view.wait()
            if not view.value:
                cancel_view = self.buttons.CanceledView()
                await interaction.edit_original_response(view=cancel_view)
                return
            else:
                confirm_view = self.buttons.ConfirmedView()
                await interaction.edit_original_response(view=confirm_view)
                await self.category_and_roles.recreate_roles()
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            print(f"Error in {func_name}: {e}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='alchemist_ban', description='Забанить игрока.')
    @app_commands.describe(user='Выберите игрока.')
    @app_commands.guilds(config.GUILD_ID)
    async def alchemist_ban(self, interaction: Interaction, user: discord.User):
        try:
            if interaction.user.id != 512079329619083291:
                await interaction.response.send_message('Нет полномочий.', ephemeral=True)
                return
            player = await self.get_player(user.id)
            player.ban()
            await interaction.response.send_message('Игрок заблокирован с ивента.', ephemeral=True)
            return
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='alchemist_unban', description='Разбанить игрока.')
    @app_commands.describe(member='Выберите игрока.')
    @app_commands.guilds(config.GUILD_ID)
    async def alchemist_unban(self, interaction: Interaction, member: discord.Member):
        try:
            if interaction.user.id != 512079329619083291:
                await interaction.response.send_message('Нет полномочий.', ephemeral=True)
                return
            player = await self.get_player(member.id)
            player.unban()
            await interaction.response.send_message('Игрок разблокирован с ивента.', ephemeral=True)
            return
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='alchemist_data', description='Данный ивента Пьяного Алхимика.')
    @app_commands.guilds(config.GUILD_ID)
    async def alchemist_data(self, interaction: Interaction):
        try:
            await self.category_and_roles.update_category()
            await self.category_and_roles.fetch_roles()
            content = (
                f'# Состояния:\n'
                f'- Игра активна — {data.GAME_ACTIVE}\n'
                f'# Каналы\n'
                f'- {self.category_and_roles.category.mention}\n'
                f'- ┣ {self.category_and_roles.rules_channel.mention}\n'
                f'- ┣ {self.category_and_roles.announcements_channel.mention}\n'
                f'- ┣ {self.category_and_roles.general_channel.mention}\n'
                f'- ┗ {self.category_and_roles.log_channel.mention}\n'
                f'# Роли\n'
                f'- Роль игрока — {self.category_and_roles.player_role.mention}\n'
                f'- Роль замороженного — {self.category_and_roles.frozen_role.mention}\n'
                f'- Роль уведомления — {self.category_and_roles.announce_role.mention}\n'
            )

            await interaction.response.send_message(content, ephemeral=True)
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            print(f"Error in {func_name}: {e}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='alchemist_update_rules', description='Переделать сообщение правил.')
    @app_commands.guilds(config.GUILD_ID)
    async def alchemist_update_rules(self, interaction: Interaction):
        try:
            if interaction.user.id != 512079329619083291:
                await interaction.response.send_message('Нет полномочий.', ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=False)
            await self.messages.update_or_send_rules()
            await interaction.followup.send('Правила обновлены.', ephemeral=True)
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            print(f"Error in {func_name}: {e}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='alchemist_start', description='Начать ивент.')
    @app_commands.guilds(config.GUILD_ID)
    async def alchemist_start(self, interaction: Interaction):
        try:
            if interaction.user.id != 512079329619083291:
                await interaction.response.send_message('Нет полномочий.', ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            self.game.start()

            try:
                _ = data.PLAYER_MESS_ID
            except AttributeError:
                data.PLAYER_MESS_ID = 0

            if not data.PLAYER_MESS_ID:
                player_id = self.table.get_random_player_id()
                player = await self.get_player(int(player_id))
                player.make_dedom()
                await player.user.send(content='# ТЫ СТАЛ ПЕРВЫМ ПРОСВЕТЛЕННЫМ!')
                asyncio.create_task(self.messages.send_riddle_form(user=player.user))
            await interaction.followup.send('Игра запущена.', ephemeral=True)
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

async def setup(bot):
    # await bot.add_cog(ColdOldMan(bot))
    pass
