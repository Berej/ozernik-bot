"""
Личные голосовые каналы (переделка — решения Alium 2026-11-08).

Зашёл в канал-вход → бот создаёт личный войс и переносит туда. Людей в войсе не осталось
(боты не в счёт) → войс удаляется.

Бот помнит в общей базе database.db (utilities.Database):
- personal_voice_channels — чей каждый созданный войс. Раньше это жило в словаре в памяти
  и пропадало при перезапуске: бот забывал свои войсы и не удалял их, даже пустые.
  Строки остаются и после удаления канала — по ним «Топ каналов» плашки *i (модуль stats)
  показывает удалённый войс как «Войс Имя», а не теряет его;
- personal_voices — настройки, сохранённые /voice_save (название, лимит, закрыт ли, кого
  пускать). Следующий войс создаётся с ними; без /voice_save — с прошлыми сохранёнными или
  стандартными;
- personal_voice_minutes — кто сколько минут сидел в чьих войсах. Пока только копится —
  показ будет в новой плашке статистики.

Человек со всеми аккаунтами — один владелец (твинки): войс, настройки и минуты — на основной.
"""

import asyncio
import time
import traceback

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.rsn._rsn_levels import ISOLATION_OVERWRITE, MUTE_OVERWRITE

VOICE_ID = 1150378018771050537      # канал-вход
CATEGORY_ID = 1002923440644304926   # категория личных войсов

# Роли, которым в личные войсы нельзя (ID живого сервера). Роли наказания РСН 2.0 («Мут»,
# «Изоляция») добавляются сами из настроек РСН — с теми же запретами, что ставит /rsn_setup.
FORBIDDEN_ROLE_IDS = [1550578606521188502, 1168293205624766524, 1234872836691067063]

DEFAULT_NAME = "{name}'s Room"
MAX_NAME_LENGTH = 100   # предел Discord для названия канала
MAX_USER_LIMIT = 99     # предел Discord для лимита войса

# Discord разрешает менять название канала 2 раза за 10 минут. Третья попытка не падает —
# discord.py молча ждёт, пока Discord разрешит (до 10 минут). Поэтому считаем сами и
# ограничиваем ожидание, чтобы человек не смотрел на «думает…».
RENAME_LIMIT = 2
RENAME_WINDOW = 10 * 60
EDIT_TIMEOUT = 15

NO_VOICE_TEXT = f"У вас сейчас нет личного войса. Зайдите в <#{VOICE_ID}> — бот создаст его."

# «Кто сейчас в канале» (channel.members) discord.py берёт из своего кэша, а кэш обновляют события
# шлюза — они приходят отдельно от ответов на HTTP-запросы и позже них. move_to возвращается,
# когда Discord принял перенос, но событие «человек в войсе» ещё в пути — в этот момент войс
# по кэшу пуст. Кейс 2026-11-14: бот создал войс, перенёс Берджа и тут же удалил «опустевший».
# Поэтому свежий войс первые FRESH_SECONDS цикл не трогает — кэш успевает догнать.
FRESH_SECONDS = 30
# Новый канал попадает в кэш только с событием «канал создан» (create_voice_channel его туда не
# кладёт). Перенос до этого события discord.py не сопоставит с каналом и решит, что человек вышел
# из голоса совсем. Поэтому перед переносом ждём, пока канал появится в кэше.
CACHE_WAIT_SECONDS = 5


def is_fresh(voice: dict) -> bool:
    return time.time() - voice["created_at"] < FRESH_SECONDS


def humans(channel) -> list:
    """Участники войса без ботов: войс «пустой», когда людей нет, даже если там сидит музыкальный бот."""
    return [member for member in channel.members if not member.bot]


def default_name(member) -> str:
    return DEFAULT_NAME.format(name=member.display_name)[:MAX_NAME_LENGTH]


def describe_settings(name: str | None, user_limit: int, locked: bool, allowed_count: int) -> str:
    lines = [
        f"Название: **{name}**" if name else "Название: стандартное («Имя's Room»)",
        f"Лимит: **{user_limit}**" if user_limit else "Лимит: без ограничения",
        f"Закрыт — пускает только допущенных ({allowed_count})" if locked else "Открыт для всех",
    ]
    return "\n".join(lines)


