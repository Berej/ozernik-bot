import traceback
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from discord import Embed
from typing_extensions import Literal

from config import config

LOG_CHANNEL = 1131880136053100616

ALLOWED_ROLES = [725675581881974794, # admin role main server
                 1408776412177109142, 
                 1284568196673830924, 
                 779015800555176006,
                 1398937618527293510 # admin role test server
                 ]

class Panel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_modules_autocomplete(self, interaction: discord.Interaction, current: str): # noqa
        loaded_extensions: list[str] = list(self.bot.extensions.keys())
        all_extensions: dict[str, None] = config.MODULES
        results = []

        for extension in all_extensions:
            if current.lower() in extension.lower():
                module_name = extension.split('.')[-1]
                if extension not in loaded_extensions:
                    module_name += ' (выкл)'
                results.append(app_commands.Choice(name=module_name, value=extension))

        return results[:25]
    @app_commands.command(name='modules_control', description='Управление модулями.')
    @app_commands.describe(module="Новое содержимое сообщения.", mode='По умолчанию "reload".')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    @app_commands.autocomplete(module=get_modules_autocomplete)
    async def modules_control(self, interaction: discord.Interaction, module: str, mode: Literal["reload", "load", "unload"] | None = None):
        try:
            await interaction.response.defer(ephemeral=False, thinking=True)
            module_name = module.split('.')[-1]

            match mode:
                case 'unload':
                    await self.bot.unload_extension(module)
                    config.module_off(module)
                    await interaction.followup.send(f"Выгружен модуль: {module_name}")
                    print(f"Выгружен модуль: {module_name}")
                case 'load':
                    await self.bot.load_extension(module)
                    await interaction.followup.send(f"Загружен модуль: {module_name}")
                    print(f"Загружен модуль: {module_name}")
                case _:
                    await self.bot.reload_extension(module)
                    await interaction.followup.send(f"Перезагружен модуль: {module_name}")
                    print(f"Перезагружен модуль: {module_name}")
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Listen for the exact text ".bot_shutdown" in the main guild.
        If author is a guild member with one of ALLOWED_ROLES, acknowledge and shut the bot down.
        This is an emergency immediate shutdown — the bot will attempt to send a confirmation
        message and then call bot.close().
        """
        # ignore bots (including self)
        if message.author.bot:
            return

        # only react to the exact command (case-insensitive), trim whitespace
        if message.content.strip().lower() != ".bot_shutdown":
            return

        # require this to happen in the configured guild
        if message.guild is None or message.guild.id != config.GUILD_ID:
            return

        # ensure author is a guild Member (has roles)
        if not isinstance(message.author, discord.Member):
            return

        # role check: user must have at least one allowed role
        if not any(role.id in ALLOWED_ROLES for role in message.author.roles):
            # polite refusal; auto-delete to avoid clutter
            try:
                await message.channel.send("You do not have permission to shut down the bot.", delete_after=8)
            except Exception: # noqa
                pass
            return

        # Acknowledge and shutdown (give a short delay to allow the message to be delivered)
        try:
            await message.channel.send(f"Shutdown command received from {message.author.mention}. Shutting down...", delete_after=5)
        except Exception: # noqa
            # If we can't send in-channel, try DM to the author as a last resort (best-effort)
            try:
                await message.author.send("Shutdown command received. Bot is shutting down.")
            except Exception: # noqa
                pass

        # small delay so Discord has time to deliver the confirmation message
        await asyncio.sleep(0.5)

        # Log and close the bot cleanly
        print(f"Shutdown initiated by {message.author} (id={message.author.id})")
        try:
            await self.bot.close()
        except Exception as e:
            # if close fails for some reason, log and force-exit the process
            print("Error while closing bot:", repr(e))
            try:
                # best-effort: stop the loop
                loop = asyncio.get_running_loop()
                loop.stop()
            except Exception: # noqa
                pass


    # --------- BOT TEXT MODERATION COMMANDS --------- 
    # Here are slash-commands to send and modify bot's messages


    # Send message with bot
    @app_commands.command(
        name='send_message',
        description='Отправить сообщение от имени Бота'
    )
    @app_commands.describe(channel='Канал отправки', content="Содержимое сообщения.")
    @app_commands.guilds(config.GUILD_ID)
    async def _send_message(self, interaction: discord.Interaction, channel: discord.TextChannel, content: str):
        # Validate everything before touching Discord
        if not any(role.id in ALLOWED_ROLES for role in interaction.user.roles):
            await interaction.response.send_message("Только для Администрации.", ephemeral=True)
            return

        if not channel:
            await interaction.response.send_message("Канал не найден.", ephemeral=True)
            return

        if len(content) > 2000:
            await interaction.response.send_message("Сообщение слишком длинное.", ephemeral=True)
            return

        formatted_content = content.replace("\\n", "\n")
        if '\\n' in content:
            log_content = content.replace("\\n", "\n\\n")
        else:
            log_content = content.replace("\n", "\n\\n")

        # Acknowledge the interaction FIRST — before any channel.send()
        await interaction.response.send_message("Сообщение отправлено.", ephemeral=True)

        try:
            sent_message = await channel.send(formatted_content)
        except Exception as e:
            print(f"Error send_message: {e}")
            await interaction.followup.send(f"Произошла ошибка при отправке сообщения:\n```\n{e}\n```", ephemeral=True)
            return

        try:
            # Get or create webhook for logs
            log_channel = interaction.guild.get_channel(LOG_CHANNEL)
            webhooks = await log_channel.webhooks()
            webhook = next((w for w in webhooks if w.name == "Лилия, следящая за информацией"), None)

            if webhook is None:
                webhook = await log_channel.create_webhook(name="Лилия, следящая за информацией")
                print("Created new webhook")

            # Creating Embed
            embeds_pairs = (formatted_content, log_content)

            embed = Embed(
                title=f"Отправлено сообщение в #{channel.name}",
                url=f"https://discord.com/channels/{interaction.guild.id}/{channel.id}/{sent_message.id}",
                color=0x3498db
            )
            embed.set_author(
                name=interaction.user.name,
                icon_url=interaction.user.display_avatar.url
            )
            await webhook.send(embed=embed)

            for i, pair in enumerate(embeds_pairs):
                if i == 0:
                    embed = Embed(color=0x3498db, description=pair)
                else:
                    embed = Embed(color=0x3498db)
                    embed.set_footer(text=pair)
                await webhook.send(embed=embed)

        except Exception as e:
            print(f"Error log send_message: {e}")
            await interaction.followup.send(f"Произошла ошибка при логе сообщения:\n```\n{e}\n```", ephemeral=True)


    # Edit bot's message
    @app_commands.command(
        name='edit_message',
        description='Редактировать сообщение Бота.'
    )
    @app_commands.describe(
        channel="Канал сообщения.",
        message_id="ID сообщения.",
        content="Новое содержимое сообщения."
    )
    @app_commands.guilds(config.GUILD_ID)
    async def edit_message(self, interaction: discord.Interaction, channel: discord.TextChannel, message_id: str, content: str):
        try:
            # Проверка ролей
            if not any(role.id in ALLOWED_ROLES for role in interaction.user.roles):
                await interaction.response.send_message("Только для Администрации.", ephemeral=True)
                return

            # Проверка канала
            if not channel:
                await interaction.response.send_message("Канал не найден.", ephemeral=True)
                return

            # Получаем сообщение
            try:
                message = await channel.fetch_message(int(message_id))
            except Exception as e:
                await interaction.response.send_message(f"Сообщение не найдено: {e}", ephemeral=True)
                return

            if len(content) > 2000:
                await interaction.response.send_message("Сообщение слишком длинное.", ephemeral=True)
                return

            old_content = message.content or "*Пустое сообщение*"

            # Форматирование текста
            new_content = content.replace("\\n", "\n")
            if '\\n' in content:
                log_content = content.replace("\\n", "\n\\n")
            else:
                log_content = content.replace("\n", "\n\\n")

            log_old_content = message.content.replace("\n", "\n\\n")

            # Редактирование
            await message.edit(content=new_content)

            await interaction.response.send_message("Сообщение изменено.", ephemeral=True)
        except Exception as e:
            print(f"Error edit_message: {e}")
            await interaction.response.send_message(f"Произошла ошибка при изменении сообщения:\n```\n{e}\n```", ephemeral=True)
            return

        try:
            # Получаем или создаем вебхук для логов
            log_channel = interaction.guild.get_channel(LOG_CHANNEL)
            webhooks = await log_channel.webhooks()
            webhook = next((w for w in webhooks if w.name == "Лилия, следящая за информацией"), None)

            if webhook is None:
                webhook = await log_channel.create_webhook(name="Лилия, следящая за информацией")
                print("Created new webhook")

            # Формируем Embed
            embeds_pairs = [
                ("Старое сообщение:", [old_content, log_old_content]),
                ("Новое сообщение:", [new_content, log_content])
            ]

            embed = discord.Embed(
                title=f"Изменено сообщение в #{channel.name}",
                url=f"https://discord.com/channels/{interaction.guild.id}/{channel.id}/{message.id}",
                color=0xF1C40F
            )
            embed.set_author(
                name=interaction.user.name,
                icon_url=interaction.user.display_avatar.url
            )
            await webhook.send(embed=embed)

            for name, values in embeds_pairs:
                for i, value in enumerate(values):
                    if i == 0:
                        embed = discord.Embed(title=name, description=value, color=0xF1C40F)
                    else:
                        embed = discord.Embed(color=0xF1C40F)
                        embed.set_footer(text=value)
                    await webhook.send(embed=embed)

        except Exception as e:
            print(f"Error log edit_message: {e}")
            await interaction.followup.send(f"Произошла ошибка при логе изменения:\n```\n{e}\n```", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Panel(bot))