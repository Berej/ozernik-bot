import html
import inspect
import os
import random
import re
import sqlite3
import asyncio
import pylottie
from concurrent.futures import ProcessPoolExecutor
from collections import defaultdict
from contextlib import suppress
from pathlib import Path
from typing import Iterable

import discord
import telegram
from discord import (
    Interaction,
    InvalidData, # noqa
    SelectOption,
    Webhook, # noqa
    app_commands
)
from discord.ext import commands
from discord.ui import Select, View
from telegram import (
    Bot,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler, # noqa
    filters,
)

from config import (
    TELEGRAM_TOKEN_MAIN,
    TELEGRAM_TOKEN_REPEATER_1,
    TELEGRAM_TOKEN_REPEATER_2,
    config,
)
from utilities import BridgeDatabase, DataTypes, DataWorker

data_setup = {
    'log_channel_id': 0
}

data = DataWorker('modules/bridge/data.json', setup=data_setup)

DEFAULT_AVATARS = [
    "https://cdn.discordapp.com/attachments/1108123699309195365/1445181563368378449/Red.png",
    "https://cdn.discordapp.com/attachments/1108123699309195365/1445181563733151835/White.png",
    "https://cdn.discordapp.com/attachments/1108123699309195365/1445181564085600512/Black.png",
    "https://cdn.discordapp.com/attachments/1108123699309195365/1445181564542783690/Blue.png",
    "https://cdn.discordapp.com/attachments/1108123699309195365/1445181564941111296/Brown.png",
    "https://cdn.discordapp.com/attachments/1108123699309195365/1445181565406543984/Cyan.png",
    "https://cdn.discordapp.com/attachments/1108123699309195365/1445181565796745277/Gray.png",
    "https://cdn.discordapp.com/attachments/1108123699309195365/1445181566233088060/Orange.png",
]

def random_avatar(user_id: int) -> str:
    r = random.Random(user_id)
    return r.choice(DEFAULT_AVATARS)