class VoiceManager(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self._creating: set[int] = set()         # кому прямо сейчас создаётся войс (двойной заход)
        self._renames: dict[int, list[float]] = {}  # канал → когда бот его переименовывал

    async def cog_load(self):
        self.voice_tick.start()

    async def cog_unload(self):
        self.voice_tick.cancel()

    # ---------- кто чей ----------

    def _person(self, user):
        """Человек в общей базе (по твинку — основной); нет записи — создаётся."""
        return self.bot.db_ensure_user(user)

    def _accounts(self, user_id: int) -> set[int]:
        return set(self.db.get_discord_ids(user_id))

    def _live_channel(self, guild, user_id: int):
        """Живой войс человека или None. Канал, которого уже нет, отмечается удалённым."""
        live = self.db.get_live_personal_voice(user_id)
        if live is None or guild is None:
            return None
        channel = guild.get_channel(live["channel_id"])
        if channel is None:
            if is_fresh(live):
                return None  # только что создан и ещё не в кэше — не считать удалённым
            self.db.mark_personal_voice_deleted(live["channel_id"])
            return self._live_channel(guild, user_id)
        return channel

    # ---------- права ----------

    def _rsn_overwrites(self) -> list[tuple]:
        """Роли наказания РСН и их запреты; РСН выключен или роли не настроены — пусто."""
        rsn = self.bot.get_cog("RsnCog")
        if rsn is None:
            return []
        pairs = []
        for key, overwrite in (("mute_role_id", MUTE_OVERWRITE), ("isolation_role_id", ISOLATION_OVERWRITE)):
            role = rsn._role(key)
            if role is not None:
                pairs.append((role, overwrite))
        return pairs

    def _overwrites(self, guild, member, owner_id: int, settings: dict | None) -> dict:
        locked = bool(settings and settings["locked"])
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=not locked)}

        for role_id in FORBIDDEN_ROLE_IDS:
            role = guild.get_role(role_id)
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(view_channel=False, connect=False)

        # Роль «Мут» РСН 2.0 видит каналы, но не заходит в войс; одна из старых запрещённых —
        # та же роль, поэтому запрет РСН её перекрывает.
        for role, overwrite in self._rsn_overwrites():
            overwrites[role] = overwrite

        # Участникам лично — только connect, без view_channel. Личное разрешение в Discord сильнее
        # запрета роли в том же канале: с view_channel=True человек в «Изоляции» РСН, попавший
        # в допущенные, увидел бы войс. Видеть войс и так разрешено всем через @everyone.
        if settings:
            for allowed_id in settings["allowed"]:
                allowed = guild.get_member(allowed_id)
                if allowed is not None:
                    overwrites[allowed] = discord.PermissionOverwrite(connect=True)

        # Другие аккаунты владельца — зайти могут и в закрытый, управляет тот, с которого создан.
        for account_id in self._accounts(owner_id) - {member.id}:
            account = guild.get_member(account_id)
            if account is not None:
                overwrites[account] = discord.PermissionOverwrite(connect=True)

        overwrites[member] = discord.PermissionOverwrite(connect=True, manage_channels=True)
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
        return overwrites

    # ---------- создание и удаление ----------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if before.channel == after.channel:
            return  # включил микрофон, заглушился и т.п. — не переход

        if before.channel is not None:
            await self._cleanup(before.channel)

        if after.channel is None or member.bot:
            return
        if after.channel.id == VOICE_ID:
            await self._open_voice(member)
        else:
            await self._kick_if_punished(member, after.channel)

    async def _kick_if_punished(self, member, channel) -> None:
        """
        Замученный РСН в личном войсе — отключить. Обычно его не пускает запрет роли, но личное
        разрешение (допущен в закрытый войс до мута) этот запрет перебивает.
        """
        voice = self.db.get_personal_voice(channel.id)
        if voice is None or voice["deleted_at"] is not None:
            return
        punish_roles = {role for role, _ in self._rsn_overwrites()}
        if not punish_roles.intersection(member.roles):
            return
        try:
            await member.move_to(None, reason="РСН: наказание действует — в войс нельзя")
        except discord.HTTPException as error:
            print(f"[Войсы] Не отключить {member.id} из {channel.id}: {error}")

    async def _open_voice(self, member):
        """Зашёл в канал-вход: перенести в его живой войс, а если войса нет — создать."""
        guild = member.guild
        owner = self._person(member)

        if owner.id in self._creating:
            return  # войс уже создаётся (два захода подряд, пока ждали Discord)

        existing = self._live_channel(guild, owner.id)
        if existing is not None:
            try:
                await member.move_to(existing)
            except discord.HTTPException as error:
                print(f"[Войсы] Не перенести {member.id} в его войс {existing.id}: {error}")
            return

        self._creating.add(owner.id)
        try:
            settings = self.db.get_personal_voice_settings(owner.id)
            category = guild.get_channel(CATEGORY_ID) if CATEGORY_ID else None
            if CATEGORY_ID and category is None:
                print(f"[Войсы] Категория {CATEGORY_ID} не найдена — войс создаётся вне категорий.")

            try:
                channel = await guild.create_voice_channel(
                    name=(settings and settings["name"]) or default_name(member),
                    category=category,
                    overwrites=self._overwrites(guild, member, owner.id, settings),
                    user_limit=settings["user_limit"] if settings else 0,
                    reason=f"Личный войс {member}",
                )
            except discord.HTTPException as error:
                print(f"[Войсы] Не создать войс для {member.id}: {error}")
                return

            self.db.add_personal_voice(channel.id, owner.id, category.id if category else None)
            await self._wait_in_cache(guild, channel.id)

            try:
                await member.move_to(channel)
            except discord.HTTPException as error:
                # Перенос не удался (например, ушёл из канала-входа, пока войс создавался) — значит,
                # владельца там нет, и войс пуст наверняка: удаляем, не спрашивая отстающий кэш.
                print(f"[Войсы] Не перенести {member.id} в новый войс {channel.id}: {error}")
                await self._delete(channel, "Личный войс: не удалось перенести владельца")
        finally:
            self._creating.discard(owner.id)

    async def _wait_in_cache(self, guild, channel_id: int) -> None:
        """Дождаться события «канал создан» (не дольше CACHE_WAIT_SECONDS)."""
        if guild.get_channel(channel_id) is not None:
            return
        # Между проверкой выше и подпиской ниже нет await — событие не может проскочить посередине.
        try:
            await self.bot.wait_for(
                "guild_channel_create", check=lambda created: created.id == channel_id, timeout=CACHE_WAIT_SECONDS,
            )
        except asyncio.TimeoutError:
            print(f"[Войсы] Канал {channel_id} не появился в кэше за {CACHE_WAIT_SECONDS} с — переношу так.")

    async def _cleanup(self, channel) -> None:
        """
        Личный войс без людей — удалить. Не личный или уже удалённый — не трогать.
        Пустоту проверяет по кэшу: вызывать, когда кэш свежий (событие «вышел из войса»),
        или для войсов старше FRESH_SECONDS (цикл).
        """
        voice = self.db.get_personal_voice(channel.id)
        if voice is None or voice["deleted_at"] is not None or humans(channel):
            return
        await self._delete(channel, "Личный войс опустел")

    async def _delete(self, channel, reason: str) -> None:
        try:
            await channel.delete(reason=reason)
        except discord.NotFound:
            pass  # уже удалили
        except discord.HTTPException as error:
            print(f"[Войсы] Не удалить войс {channel.id}: {error}")
            return  # отметка остаётся — цикл попробует ещё раз

        self.db.mark_personal_voice_deleted(channel.id)
        self._renames.pop(channel.id, None)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """Войс удалили руками — помечаем, чтобы бот не считал его живым."""
        self.db.mark_personal_voice_deleted(channel.id)
        self._renames.pop(channel.id, None)

    # ---------- раз в минуту: уборка и минуты ----------

    @tasks.loop(minutes=1)
    async def voice_tick(self):
        """
        Удаляет войсы, опустевшие, пока бот лежал или пропустил событие, и считает минуты:
        каждому человеку в личном войсе +1 минута в войсе его владельца.

        Всё в try: необработанное исключение в tasks.loop останавливает цикл навсегда
        (до перезагрузки модуля), и никто этого не заметит.
        """
        try:
            await self._tick()
        except Exception:
            print(f"[Войсы] Ошибка цикла:\n{traceback.format_exc()}")

    @voice_tick.before_loop
    async def _before_tick(self):
        await self.bot.wait_until_ready()

    async def _tick(self):
        guild = self.bot.guild
        if guild is None:
            return  # сервер ещё не в кэше — не считать все войсы удалёнными

        minutes = []
        for voice in self.db.get_live_personal_voices():
            # Свежий войс: кэш мог ещё не узнать ни о канале, ни о том, что владелец внутри.
            fresh = is_fresh(voice)
            channel = guild.get_channel(voice["channel_id"])
            if channel is None:
                if not fresh:
                    self.db.mark_personal_voice_deleted(voice["channel_id"])
                continue

            present = humans(channel)
            if not present:
                if not fresh:
                    await self._cleanup(channel)
                continue

            for member in present:
                minutes.append((voice["user_id"], self._person(member).id, 1))

        # Одной транзакцией: коммит на диск ~15 мс, по коммиту на человека — бот стоял бы.
        self.db.add_personal_voice_minutes_many(minutes)

    # ---------- команды ----------

    async def _owned_voice(self, interaction: discord.Interaction):
        """(человек, его живой войс) или None — с ответом, что войса нет."""
        owner = self._person(interaction.user)
        channel = self._live_channel(interaction.guild, owner.id)
        if channel is None:
            await interaction.response.send_message(NO_VOICE_TEXT, ephemeral=True)
            return None
        return owner, channel

    def _rename_wait(self, channel_id: int) -> int:
        """Сколько секунд ждать до следующего переименования (0 — можно сейчас)."""
        now = time.time()
        recent = [moment for moment in self._renames.get(channel_id, []) if now - moment < RENAME_WINDOW]
        self._renames[channel_id] = recent
        if len(recent) < RENAME_LIMIT:
            return 0
        return int(RENAME_WINDOW - (now - recent[0])) + 1

    @app_commands.command(name="voice_name", description="Переименовать свой личный войс")
    @app_commands.describe(name="Новое название")
    async def voice_name(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, MAX_NAME_LENGTH]):
        found = await self._owned_voice(interaction)
        if found is None:
            return
        _, channel = found

        name = name.strip()
        if not name:
            await interaction.response.send_message("Название не может быть пустым.", ephemeral=True)
            return

        wait = self._rename_wait(channel.id)
        if wait:
            await interaction.response.send_message(
                f"Discord разрешает переименовывать канал только {RENAME_LIMIT} раза за 10 минут. "
                f"Попробуйте через {max(1, round(wait / 60))} мин.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await asyncio.wait_for(channel.edit(name=name, reason=f"Личный войс: {interaction.user}"), EDIT_TIMEOUT)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Discord пока не даёт переименовать: не чаще 2 раз за 10 минут "
                "(считаются и переименования в самом Discord). Попробуйте позже.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            await interaction.followup.send(f"Не получилось переименовать: {error.text or error}", ephemeral=True)
            return

        self._renames.setdefault(channel.id, []).append(time.time())
        await interaction.followup.send(
            f"Войс теперь называется **{name}**.\n-# Чтобы следующий войс назывался так же — `/voice_save`.",
            ephemeral=True,
        )

    @app_commands.command(name="voice_limit", description="Сколько человек может быть в вашем войсе (0 — без ограничения)")
    @app_commands.describe(limit="От 0 до 99; 0 — без ограничения")
    async def voice_limit(self, interaction: discord.Interaction, limit: app_commands.Range[int, 0, MAX_USER_LIMIT]):
        found = await self._owned_voice(interaction)
        if found is None:
            return
        _, channel = found

        await interaction.response.defer(ephemeral=True)
        try:
            await asyncio.wait_for(channel.edit(user_limit=limit, reason=f"Личный войс: {interaction.user}"), EDIT_TIMEOUT)
        except (asyncio.TimeoutError, discord.HTTPException):
            await interaction.followup.send("Не получилось поменять лимит — попробуйте ещё раз чуть позже.", ephemeral=True)
            return

        text = f"Лимит войса: **{limit}**." if limit else "Лимит войса снят."
        await interaction.followup.send(f"{text}\n-# Запомнить для следующих войсов — `/voice_save`.", ephemeral=True)

    @app_commands.command(name="voice_lock", description="Закрыть свой войс: зайти смогут только те, кто сейчас внутри")
    async def voice_lock(self, interaction: discord.Interaction):
        found = await self._owned_voice(interaction)
        if found is None:
            return
        _, channel = found
        guild = interaction.guild

        await interaction.response.defer(ephemeral=True)
        try:
            # Кто сейчас внутри — допущен: вышел и вернулся — пустит.
            for member in humans(channel):
                overwrite = channel.overwrites_for(member)
                if overwrite.connect is not True:
                    overwrite.update(connect=True)
                    await channel.set_permissions(member, overwrite=overwrite, reason="Личный войс закрыт")

            everyone = channel.overwrites_for(guild.default_role)
            everyone.update(view_channel=True, connect=False)
            await channel.set_permissions(guild.default_role, overwrite=everyone, reason="Личный войс закрыт")
        except discord.HTTPException as error:
            await interaction.followup.send(f"Не получилось закрыть войс: {error.text or error}", ephemeral=True)
            return

        await interaction.followup.send(
            "🔒 Войс закрыт: зайти могут только те, кто был внутри.\n"
            "-# Открыть — `/voice_unlock`. Запомнить для следующих войсов (вместе с допущенными) — `/voice_save`.",
            ephemeral=True,
        )

    @app_commands.command(name="voice_unlock", description="Открыть свой войс для всех")
    async def voice_unlock(self, interaction: discord.Interaction):
        found = await self._owned_voice(interaction)
        if found is None:
            return
        _, channel = found
        guild = interaction.guild

        await interaction.response.defer(ephemeral=True)
        try:
            everyone = channel.overwrites_for(guild.default_role)
            everyone.update(view_channel=True, connect=True)
            await channel.set_permissions(guild.default_role, overwrite=everyone, reason="Личный войс открыт")
        except discord.HTTPException as error:
            await interaction.followup.send(f"Не получилось открыть войс: {error.text or error}", ephemeral=True)
            return

        await interaction.followup.send("🔓 Войс открыт для всех.", ephemeral=True)

    async def _hand_over(self, channel, old_owner_id: int, new_member) -> None:
        """Передать войс: у прежнего владельца — без управления (зайти может), у нового — управление."""
        old_accounts = self._accounts(old_owner_id)
        for target, overwrite in list(channel.overwrites.items()):
            if target.id in old_accounts and overwrite.manage_channels:
                overwrite.update(manage_channels=None, connect=True)
                await channel.set_permissions(target, overwrite=overwrite, reason="Личный войс передан")

        overwrite = channel.overwrites_for(new_member)
        overwrite.update(connect=True, manage_channels=True)
        await channel.set_permissions(new_member, overwrite=overwrite, reason="Личный войс передан")

        self.db.set_personal_voice_owner(channel.id, self._person(new_member).id)

        try:
            await channel.send(
                f"👑 Теперь войсом управляет {new_member.mention}.",
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except discord.HTTPException:
            pass

    @app_commands.command(name="voice_transfer", description="Передать свой войс другому участнику войса")
    @app_commands.describe(member="Кому передать — он должен быть в вашем войсе")
    async def voice_transfer(self, interaction: discord.Interaction, member: discord.Member):
        found = await self._owned_voice(interaction)
        if found is None:
            return
        owner, channel = found

        if member.bot:
            await interaction.response.send_message("Боту войс не передать.", ephemeral=True)
            return
        if member.id in self._accounts(owner.id):
            await interaction.response.send_message("Это ваш же аккаунт.", ephemeral=True)
            return
        if member not in channel.members:
            await interaction.response.send_message(f"{member.mention} сейчас не в вашем войсе.", ephemeral=True)
            return
        if self._live_channel(interaction.guild, self._person(member).id) is not None:
            await interaction.response.send_message(f"У {member.mention} уже есть свой войс.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await self._hand_over(channel, owner.id, member)
        except discord.HTTPException as error:
            await interaction.followup.send(f"Не получилось передать войс: {error.text or error}", ephemeral=True)
            return

        await interaction.followup.send(f"Войс передан {member.mention}.", ephemeral=True)

    @app_commands.command(name="voice_claim", description="Забрать личный войс, из которого ушёл владелец")
    async def voice_claim(self, interaction: discord.Interaction):
        state = getattr(interaction.user, "voice", None)
        channel = getattr(state, "channel", None)
        voice = self.db.get_personal_voice(channel.id) if channel is not None else None

        if voice is None or voice["deleted_at"] is not None:
            await interaction.response.send_message("Зайдите в личный войс, который хотите забрать.", ephemeral=True)
            return

        me = self._person(interaction.user)
        if voice["user_id"] == me.id:
            await interaction.response.send_message("Это и так ваш войс.", ephemeral=True)
            return

        owner_accounts = self._accounts(voice["user_id"])
        present_owner = next((member for member in channel.members if member.id in owner_accounts), None)
        if present_owner is not None:
            await interaction.response.send_message(
                f"Владелец ({present_owner.mention}) ещё здесь — попросите передать войс: `/voice_transfer`.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        if self._live_channel(interaction.guild, me.id) is not None:
            await interaction.response.send_message("У вас уже есть свой войс.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await self._hand_over(channel, voice["user_id"], interaction.user)
        except discord.HTTPException as error:
            await interaction.followup.send(f"Не получилось забрать войс: {error.text or error}", ephemeral=True)
            return

        await interaction.followup.send("Теперь это ваш войс.", ephemeral=True)

    @app_commands.command(name="voice_save", description="Запомнить настройки своего войса для следующих")
    async def voice_save(self, interaction: discord.Interaction):
        found = await self._owned_voice(interaction)
        if found is None:
            return
        owner, channel = found
        guild = interaction.guild

        accounts = self._accounts(owner.id)
        default_names = {default_name(member) for member in map(guild.get_member, accounts) if member is not None}
        name = None if channel.name in default_names else channel.name

        locked = channel.overwrites_for(guild.default_role).connect is False
        bot_id = guild.me.id if guild.me else None
        allowed = [
            target.id for target, overwrite in channel.overwrites.items()
            if overwrite.connect is True
            and target.id not in accounts
            and target.id != bot_id
            and guild.get_member(target.id) is not None   # только участники: роли и ушедшие — нет
        ]

        self.db.save_personal_voice_settings(owner.id, name, channel.user_limit or 0, locked, allowed)
        await interaction.response.send_message(
            "✓ Настройки сохранены — следующий войс создастся с ними:\n"
            + describe_settings(name, channel.user_limit or 0, locked, len(allowed))
            + "\n-# Сохраняется и то, что вы поменяли в настройках канала в самом Discord (название, лимит).",
            ephemeral=True,
        )

    @app_commands.command(name="voice_reset", description="Забыть сохранённые настройки войса")
    async def voice_reset(self, interaction: discord.Interaction):
        owner = self._person(interaction.user)
        if self.db.delete_personal_voice_settings(owner.id):
            text = "Сохранённые настройки забыты — следующий войс будет стандартным."
        else:
            text = "Сохранённых настроек и не было."
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot):
    await bot.add_cog(VoiceManager(bot))
