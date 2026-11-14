import inspect
import traceback
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from discord import Embed
from typing_extensions import Literal

from config import config
from utilities import require_staff

LOG_CHANNEL = 1131880136053100616

ALLOWED_ROLES = [725675581881974794, # admin role main server
                 1408776412177109142, 
                 1284568196673830924, 
                 779015800555176006,
                 1398937618527293510 # admin role test server
                 ]

# Выключение и перезапуск по сообщению. Код выхода для «выключить» — в юните systemd нужна строка
# RestartPreventExitStatus=64, иначе Restart=always поднимет бота снова (см. on_message).
SHUTDOWN_EXIT_CODE = 64
BOT_POWER_COMMANDS = {".bot_shutdown": "shutdown", ".bot_restart": "restart"}


# ---------- /staff_roles: роли администрации и модерации бота (решение Alium 2026-11-15) ----------

STAFF_LEVELS = (("ADMIN_ROLES_IDS", "Администрация"), ("MODERATOR_ROLES_IDS", "Модерация"))


def staff_roles_text(guild) -> str:
    lines = ["**Роли администрации и модерации бота** — общие для всех модулей."]
    for key, title in STAFF_LEVELS:
        roles = [f"<@&{role_id}>" for role_id in (getattr(config, key, None) or []) if guild.get_role(role_id)]
        lines.append(f"{title}: {', '.join(roles) if roles else 'не задано'}")
    lines.append("-# У кого есть право «Администратор» на сервере — администрация в любом случае. "
                 "Модерации доступны муты и дела РСН, твинки и /settings_karma.")

    # Отдельный, зашитый в код список панели (/send_message, /edit_message, .bot_shutdown, .bot_restart) —
    # с общим не сведён: чьи две роли из него, неизвестно. Показ здесь — чтобы узнать на выкатке.
    lines.append("")
    lines.append("**Старый список панели** (`/send_message`, `/edit_message`, `.bot_shutdown`, `.bot_restart`) — "
                 "сведём с общим в следующем патче:")
    for role_id in ALLOWED_ROLES:
        role = guild.get_role(role_id)
        if role is not None:
            members = len(getattr(role, "members", []))
            lines.append(f"- <@&{role_id}> (`{role_id}`) — участников: {members}")
        else:
            lines.append(f"- `{role_id}` — такой роли на этом сервере нет")
    return "\n".join(lines)


class StaffRolesView(discord.ui.View):
    """
    Два списка ролей и «Сохранить». Нажимать может только вызвавший.

    Select.values хранит только то, что выбрали в ЭТОМ меню, — предвыбранные значения туда не попадают.
    Если менять одну модерацию и нажать «Сохранить», администрация пришла бы пустой и стёрлась.
    Поэтому выбор запоминается в self.picked, а нетронутый список остаётся как был.
    """

    def __init__(self, author_id: int, guild):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.picked: dict[str, list[int]] = {}
        for row, (key, title) in enumerate(STAFF_LEVELS):
            # Только роли, что ещё есть на сервере; Object(type=Role) — так Discord понимает «предвыбрать роль».
            current = [
                discord.Object(id=role.id, type=discord.Role)
                for role in map(guild.get_role, getattr(config, key, None) or []) if role is not None
            ]
            select = discord.ui.RoleSelect(
                placeholder=f"{title}: выберите роли",
                min_values=0,
                max_values=10,
                default_values=current,
                row=row,
            )
            select.callback = self._make_callback(key, select)
            self.add_item(select)

    def _make_callback(self, key: str, select: discord.ui.RoleSelect):
        async def callback(interaction: discord.Interaction):
            self.picked[key] = [role.id for role in select.values]
            await interaction.response.defer()
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message("Это меню вызвал другой человек.", ephemeral=True)
        return False

    @discord.ui.button(label="Сохранить", style=discord.ButtonStyle.success, row=2)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        for key, role_ids in self.picked.items():
            setattr(config, key, role_ids)
        self.stop()
        await interaction.response.edit_message(content="✓ Сохранено.\n" + staff_roles_text(interaction.guild), view=None)


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
                    print(f"Выгружается модуль: {module_name}")
                    await self.bot.unload_extension(module)
                    config.module_off(module)
                    await interaction.followup.send(f"Выгружен модуль: {module_name}")
                    print(f"Выгружен модуль: {module_name}")
                case 'load':
                    print(f"Загружается модуль: {module_name}")
                    await self.bot.load_extension(module)
                    config.module_on(module)
                    await interaction.followup.send(f"Загружен модуль: {module_name}")
                    print(f"Загружен модуль: {module_name}")
                case _:
                    print(f"Перезагружается модуль: {module_name}")
                    await self.bot.reload_extension(module)
                    await interaction.followup.send(f"Перезагружен модуль: {module_name}")
                    print(f"Перезагружен модуль: {module_name}")
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='staff_roles', description='Роли администрации и модерации бота — общие для всех модулей.')
    @app_commands.guilds(config.GUILD_ID)
    @app_commands.default_permissions(administrator=True)
    async def staff_roles(self, interaction: discord.Interaction):
        if not await require_staff(interaction, "admin"):
            return
        await interaction.response.send_message(
            staff_roles_text(interaction.guild),
            view=StaffRolesView(interaction.user.id, interaction.guild),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        `.bot_shutdown` — выключить бота, `.bot_restart` — перезапустить (решение Alium 2026-11-15).

        На сервере бот работает под systemd с Restart=always: ЛЮБОЙ выход процесса — перезапуск через
        RestartSec (5 с). Поэтому прежний .bot_shutdown на деле был перезапуском. Теперь бот выходит
        с разными кодами: перезапуск — 0 (systemd поднимет), выключение — SHUTDOWN_EXIT_CODE, а в юните
        стоит RestartPreventExitStatus=64 — процесс, вышедший с этим кодом, systemd не поднимает.
        Без этой строки в юните .bot_shutdown по-прежнему будет перезапуском.
        """
        if message.author.bot:
            return

        action = BOT_POWER_COMMANDS.get(message.content.strip().lower())
        if action is None:
            return

        if message.guild is None or message.guild.id != config.GUILD_ID:
            return

        # Права — пока по старому списку панели (ALLOWED_ROLES); с общим сведём в следующем патче.
        if not any(role.id in ALLOWED_ROLES for role in getattr(message.author, "roles", [])):
            try:
                await message.channel.send("Нет прав выключать или перезапускать бота.", delete_after=8)
            except discord.HTTPException:
                pass
            return

        if action == "shutdown":
            text = f"Выключаюсь по команде {message.author.mention}."
        else:
            text = f"Перезапускаюсь по команде {message.author.mention} — вернусь через несколько секунд."
        try:
            await message.channel.send(text, delete_after=5)
        except discord.HTTPException:
            pass

        # Небольшая пауза — чтобы ответ успел уйти до отключения.
        await asyncio.sleep(0.5)

        print(f"{'Выключение' if action == 'shutdown' else 'Перезапуск'} по команде {message.author} (id={message.author.id})")
        # Код выхода читает bot.py после остановки (sys.exit(bot.exit_code)).
        self.bot.exit_code = SHUTDOWN_EXIT_CODE if action == "shutdown" else 0
        try:
            await self.bot.close()
        except Exception as error:
            print("Ошибка при остановке бота:", repr(error))


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