def markdown_to_html_custom(text: str) -> str:
    if not text:
        return ""

    # --- Временно сохраняем допустимые HTML-теги ---
    allowed_tags = [
        "u", "s", "tg-spoiler", "blockquote"
    ]

    placeholders = {}
    for tag in allowed_tags:
        pattern = rf"<{tag}>(.*?)</{tag}>"
        for i, match in enumerate(re.findall(pattern, text, flags=re.S)):
            key = f"__{tag.upper()}_{len(placeholders)}__"
            placeholders[key] = f"<{tag}>{match}</{tag}>"
            text = text.replace(f"<{tag}>{match}</{tag}>", key, 1)

    # --- Экранируем остальной HTML ---
    text = html.escape(text)

    lines = text.splitlines()
    result = []

    for line in lines:
        if line.startswith(("### ", "## ", "# ")):
            content = line.lstrip("# ").strip()
            result.append(f"<b>{content}</b>")

        elif line.startswith("-# "):
            content = line[3:].strip()
            result.append(f"<i>{content}</i>")

        else:
            result.append(line)

    text = "\n".join(result)

    # --- Markdown → HTML ---
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)        # **bold**
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)            # *italic*
    text = re.sub(r"__(.+?)__", r"<u>\1</u>", text)            # __underline__
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)            # ~~strike~~
    text = re.sub(r"\|\|(.+?)\|\|", r"<tg-spoiler>\1</tg-spoiler>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = text.replace('\n- ', '\n• ')

    # --- Восстанавливаем разрешённые HTML-теги ---
    for key, value in placeholders.items():
        text = text.replace(key, value)

    return text

def get_private_topic_mention(chat_id: int, topic_id: int) -> str:
    chat_id_str = str(chat_id)

    if chat_id_str.startswith("-100"):
        internal_id = chat_id_str[4:]
    else:
        internal_id = chat_id_str.lstrip("-")

    return f"https://t.me/c/{internal_id}/{topic_id}"

async def safe_remove(path: str):
    for _ in range(10):
        try:
            if os.path.exists(path):
                os.remove(path)
            return
        except PermissionError:
            await asyncio.sleep(0.2)

    print("Не удалось удалить файл:", path)

file_index = 0
file_lock = asyncio.Lock()

async def download_telegram_file(file: telegram.File, format_: str) -> str:
    try:
        global file_index
        unique_index = f'{file.file_unique_id}{file_index}'
        file_index += 1

        base_dir = os.path.dirname(os.path.abspath(__file__))
        temp_dir = os.path.join(base_dir, "temp")
        os.makedirs(temp_dir, exist_ok=True)

        path = os.path.join(temp_dir, f'{unique_index}.{format_}')
        await file.download_to_drive(path)

        if format_ == "tgs":
            tgs_path = path
            webp_path = os.path.join(temp_dir, f'{unique_index}.webp')

            async with file_lock:
                loop = asyncio.get_running_loop()
                with ProcessPoolExecutor(max_workers=1) as executor:
                    await loop.run_in_executor(
                        executor,
                        pylottie.convertMultLottie2Webp,
                        [tgs_path],
                        [webp_path]
                )

            await safe_remove(tgs_path)

            path = webp_path
            if os.path.exists(path):
                return path
            else:
                print("Файл не существует.")
                return None
        return path
    except Exception as e:
        print('download telegram file failed:', e)

class ViewSelectForum(View):
    def __init__(self, cog: commands.Cog, user: discord.User, channel: discord.TextChannel):
        super().__init__(timeout=180)
        self.add_item(self.SelectForum(cog=cog, user=user, channel=channel))

    class SelectForum(Select):
        def __init__(self, cog: commands.Cog, user: discord.User, channel: discord.TextChannel):
            super().__init__(placeholder='Выбрать telegram форум:')
            self.cog = cog
            self.user = user
            self.channel = channel

            for forum in cog.telegram.available_forums:
                self.options.append(SelectOption(label=forum.title, value=forum.id))

        async def callback(self, select_interaction: discord.Interaction):
            try:
                forum_id = int(self.values[0])

                success, mess = await self.cog.telegram.check_bots_exist(forum_id)

                if not success:
                    await select_interaction.channel.send(f'```{mess}```')
                    return

                topic: telegram.ForumTopic = await self.cog.telegram.app_main._bot.create_forum_topic(
                    chat_id=forum_id,
                    name=self.channel.name
                )

                t = 'за'

                try:
                    self.cog.db.add_chat_link(discord_chanel_id=self.channel.id, telegram_chat_id=forum_id,
                                              telegram_topic_id=topic.message_thread_id)
                except sqlite3.IntegrityError:
                    t = 'пере'
                    chat_link = self.cog.db.get_chat_link(chat_id=self.channel.id, chat_type='discord')
                    self.cog.db.update_chat_link(link_id=chat_link.id, telegram_topic_id=topic.message_thread_id, telegram_chat_id=forum_id)

                topic_mention = get_private_topic_mention(forum_id, topic.message_thread_id)
                await select_interaction.channel.send(f'Мост в {self.channel.mention} {t}регистрирован.')

                await select_interaction.message.delete(delay=0)

            except Exception as _e:
                await select_interaction.channel.send(f'```{_e}```')

        async def interaction_check(self, check_interaction: discord.Interaction) -> bool:
            if check_interaction.user != self.user:
                await check_interaction.response.send_message('Не вы использовали команду.', ephemeral=True)
                return False
            return True

class ViewSelectGroup(View):
    def __init__(self, cog: commands.Cog, user: discord.User, channel: discord.TextChannel):
        super().__init__(timeout=180)
        self.add_item(self.SelectGroup(cog=cog, user=user, channel=channel))

    class SelectGroup(Select):
        def __init__(self, cog: commands.Cog, user: discord.User, channel: discord.TextChannel):
            super().__init__(placeholder='Выбрать telegram группу:')
            self.cog = cog
            self.user = user
            self.channel = channel

            for group in cog.telegram.available_groups:
                self.options.append(SelectOption(label=group.title, value=group.id))

        async def callback(self, select_interaction: discord.Interaction):
            try:
                group_id = int(self.values[0])

                success, mess = await self.cog.telegram.check_bots_exist(group_id, False)

                if not success:
                    await select_interaction.channel.send(f'```{mess}```')
                    return

                t = 'за'

                try:
                    self.cog.db.add_chat_link(discord_chanel_id=self.channel.id,
                                              telegram_chat_id=group_id)
                except sqlite3.IntegrityError:
                    t = 'пере'
                    chat_link = self.cog.db.get_chat_link(chat_id=self.channel.id, chat_type='discord')
                    self.cog.db.update_chat_link(link_id=chat_link.id, telegram_chat_id=group_id, telegram_topic_id=-1)

                await select_interaction.channel.send(f'Мост в {self.channel.mention} {t}регистрирован.')

                await select_interaction.message.delete(delay=0)

            except Exception as _e:
                await select_interaction.channel.send(f'```{_e}```')

        async def interaction_check(self, check_interaction: discord.Interaction) -> bool:
            if check_interaction.user != self.user:
                await check_interaction.response.send_message('Не вы использовали команду.', ephemeral=True)
                return False
            return True

class ViewSelectChannel(View):
    def __init__(self, cog: commands.Cog, user: discord.User, channel: discord.TextChannel):
        super().__init__(timeout=180)
        self.add_item(self.SelectChannel(cog=cog, user=user, channel=channel))

    class SelectChannel(Select):
        def __init__(self, cog: commands.Cog, user: discord.User, channel: discord.TextChannel):
            super().__init__(placeholder='Выбрать telegram группу:')
            self.cog = cog
            self.user = user
            self.channel = channel

            for tg_channel in cog.telegram.available_channels:
                self.options.append(SelectOption(label=tg_channel.title, value=tg_channel.id))

        async def callback(self, select_interaction: discord.Interaction):
            try:
                tg = self.cog.telegram
                tg_bot = tg.app_main._bot
                tg_channel_id = int(self.values[0])

                success, mess = await tg.check_bots_exist(tg_channel_id, False)

                if not success:
                    await select_interaction.channel.send(f'```{mess}```')
                    return

                success, mess = await tg.check_announce_linked_chat_bots_exist(tg_channel_id)

                if not success:
                    await select_interaction.channel.send(f'```{mess}```')
                    return

                t = 'за'

                try:
                    self.cog.db.add_chat_link(discord_chanel_id=self.channel.id,
                                              telegram_chat_id=tg_channel_id)
                except sqlite3.IntegrityError:
                    t = 'пере'
                    chat_link = self.cog.db.get_chat_link(chat_id=self.channel.id, chat_type='discord')
                    self.cog.db.update_chat_link(link_id=chat_link.id, telegram_chat_id=tg_channel_id, telegram_topic_id=-1)

                await select_interaction.channel.send(f'Мост в {self.channel.mention} {t}регистрирован.')

                await select_interaction.message.delete(delay=0)

            except Exception as _e:
                await select_interaction.channel.send(f'```{_e}```')

        async def interaction_check(self, check_interaction: discord.Interaction) -> bool:
            if check_interaction.user != self.user:
                await check_interaction.response.send_message('Не вы использовали команду.', ephemeral=True)
                return False
            return True

class Bridge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = BridgeDatabase()
        self.telegram = Telegram(self)

    async def cog_load(self):
        try:
            # Запуск Телеграм ботов
            await self.telegram.start()
        except Exception as e:
            print(e)

    async def cog_unload(self):
        if self.telegram.app_main and self.telegram.app_main.updater.running:
            await self.telegram.app_main.updater.stop()

        await self.telegram.app_main.stop()
        await self.telegram.app_main.shutdown()

        if self.telegram.app_repeater1 and self.telegram.app_repeater1.updater.running:
            await self.telegram.app_repeater1.updater.stop()

        await self.telegram.app_repeater1.stop()
        await self.telegram.app_repeater1.shutdown()

        if self.telegram.app_repeater2 and self.telegram.app_repeater2.updater.running:
            await self.telegram.app_repeater2.updater.stop()

        await self.telegram.app_repeater2.stop()
        await self.telegram.app_repeater2.shutdown()

    async def send_log(self, text: str, error: bool = True, func_name: str | None = None, user: discord.User | None = None):
        name_str = ''
        if func_name:
            func_name = f' в {func_name}'
        if user:
            name_str = f' у {user.name}'
        if error:
            content = f'Произошла ошибка{func_name}{name_str}:\n```\n{text}\n```'
        else:
            content = f'Лог{func_name}{name_str}:\n```\n{text}\n```'
        await self.category_and_roles.log_channel.send(content)

    @app_commands.command(name='bridge_set_log_channel', description='Установить каналов логов моста.')
    @app_commands.describe(channel='Выберите канал.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    async def bridge_set_log_channel(self, interaction: Interaction, channel: discord.TextChannel):
        try:
            data.log_channel_id = channel.id
            await interaction.response.send_message('Канал установлен', ephemeral=True)
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            print(f"Error in {func_name}: {e}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='bridge_create_thread',
                          description='Создать зеркало канала в определенном telegram форуме.')
    @app_commands.describe(channel='Выберите канал.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    async def bridge_create_thread(self, interaction: Interaction, channel: discord.TextChannel):
        try:
            if not self.telegram.available_forums:
                await interaction.response.send_message(f'В оперативной памяти нет доступных telegram форумов. Добавьте `@{self.telegram.app_main.bot.username}` в чат форум и отправьте там любое сообщение.', ephemeral=True)
                return

            view = ViewSelectForum(cog=self, user=interaction.user, channel=channel)
            await interaction.response.send_message(f'Выберите форум в который будет вести канал {channel.mention}. В этом форуме автоматически создастся связанный тред.', view=view)
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            print(f"Error in {func_name}: {e}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='bridge_create_strong_link',
                          description='Создать зеркало канала в определенном telegram чате.')
    @app_commands.describe(channel='Выберите канал.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    async def bridge_create_strong_link(self, interaction: Interaction, channel: discord.TextChannel):
        try:
            if not self.telegram.available_groups:
                await interaction.response.send_message(
                    f'В оперативной памяти нет доступных telegram групп. Добавьте `@{self.telegram.app_main.bot.username}` в группу и отправьте там любое сообщение.',
                    ephemeral=True)
                return

            view = ViewSelectGroup(cog=self, user=interaction.user, channel=channel)
            await interaction.response.send_message(
                f'Выберите группу в которую будет вести канал {channel.mention}. Если мост существует, то он перезапишется.',
                view=view)
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            print(f"Error in {func_name}: {e}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='bridge_create_announcements_link',
                          description='Создать зеркало канала в определенном telegram канале/блоге.')
    @app_commands.describe(channel='Выберите канал.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    async def bridge_create_announcements_link(self, interaction: Interaction, channel: discord.TextChannel):
        try:
            if not self.telegram.available_channels:
                await interaction.response.send_message(
                    f'В оперативной памяти нет доступных telegram каналов/блогов. Добавьте `@{self.telegram.app_main.bot.username}` в группу и отправьте там любое сообщение.',
                    ephemeral=True)
                return

            view = ViewSelectChannel(cog=self, user=interaction.user, channel=channel)
            await interaction.response.send_message(
                f'Выберите канал/блог в который будет вести канал {channel.mention}. Если мост существует, то он перезапишется.',
                view=view)
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            print(f"Error in {func_name}: {e}")
            await self.send_log(user=interaction.user, text=e, func_name=func_name)
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    async def send_warning_about_announcements(self, message: discord.Message):
        try:
            await message.author.send(
                content=f'Чтобы сообщение отправилось через мост, необходимо использовать функцию каналов с объявлениями "Опубликовать". '
                        f'Настоятельно рекомендую не дробить сообщения на несколько, а отправлять все за один раз (или изменять существующее сообщение).'
                        f'После публикации автоматически создастся ветка.'
            )
        except Exception as e:
            print('send_warning_about_announcements error:', e)
            await self.send_log(
                text=e,
                error=True,
                func_name='send_warning_about_announcements',
                user=message.author,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            if message.webhook_id:
                return

            if message.content == '!reload':
                print('Reloading...')
                await self.bot.reload_extension('modules.bridge.bridge')
                return

            channel_id = message.channel.id
            chat_link = self.db.get_chat_link(channel_id, 'discord')
            if not chat_link:
                print('Не мост')
                return

            try:
                tg_chat = await self.telegram.app_main.bot.get_chat(chat_link.telegram_chat_id)
                print(tg_chat.type)

            except telegram.error.BadRequest as e:
                print("Чат не найден или неверный telegram_chat_id:", e)
                await self.send_log(
                    text='Чат не найден или неверный telegram_chat_id',
                    error=True,
                    func_name='on_message',
                    user=message.author,
                )
                return

            except telegram.error.Forbidden as e:
                print("Бот не имеет доступа к чату:", e)
                await self.send_log(
                    text='Бот не имеет доступа к чату',
                    error=True,
                    func_name='on_message',
                    user=message.author,
                )
                return

            if tg_chat.type == 'channel':
                pass

            if message.author == self.bot.user:
                print('main_bot_message')
                await self.telegram.send_main_bot_message(chat_link, message)
                return

            ozernik = self.db.get_user(message.author.id, 'discord_id')

            if ozernik is None:
                ozernik = self.db.add_discord_user(message.author.id)

            if not ozernik.telegram_id:
                print('unregistered_in_telegram_user_message')
                await self.telegram.send_repeater_message(chat_link, message)
                return

            if ozernik.telegram_bot_id:
                print('with_bot_user_message')
                await self.on_user_with_bot_message(message, chat_link)
                return

            if ozernik.telegram_id:
                print('user_message')
                await self.on_user_message(message, chat_link)
                return
        except Exception as e:
            print(f"on_message error: {e}")

    @staticmethod
    async def get_webhook(channel: discord.TextChannel):
        try:
            webhooks = await channel.webhooks()
            webhook = next((wh for wh in webhooks if wh.name == "Лилия, пересылающая сообщения"), None)
            if webhook is None:
                webhook = await channel.create_webhook(name="Лилия, пересылающая сообщения")
            return webhook
        except Exception as e:
            print(f"get_webhook error: {e}")

    async def get_avatar(self, telegram_user_id: int) -> str:
        try:
            ozernik = self.db.get_user(telegram_user_id, 'telegram_id')
            avatar_url = None
            bot = self.telegram.app_main.bot

            if getattr(ozernik, 'discord_id', None):
                user = await self.bot.fetch_user(ozernik.discord_id)
                avatar_url = user.display_avatar.url

            else:
                try:
                    photos = await bot.get_user_profile_photos(telegram_user_id, limit=1)
                    if photos.total_count > 0:
                        file_id = photos.photos[0][0].file_id
                        file = await bot.get_file(file_id)
                        avatar_url = file.file_path
                except Exception as e:
                    print(f"Error getting {telegram_user_id} avatar: {e}")

            if not avatar_url:
                avatar_url = random_avatar(telegram_user_id)

            return avatar_url
        except Exception as e:
            print(f"get_avatar error: {e}")
            return random_avatar(telegram_user_id)

    @staticmethod
    def get_reply_ping(ds_message: discord.Message) -> str:
        try:
            if not ds_message.webhook_id:
                reply_ping = ds_message.author.mention
            else:
                reply_ping = f'**{ds_message.author.display_name}**'
            return reply_ping
        except Exception as e:
            print(f"get_reply_ping error: {e}")

    @staticmethod
    def remove_unnecessary_markup(text: str) -> str:
        try:
            text = text.replace('\n', ' ')
            text = text.replace('**', '')

            if text.startswith('-# ') or text.startswith('## '):
                text = text.replace('-# ', '', 1)
            elif text.startswith('# '):
                text = text.replace('# ', '', 1)
            elif text.startswith('## '):
                text = text.replace('## ', '', 1)
            elif text.startswith('### '):
                text = text.replace('### ', '', 1)

            text = text.replace('https://', '' )
            text = text.replace('http://', '')

            return text
        except Exception as e:
            print(f"remove_unnecessary_markup error: {e}")

    async def get_reply_body(self, ds_message: discord.Message) -> tuple[str,  str]:
        try:
            reply_text = ds_message.clean_content

            if ds_message.webhook_id:
                lines = reply_text.splitlines(keepends=True)

                if lines and lines[0].startswith('-# ┏  '):
                    reply_text = ''.join(lines[1:])

            reply_text = self.remove_unnecessary_markup(reply_text)

            if len(ds_message.author.display_name) / 2 + len(reply_text) > 30:
                reply_text = reply_text[:30] + "..."

            reply_mention = f'https://discord.com/channels/{ds_message.guild.id}/{ds_message.channel.id}/{ds_message.id}'

            if reply_text:
                print('reply_text')
                reply_body = f'[{reply_text}]({reply_mention})'
            else:
                print('else')
                reply_body = f'{reply_mention}'

            return reply_body, reply_mention
        except Exception as e:
            print(f"get_reply_body error: {e}")

    @staticmethod
    def format_origin(message: telegram.Message, content: str) -> str:
        try:
            origin = message.forward_origin

            title = 'Неизвестно'
            match origin.type:
                case "user":
                    title = origin.sender_user.full_name

                case "hidden_user":
                    title = origin.sender_user_name

                case "chat":
                    title = origin.sender_chat.title

                case "channel":
                    title = origin.chat.title

            content = content.replace('\n', '\n> ### ')

            content = (
                '> -# ↪ *Переслано*\n'
               f'> ### {content}\n'
               f'> -# {title} • <t:{int(origin.date.timestamp())}:R>\n'
            )

            return content

        except Exception as e:
            print(f"get_origin error: {e}")

    @staticmethod
    def get_external_reply_sender_name(message) -> str:
        ext = message.external_reply
        if ext is None:
            return 'Неизвестный пользователь'

        origin = ext.origin

        sender_user = getattr(origin, "sender_user", None)
        if sender_user is not None:
            return sender_user.full_name

        sender_user_name = getattr(origin, "sender_user_name", None)
        if sender_user_name is not None:
            return sender_user_name

        sender_chat = getattr(origin, "sender_chat", None)
        if sender_chat is not None:
            return sender_chat.title

        origin_chat = getattr(origin, "chat", None)
        if origin_chat is not None:
            return origin_chat.title

        return 'Неизвестный пользователь'

    async def get_content(self, message: telegram.Message, chat_link: DataTypes.ChatLink) -> tuple[str, str]:
        try:
            content = message.text_markdown_v2 or message.caption_markdown_v2 or ''

            content = re.sub(r"@everyone", r"@evеryone", content)
            content = re.sub(r"@here", r"@hеre", content)

            reply_block = ''

            if message.forward_origin:
                content = self.format_origin(message, content)

            if message.reply_to_message is not None:
                tg_replied_message = message.reply_to_message
                if tg_replied_message.forum_topic_created is None:

                    print('reply_to_message')
                    replied_message_link = self.db.get_message_link(
                        message_id=tg_replied_message.message_id,
                        mess_type="telegram",
                        chat_link_id=chat_link.id,
                    )

                    if replied_message_link:
                        channel = self.bot.get_channel(chat_link.discord_chanel_id)
                        ds_replied_message: discord.Message = await channel.fetch_message(replied_message_link.discord_message_id)

                        reply_ping = self.get_reply_ping(ds_replied_message)
                        reply_body = await self.get_reply_body(ds_replied_message)
                        reply_block = f'-# ┏  {reply_ping}╺╸**{reply_body}**\n'
                    else:
                        reply_block = f'-# ┏  **{message.reply_to_message.from_user.full_name}**╺╸**Неизвестное сообщение**\n'

            if message.external_reply is not None:
                print('external_reply')
                external_reply = message.external_reply

                external_message_id = getattr(external_reply, "message_id", None)
                external_chat = getattr(external_reply, "chat", None)

                print(external_message_id, external_chat)

                if external_message_id is not None and external_chat is not None:
                    discord_message_link = self.db.deep_get_discord_message_link(external_message_id, external_chat.id)

                    if discord_message_link:
                        channel = self.bot.get_channel(discord_message_link.chat_link.discord_chanel_id)
                        ds_replied_message: discord.Message = await channel.fetch_message(discord_message_link.discord_message_id)

                        reply_ping = self.get_reply_ping(ds_replied_message)
                        reply_body, reply_mention = await self.get_reply_body(ds_replied_message)
                        reply_block = f'-# ┏  {reply_ping}╺╸**{reply_mention}**\n'
                    else:
                        text = getattr(message.quote, 'text', None)
                        if text is not None:
                            text = text.replace('\n', '\n> ')
                        reply_block = f'-# ┏  **{self.get_external_reply_sender_name(message)}**╺╸**{external_chat.title}**\n> {text}\n'

            return content, reply_block
        except Exception as e:
            print(f"get_content error: {e}")

    async def get_username(self, telegram_user: telegram.User) -> str:
        try:
            username = telegram_user.username

            ozernik = self.db.get_user(telegram_user.id, 'telegram_id')

            if getattr(ozernik, 'discord_id', None):
                user = await self.bot.fetch_user(ozernik.discord_id)
                username = user.display_name

            return username
        except Exception as e:
            print(f"get_username error: {e}")

    async def send_webhook_message(self, chat_link: DataTypes.ChatLink, tg_messages: list[telegram.Message], files: list):
        try:
            channel = self.bot.get_channel(chat_link.discord_chanel_id)
            webhook = await self.get_webhook(channel)

            if tg_messages:
                tg_message = tg_messages[0]
            else:
                print("Список tg_messages пустой")
                return

            content, reply_block = await self.get_content(tg_message, chat_link)
            username = await self.get_username(tg_message.from_user)
            avatar_url = await self.get_avatar(tg_message.from_user.id)

            kwargs = {
                "content": reply_block + content,
                "username": username,
                "avatar_url": avatar_url,
                "wait": True,
            }

            if files:
                discord_files = []
                for file in files:
                    if os.path.exists(file):
                        discord_files.append(discord.File(file))
                kwargs["files"] = discord_files

            message = await webhook.send(**kwargs)
            ozernik = self.db.get_user(tg_message.from_user.id, 'telegram_id')

            self.db.add_message_link(
                user_id=getattr(ozernik, 'id', None),
                link_chat_id=chat_link.id,
                discord_message_id=message.id,
                telegram_message_id=tg_message.id,
                text=content,
                repeater_id=tg_message.from_user.id
            )
            for file in files:
                if os.path.exists(file):
                    os.remove(file)

        except Exception as e:
            print(f"send_webhook_message error: {e}")

class Telegram:
        def __init__(self, cog: Bridge):
            self.cog = cog

            self.app_main: telegram.ext.Application = None
            self.app_repeater1: telegram.ext.Application = None
            self.app_repeater2: telegram.ext.Application = None

            self.available_groups: set[telegram.Chat] = set()
            self.available_forums: set[telegram.Chat] = set()
            self.available_channels: set[telegram.Chat] = set()

            self.chats: set[telegram.Chat] = set()

            self.chat_locks = defaultdict(asyncio.Lock)

            self.attachments_index = 0

            self._repeaters_memory: dict[tuple[int, int], dict[str, int]] = {}

            self.entities = {}

            self.albums = defaultdict(list)
            self.album_tasks = {}

        def get_repeater(self, chat_link: DataTypes.ChatLink, user_id: int) -> tuple[telegram.Bot, bool]:
            chat_ids = (chat_link.telegram_chat_id, chat_link.telegram_topic_id)
            replace = False

            if chat_ids not in self._repeaters_memory:
                self._repeaters_memory[chat_ids] = {
                    'index': 1,
                    'user_id': user_id,
                }
                replace = True

            elif self._repeaters_memory[chat_ids]['user_id'] != user_id:
                self._repeaters_memory[chat_ids]['index'] += 1
                self._repeaters_memory[chat_ids]['user_id'] = user_id
                replace = True

            if self._repeaters_memory[chat_ids]['index'] % 2 == 0:
                return self.app_repeater1.bot, replace
            else:
                return self.app_repeater2.bot, replace

        def add_chat(self, chat: telegram.Chat): # noqa
            self.chats.add(chat)

            if chat.is_forum:
                self.available_forums.add(chat)

            elif chat.type == 'group' or chat.type == 'supergroup':
                self.available_groups.add(chat)

            elif chat.type == 'channel':
                self.available_channels.add(chat)

        def add_entities(self, message: telegram.Message):
            self.entities[(message.id, message.chat.id, message.message_thread_id)] = message.entities or message.caption_entities

        async def save_discord_attachments(self, message: discord.Message) -> list[str]:
            files = []
            base_dir = os.path.dirname(os.path.abspath(__file__))
            temp_dir = os.path.join(base_dir, "temp")
            os.makedirs(temp_dir, exist_ok=True)

            if message.attachments:
                for attachment in message.attachments:
                    name, ext = os.path.splitext(attachment.filename)
                    unique_name = f"{name}_{self.attachments_index}{ext}"
                    file_path = os.path.join(temp_dir, unique_name)

                    await attachment.save(file_path)  # noqa
                    files.append(file_path)
                    self.attachments_index += 1

            if message.stickers:
                sticker = message.stickers[0]
                file_path = os.path.join(temp_dir, f"{sticker.name}{sticker.format}")
                await sticker.save(file_path)
                files.append(file_path)

            return files

        async def get_username(self, user_id: int) -> str:
            for chat in self.chats:
                try:
                    member = await self.app_main.bot.get_chat_member(chat.id, user_id)
                    if member:
                        return member.username
                except BadRequest:
                    continue
            return 'username'

        async def get_hat(self, author: discord.User) -> str:
            ozernik = self.cog.db.get_user(author.id, 'discord_id')
            if ozernik is None:
                return f'<b><a>{author.display_name}</a></b>:\n'

            if ozernik.telegram_id:
                username = self.get_username(ozernik.telegram_id)
                return f'<b><a href="https://t.me/{username}">@{author.display_name}</a></b>:\n'

            return f'<b><a href="https://t.me/addlist/hFRN5LR90SliN2Iy">{author.display_name}</a></b>:\n'

        async def check_bots_exist(self, chat_id: int, check_can_manage_topics: bool = True) -> bool:
            try:
                try:
                    main_bot = await self.app_main.bot.get_chat_member(
                        chat_id=chat_id,
                        user_id=self.app_main.bot.id
                    )
                except telegram.error.BadRequest:
                    return False, f"Главный бот не найден в чате."

                if main_bot.status not in ['administrator', 'creator']:
                    return False, f"Главный бот должен быть администратором."

                if getattr(main_bot, 'can_manage_topics', False) and check_can_manage_topics:
                    return False, f"У главного бота нет прав на управление темами (форумом)."

                try:
                    await self.app_main.bot.get_chat_member(chat_id=chat_id, user_id=self.app_repeater1.bot.id)
                except telegram.error.BadRequest:
                    return False, f"Бот-повторитель №1 не найден в чате."

                try:
                    await self.app_main.bot.get_chat_member(chat_id=chat_id, user_id=self.app_repeater2.bot.id)
                except telegram.error.BadRequest:
                    return False, f"Бот-повторитель №2 не найден в чате."

                return True, f"Все проверки пройдены успешно!"

            except telegram.error.TelegramError as e:
                return False, f"Ошибка Telegram API: \n{e}"

        async def check_announce_linked_chat_bots_exist(self, announce_chat_id: int) -> bool:
            try:
                bot = self.app_main.bot
                channel = await bot.get_chat(announce_chat_id)

                if not channel.linked_chat_id:
                    return False, f"Чат обсуждения отсутствует. Включите комментарии."

                try:
                    main_bot = await self.app_main.bot.get_chat_member(
                        chat_id=channel.linked_chat_id,
                        user_id=self.app_main.bot.id
                    )
                except (telegram.error.BadRequest, telegram.error.Forbidden):
                    return False, f"Главный бот не найден в связанном чате обсуждения. Зайдите в чат обсуждений и добавьте ботов."

                if main_bot.status not in ['administrator', 'creator']:
                    return False, f"Главный бот должен быть администратором в связанном чате обсуждений."

                try:
                    await self.app_main.bot.get_chat_member(chat_id=channel.linked_chat_id, user_id=self.app_repeater1.bot.id)
                except telegram.error.BadRequest:
                    return False, f"Бот-повторитель №1 не найден в чате."

                try:
                    await self.app_main.bot.get_chat_member(chat_id=channel.linked_chat_id, user_id=self.app_repeater2.bot.id)
                except telegram.error.BadRequest:
                    return False, f"Бот-повторитель №2 не найден в чате."

                return True, f"Все проверки пройдены успешно!"

            except telegram.error.TelegramError as e:
                return False, f"Ошибка Telegram API: {e}"

        async def get_formatted_content(self, message: discord.Message) -> str:
            content = markdown_to_html_custom(message.clean_content)
            for user in message.mentions:
                ozernik = self.cog.db.get_user(user.id, 'discord_id')
                if getattr(ozernik, 'telegram_id', None):
                    content = content.replace(
                        f"@{user.display_name}",
                        f'<a href="tg://user?id={ozernik.telegram_id}">@{user.display_name}</a>'
                    )
                    continue
                else:
                    content = content.replace(
                        f"@{user.display_name}",
                        f"@{user.name}"
                    )
                    continue

            for channel in message.channel_mentions:
                chat_link = self.cog.db.get_chat_link(channel.id, 'discord')
                if chat_link:
                    chat_id = str(chat_link.telegram_chat_id).replace('-100', '')
                    topic_id = f'/{chat_link.telegram_topic_id}' if chat_link.telegram_topic_id else ''
                    content = content.replace(
                        f"#{channel.name}",
                        f'<a href="https://t.me/c/{chat_id}{topic_id}">#{channel.name}</a>'
                    )

            for role in message.role_mentions:
                content = content.replace(f"&lt;@&amp;{role.id}&gt;", f"@{role.name}")

            pattern = re.compile(
                r"https://discord\.com/channels/(\d+)/(\d+)/(\d+)"
            )
            print(content)
            for match in pattern.finditer(content):
                full_link = match.group(0)

                guild_id, channel_id, discord_message_id = match.groups()

                channel_id_int = int(channel_id)
                discord_message_id_int = int(discord_message_id)

                chat_link = self.cog.db.get_chat_link(channel_id_int, 'discord')
                message_link = self.cog.db.get_message_link(discord_message_id_int, 'discord')

                channel = self.cog.bot.get_channel(channel_id_int)
                channel_name = getattr(channel, 'name', None) if channel else "неизвестный-канал"
                channel_name = html.escape(channel_name)

                if chat_link and message_link:
                    chat_id = str(chat_link.telegram_chat_id).removeprefix("-100")
                    telegram_message_id = message_link.telegram_message_id

                    if chat_link.telegram_topic_id:
                        tg_link = (
                            f"https://t.me/c/{chat_id}/"
                            f"{chat_link.telegram_topic_id}/"
                            f"{telegram_message_id}"
                        )
                    else:
                        tg_link = f"https://t.me/c/{chat_id}/{telegram_message_id}"

                    replacement = f'<u><a href="{tg_link}">#{channel_name}▸</a></u>'
                else:
                    replacement = f'<u><a href="{full_link}">#{channel_name}▸</a></u>'

                content = content.replace(full_link, replacement)

            return content

        async def get_text(self, message: discord.Message, have_title: bool = False):
            try:
                title, body = str(), str()

                if have_title:
                    title = await self.get_hat(message.author)

                if message.content:
                    body = await self.get_formatted_content(message)

                text = title + body
                return text
            except Exception as e:
                print('get_text error: ', e)

        def is_repeater(self, user_id: int) -> bool:
            if user_id in (self.app_repeater1.bot.id, self.app_repeater2.bot.id):
                return True
            return False

        @staticmethod
        def get_media_kind(file_path: str) -> str:
            ext = Path(file_path).suffix.lower()

            if ext in {".jpg", ".jpeg", ".png"}:
                return "photo"
            if ext in {".mp4", ".mov", ".m4v"}:
                return "video"
            if ext in {".gif", ".webp", ".webp2"}:
                return "animation"
            if ext in {".mp3", ".wav", ".m4a", ".flac", ".ogg"}:
                return "audio"

            return "document"

        @staticmethod
        def get_album_group(kind: str) -> str | None:
            """
            visual: фото + видео можно в один альбом
            audio: только аудио с аудио
            document: только документы с документами
            None: нельзя отправлять альбомом
            """
            if kind in ("photo", "video"):
                return "visual"
            if kind == "audio":
                return "audio"
            if kind == "document":
                return "document"

            return None

        @staticmethod
        def chunks(items: list, size: int) -> Iterable[list]:
            for i in range(0, len(items), size):
                yield items[i:i + size]

        @staticmethod
        def make_input_media(kind: str, file, caption: str | None, parse_mode: str | None):
            kwargs = {
                "media": file,
                "caption": caption,
                "parse_mode": parse_mode if caption else None,
            }

            if kind == "photo":
                return InputMediaPhoto(**kwargs)
            if kind == "video":
                return InputMediaVideo(**kwargs)
            if kind == "audio":
                return InputMediaAudio(**kwargs)
            if kind == "document":
                return InputMediaDocument(**kwargs)

            raise ValueError(f"Unsupported album media kind: {kind}")

        async def send_media_files(
                self,
                bot: Bot,
                chat_id: int,
                files_path: list[str],
                *,
                message_thread_id: int | None = None,
                text: str | None = None,
                parse_mode: str | None = "HTML",
                reply_parameters=None,
                delete_after: bool = True,
                caption_once: bool = True,
                reply_once: bool = True,
        ) -> list[Message]:
            sent_messages: list[Message] = []

            caption_used = False
            reply_used = False

            def take_caption() -> str | None:
                nonlocal caption_used

                if not text:
                    return None

                if caption_once:
                    if caption_used:
                        return None
                    caption_used = True

                return text

            def take_reply_parameters():
                nonlocal reply_used

                if reply_parameters is None:
                    return None

                if reply_once:
                    if reply_used:
                        return None
                    reply_used = True

                return reply_parameters

            async def send_one(file_path: str):
                kind = self.get_media_kind(file_path)
                caption = take_caption()
                current_reply_parameters = take_reply_parameters()

                common_kwargs = {
                    "chat_id": chat_id,
                    "message_thread_id": message_thread_id,
                    "caption": caption,
                    "parse_mode": parse_mode if caption else None,
                    "reply_parameters": current_reply_parameters,
                }

                with open(file_path, "rb") as file:
                    if kind == "photo":
                        return await bot.send_photo(photo=file, **common_kwargs)

                    if kind == "video":
                        return await bot.send_video(video=file, **common_kwargs)

                    if kind == "animation":
                        return await bot.send_animation(animation=file, **common_kwargs)

                    if kind == "audio":
                        return await bot.send_audio(audio=file, **common_kwargs)

                    return await bot.send_document(
                        document=file,
                        filename=Path(file_path).name,
                        **common_kwargs,
                    )

            async def send_album(batch: list[str]):
                opened_files = []

                try:
                    caption = take_caption()
                    current_reply_parameters = take_reply_parameters()
                    media = []

                    for i, file_path in enumerate(batch):
                        kind = self.get_media_kind(file_path)
                        file = open(file_path, "rb")
                        opened_files.append(file)

                        media.append(
                            self.make_input_media(
                                kind=kind,
                                file=file,
                                caption=caption if i == 0 else None,
                                parse_mode=parse_mode,
                            )
                        )

                    return await bot.send_media_group(
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                        media=media,
                        reply_parameters=current_reply_parameters,
                    )

                finally:
                    for file in opened_files:
                        file.close()

            try:
                current_batch: list[str] = []
                current_group: str | None = None

                async def flush_batch():
                    nonlocal current_batch, current_group

                    if not current_batch:
                        return

                    if len(current_batch) == 1:
                        print('one')
                        sent_messages.append(await send_one(current_batch[0]))
                    else:
                        for batch_chunk in self.chunks(current_batch, 10):
                            if len(batch_chunk) == 1:
                                print('onee')
                                sent_messages.append(await send_one(batch_chunk[0]))
                            else:
                                print('album')
                                messages = await send_album(batch_chunk)
                                sent_messages.extend(messages)

                    current_batch = []
                    current_group = None

                for file_path in files_path:
                    kind = self.get_media_kind(file_path)
                    album_group = self.get_album_group(kind)

                    if album_group is None:
                        print('album_group is None')
                        await flush_batch()
                        sent_messages.append(await send_one(file_path))
                        continue

                    if current_group is None:
                        print('current_group is None')
                        current_group = album_group
                        current_batch.append(file_path)
                        continue

                    if current_group == album_group:
                        print('current_group == album_group')
                        current_batch.append(file_path)
                    else:
                        print('current_group != album_group')
                        await flush_batch()
                        current_group = album_group
                        current_batch.append(file_path)

                print('flush_batch')
                await flush_batch()

                return sent_messages

            finally:
                if delete_after:
                    for file_path in files_path:
                        with suppress(FileNotFoundError):
                            os.remove(file_path)

        @staticmethod
        async def _get_file(message: telegram.Message, bot: telegram.Bot):
            try:
                file_path = None

                if message.photo:
                    photo = message.photo[-2]
                    tg_file = await bot.get_file(photo.file_id)
                    file_path = await download_telegram_file(tg_file, format_='jpg')

                elif message.video:
                    tg_file = await bot.get_file(message.video.file_id)
                    file_path = await download_telegram_file(tg_file, format_='mp4')

                elif message.voice:
                    tg_file = await bot.get_file(message.voice.file_id)
                    file_path = await download_telegram_file(tg_file, format_='ogg')

                elif message.sticker:
                    tg_file = await bot.get_file(message.sticker.file_id)
                    format_ = "tgs" if message.sticker.is_animated else "webp"
                    file_path = await download_telegram_file(tg_file, format_=format_)

                elif message.document:
                    tg_file = await bot.get_file(message.document.file_id)
                    file_path = await download_telegram_file(tg_file, format_=message.document.file_name)

                elif message.video_note:
                    tg_file = await bot.get_file(message.video_note.file_id)
                    file_path = await download_telegram_file(tg_file, format_='mp4')

                elif message.animation:
                    tg_file = await bot.get_file(message.animation.file_id)
                    format_ = os.path.splitext(tg_file.file_path)[1] or "mp4"
                    file_path = await download_telegram_file(tg_file, format_=format_)

                return file_path
            except Exception as e:
                print('get_file error: ', e)

        async def process_message(self, message: telegram.Message, chat_link: DataTypes.ChatLink):
            self.get_repeater(chat_link, 0)

            if not message.media_group_id:
                files = []
                file = await self._get_file(message=message, bot=self.app_main.bot)
                if file:
                    files.append(file)
                await self.cog.send_webhook_message(chat_link, [message], files)
            else:
                group_id = message.media_group_id
                self.albums[group_id].append(message)
                if group_id not in self.album_tasks:
                    # Отправляем альбом на обработку, а уже там вебхук отправим.
                    self.album_tasks[group_id] = asyncio.create_task(self.process_album(chat_link, group_id))

        async def upd_on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE): # noqa
            try:
                self.add_chat(update.effective_chat)

                message = update.message
                thread_id = -1
                post = update.channel_post
                chat = await context._bot.get_chat(update.effective_chat.id)

                if message:
                    thread_id = message.message_thread_id if message.message_thread_id and update.effective_chat.is_forum else -1
                    print(f'Telegram message {message.message_id} {thread_id}')

                elif post:
                    print(f'Telegram post {post.id}')

                else:
                    print(f'No message.')
                    return

                chat_link = self.cog.db.get_chat_link(
                    chat_id=update.effective_chat.id,
                    chat_type='telegram',
                    thread_id=thread_id
                )

                if message.from_user.id == self.app_main.bot.id:
                    print('from_user is main bot.')
                    return

                if message.from_user.id == self.app_repeater1.bot.id or message.from_user.id == self.app_repeater2.bot.id:
                    print('from_user is repeater bot.')
                    return

                if not chat_link:
                    print('chat_link not found.')
                    return

                asyncio.create_task(self.process_message(message, chat_link))

            except Exception as e:
                print('upd_on_message error: ', e)

        async def process_album(self, chat_link: DataTypes.ChatLink, group_id: int):
            await asyncio.sleep(1)  # ждём, пока прилетят остальные фото/видео

            messages = self.albums.pop(group_id, [])
            self.album_tasks.pop(group_id, None)

            print(f"Получен альбом из {len(messages)} вложений")

            files = []

            for message in messages:
                file = await self._get_file(message=message, bot=self.app_main.bot)
                if file:
                    files.append(file)

            await self.cog.send_webhook_message(chat_link, messages, files)

        async def send_repeater_message(self, chat_link: DataTypes.ChatLink, message: discord.Message):
            try:
                bot, replaced = self.get_repeater(chat_link, message.author.id)
                files_path = await self.save_discord_attachments(message)
                if files_path:
                    replaced = True
                if getattr(await self.app_main.bot.get_chat(chat_link.telegram_chat_id), 'linked_chat_id', False):
                    replaced = False
                text = await self.get_text(message, have_title=replaced)

                reply_parameters = None
                if message.reference and message.reference.message_id:
                    reply = self.cog.db.get_message_link(message.reference.message_id, 'discord')
                    if reply:
                        common_kwargs = {
                            'message_id': reply.telegram_message_id,
                            'chat_id': reply.chat_link.telegram_chat_id,
                            'allow_sending_without_reply': True
                        }

                        if (reply.telegram_message_id, chat_link.telegram_chat_id, chat_link.telegram_topic_id) in self.entities:
                            common_kwargs.update({
                                'quote': reply.text,
                                'quote_entities': self.entities[
                                    reply.telegram_message_id,
                                    chat_link.telegram_chat_id,
                                    chat_link.telegram_topic_id
                                ]
                            })

                        reply_parameters = telegram.ReplyParameters(**common_kwargs)

                if not files_path:
                    tg_message = await bot.send_message(
                        chat_id=chat_link.telegram_chat_id,
                        message_thread_id=chat_link.telegram_topic_id,
                        text=text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        reply_parameters=reply_parameters
                    )
                    self.add_entities(tg_message)  # noqa

                    ozernik = self.cog.db.get_user(message.author.id, 'discord_id')
                    self.cog.db.add_message_link(
                        user_id=ozernik.id,
                        link_chat_id=chat_link.id,
                        discord_message_id=message.id,
                        telegram_message_id=tg_message.id,
                        text=tg_message.text or tg_message.caption,
                        repeater_id=bot.id,
                    )
                else:
                    tg_messages = await self.send_media_files(
                        bot=bot,
                        chat_id=chat_link.telegram_chat_id,
                        message_thread_id=chat_link.telegram_topic_id,
                        files_path=files_path,
                        text=text,
                        parse_mode="HTML",
                        reply_parameters=reply_parameters
                    )

                    for tg_message in tg_messages:
                        self.add_entities(tg_message)  # noqa

                        ozernik = self.cog.db.get_user(message.author.id, 'discord_id')
                        self.cog.db.add_message_link(
                            user_id=ozernik.id,
                            link_chat_id=chat_link.id,
                            discord_message_id=message.id,
                            telegram_message_id=tg_message.id,
                            text=tg_message.text or tg_message.caption,
                            repeater_id=bot.id,
                        )
            except Exception as e:
                print('send_repeater_message error: ', e)

        async def send_main_bot_message(self, chat_link: DataTypes.ChatLink, message: discord.Message):
            try:
                bot: telegram.Bot = self.app_main.bot
                files_path = await self.save_discord_attachments(message)
                text = await self.get_text(message)

                reply_parameters = None
                if message.reference and message.reference.message_id:
                    reply = self.cog.db.get_message_link(message.reference.message_id, 'discord')
                    if reply:
                        common_kwargs = {
                            'message_id': reply.telegram_message_id,
                            'chat_id': reply.chat_link.telegram_chat_id,
                            'allow_sending_without_reply': True
                        }

                        if (reply.telegram_message_id, chat_link.telegram_chat_id,
                            chat_link.telegram_topic_id) in self.entities:
                            common_kwargs.update({
                                'quote': reply.text,
                                'quote_entities': self.entities[
                                    reply.telegram_message_id,
                                    chat_link.telegram_chat_id,
                                    chat_link.telegram_topic_id
                                ]
                            })

                        reply_parameters = telegram.ReplyParameters(**common_kwargs)

                if not files_path:
                    tg_message = await bot.send_message(
                        chat_id=chat_link.telegram_chat_id,
                        message_thread_id=chat_link.telegram_topic_id,
                        text=text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        reply_parameters=reply_parameters
                    )
                    self.add_entities(tg_message) # noqa

                    ozernik = self.cog.db.get_user(message.author.id, 'discord_id')
                    self.cog.db.add_message_link(
                        user_id=ozernik.id,
                        link_chat_id=chat_link.id,
                        discord_message_id=message.id,
                        telegram_message_id=tg_message.id,
                        text=tg_message.text or tg_message.caption,
                        repeater_id=bot.id,
                    )
                else:
                    tg_messages = await self.send_media_files(
                        bot=bot,
                        chat_id=chat_link.telegram_chat_id,
                        message_thread_id=chat_link.telegram_topic_id,
                        files_path=files_path,
                        text=text,
                        parse_mode="HTML",
                        reply_parameters=reply_parameters
                    )

                    for tg_message in tg_messages:
                        self.add_entities(tg_message)  # noqa

                        ozernik = self.cog.db.get_user(message.author.id, 'discord_id')
                        self.cog.db.add_message_link(
                            user_id=ozernik.id,
                            link_chat_id=chat_link.id,
                            discord_message_id=message.id,
                            telegram_message_id=tg_message.id,
                            text=tg_message.text or tg_message.caption,
                            repeater_id=bot.id,
                        )
            except Exception as e:
                print('send_main_bot_message error:', e)

        async def send_users_bot_message(self, chat_id: int):
            pass

        def setup_handlers(self):
            """
            Настройка обработчиков
            """
            # Вызов для любых сообщений в группах, кроме команд и кроме редактирования сообщений.
            self.app_main.add_handler(
                MessageHandler(
                    filters.ALL
                    & ~filters.COMMAND
                    & ~filters.UpdateType.EDITED,
                    self.upd_on_message
                )
            )

        async def start(self):
            try:
                self.app_repeater1 = (
                    ApplicationBuilder().
                    token(TELEGRAM_TOKEN_REPEATER_1).
                    connect_timeout(30).
                    read_timeout(30).
                    build()
                )
                self.app_repeater2 = (
                    ApplicationBuilder().
                    token(TELEGRAM_TOKEN_REPEATER_2).
                    connect_timeout(30).
                    read_timeout(30).
                    build()
                )
                self.app_main = (
                    ApplicationBuilder().
                    token(TELEGRAM_TOKEN_MAIN).
                    connect_timeout(30).
                    read_timeout(30).
                    build()
                )

                await self.app_main.bot.get_me()
                await self.app_repeater1.bot.get_me()
                await self.app_repeater2.bot.get_me()

                self.setup_handlers()

            except Exception as e:
                print(e)
                return

            try:
                await self.app_main.initialize()
                await self.app_main.start()
                await self.app_main.updater.start_polling()
                print("Главный telegram Бот запущен.")
            except Exception as e:
                print(f'Ошибка запуска главного telegram Бота: {e}')
                return

            try:
                await self.app_repeater1.initialize()
                await self.app_repeater1.start()
                await self.app_repeater1.updater.start_polling()
                print("Первый стандартный telegram ретранслятор запущен.")
            except Exception as e:
                print(f'Ошибка запуска первого стандартного telegram ретранслятора: {e}')
                return

            try:
                await self.app_repeater2.initialize()
                await self.app_repeater2.start()
                await self.app_repeater2.updater.start_polling()
                print("Второй стандартный telegram ретранслятор запущен.")
            except Exception as e:
                print(f'Ошибка запуска второго стандартного telegram ретранслятора: {e}')
                return

async def setup(bot):
    # await bot.add_cog(Bridge(bot))
    pass
