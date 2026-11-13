"""
РСН 2.0: логика дел — роли по уровню, метка 1–10, чат дела, освобождение, закрытие, бан.

Подмешивается в RsnCog (class RsnCog(CaseMixin, commands.Cog)): методам нужны self.bot, self.db,
self.rsn_data (RSN_DATA из rsn.py) и self.save_rsn_config. Команды остаются в rsn.py.

Человек со всеми аккаунтами — один подсудимый (твинки): всё ведётся на основной аккаунт
(owner_id — его Discord ID), роли и доступ к чату — всем аккаунтам на сервере.
"""

import io
import re
import time
import traceback

import discord

from ._rsn_case_chat import (
    add_to_chat, collect_chat, format_transcript, open_case_chat, pack_attachments, remove_from_chat,
)
from ._rsn_levels import level_for, needs_marker

RESET_POINTS_AFTER_BAN = 10   # «вне зависимости от срока бана — ровно 10 очков» (Alium 2026-11-07)

BAN_TERMS = {"7d": 7 * 24 * 60, "30d": 30 * 24 * 60, "perm": None}
BAN_TERM_LABELS = {"7d": "7 дней", "30d": "30 дней", "perm": "Навсегда", "custom": "Свой срок…"}


class CaseMixin:
    # ---------- сервер и роли ----------

    def _guild(self):
        return self.bot.get_guild(self.guild_id)

    def _role(self, key: str):
        guild = self._guild()
        role_id = self.rsn_data.get(key, 0)
        return guild.get_role(role_id) if guild and role_id else None

    def _staff_roles(self) -> list:
        guild = self._guild()
        if not guild:
            return []
        ids = list(self.rsn_data.get("admin_role_ids", [])) + list(self.rsn_data.get("moderator_role_ids", []))
        return [role for role in map(guild.get_role, ids) if role is not None]

    def _is_admin(self, member) -> bool:
        admin_ids = set(self.rsn_data.get("admin_role_ids", []))
        return any(role.id in admin_ids for role in getattr(member, "roles", []))

    def _punish_role(self, points_before):
        """Роль мута по уровню: «Изоляция» для 0–30, «Мут» для 31–50 (нет «Изоляции» — «Мут»)."""
        if level_for(points_before).isolation:
            return self._role("isolation_role_id") or self._role("mute_role_id")
        return self._role("mute_role_id")

    def _punish_roles(self) -> list:
        return [role for role in (self._role("mute_role_id"), self._role("isolation_role_id")) if role]

    async def _give_role(self, owner_id: int, role, reason: str) -> int:
        """Выдать роль всем аккаунтам человека на сервере. :return: скольким выдана."""
        given = 0
        for member in self._guild_members(owner_id):
            if role in member.roles:
                given += 1
                continue
            try:
                await member.add_roles(role, reason=reason)
                given += 1
            except discord.HTTPException as error:
                print(f"[RSN] Не выдать роль {role.id} {member.id}: {error}")
        return given

    async def _remove_punish_roles(self, owner_id: int, reason: str) -> bool:
        """Снять «Мут» и «Изоляцию» со всех аккаунтов. True — если ни с кем не сорвалось."""
        ok = True
        roles = self._punish_roles()
        for member in self._guild_members(owner_id):
            present = [role for role in roles if role in member.roles]
            if not present:
                continue
            try:
                await member.remove_roles(*present, reason=reason)
            except discord.HTTPException as error:
                print(f"[RSN] Не снять роли мута с {member.id}: {error}")
                ok = False
        return ok

    async def _sync_marker(self, owner_id: int) -> None:
        """Метка уровня 1–10: есть, пока очков ≤ 10 (на всех аккаунтах), иначе снимается."""
        marker = self._role("marker_role_id")
        score = self.db.get_score(owner_id)
        if not marker or not score:
            return

        want = needs_marker(score["points"])
        for member in self._guild_members(owner_id):
            try:
                if want and marker not in member.roles:
                    await member.add_roles(marker, reason="РСН: очков порядочности ≤ 10")
                elif not want and marker in member.roles:
                    await member.remove_roles(marker, reason="РСН: очков порядочности больше 10")
            except discord.HTTPException as error:
                print(f"[RSN] Метка {member.id}: {error}")

    async def _sync_all_markers(self) -> None:
        """Раз в цикл: метка у всех, у кого ≤ 10, и снята у тех, у кого стало больше (недельный +1)."""
        marker = self._role("marker_role_id")
        if not marker:
            return

        owners = {row["user_id"] for row in self.db.get_all_scores() if needs_marker(row["points"])}
        for owner_id in owners:
            await self._sync_marker(owner_id)
        guild = self._guild()
        for member in [m for m in guild.members if marker in m.roles]:
            owner_id = self._accounts(member.id)[0]
            if owner_id not in owners:
                await self._sync_marker(owner_id)

    # ---------- чат дела ----------

    async def _get_user(self, user_id: int):
        guild = self._guild()
        member = guild.get_member(user_id) if guild else None
        if member:
            return member
        try:
            return await self.bot.fetch_user(user_id)
        except discord.HTTPException:
            return None

    async def _case_chat(self, case_id: int):
        case = self.db.get_case(case_id)
        if not case or not case["channel_id"]:
            return None
        return self.bot.get_channel(case["channel_id"])

    async def _add_to_case_chat(self, case_id: int, owner_id: int, manual: bool = False):
        """
        Дать человеку (всем аккаунтам на сервере) доступ к чату дела; нет чата — создать.
        :return: канал или None (нет сервера / не удалось создать).
        """
        guild = self._guild()
        if not guild:
            return None

        members = self._guild_members(owner_id)
        channel = await self._case_chat(case_id)
        try:
            if channel is None:
                channel = await open_case_chat(guild, case_id, self._staff_roles(), members)
                self.db.set_case_channel(case_id, channel.id)
                if manual:
                    self.db.conn.execute("UPDATE rsn_cases SET manual_chat = 1 WHERE id = ?", (case_id,))
                    self.db.conn.commit()
            else:
                for member in members:
                    await add_to_chat(channel, member, case_id)
        except discord.HTTPException as error:
            print(f"[RSN] Чат дела #{case_id}: {error}")
            return channel
        return channel

    async def _remove_from_case_chat(self, case_id: int, owner_id: int) -> None:
        channel = await self._case_chat(case_id)
        if channel is None:
            return
        _, account_ids = self._accounts(owner_id)
        for account_id in account_ids:
            user = await self._get_user(account_id)
            if user is None:
                continue
            try:
                await remove_from_chat(channel, user, case_id)
            except (discord.HTTPException, ValueError) as error:
                print(f"[RSN] Убрать {account_id} из чата дела #{case_id}: {error}")

    async def _close_case(self, case_id: int, closed_by: int | None) -> None:
        """Закрыть дело: лог переписки файлом в канал логов РСН, затем удалить чат."""
        case = self.db.get_case(case_id)
        if not case or case["status"] == "closed":
            return

        channel = await self._case_chat(case_id)
        self.db.close_case(case_id, closed_by)

        if channel is None:
            return

        try:
            log_channel = self.bot.get_channel(self.rsn_data.get("log_channel_id", 0))
            limit = channel.guild.filesize_limit
            messages, attachments = await collect_chat(channel, max_attachment_bytes=limit)
            records = self.db.get_case_records(case_id)

            ids = {r["user_id"] for r in records} | {r["moderator_id"] for r in records}
            ids |= {r["released_by"] for r in records if r.get("released_by")}
            names = {}
            for user_id in ids:
                user = await self._get_user(user_id)
                if user:
                    names[user_id] = user.display_name

            text = format_transcript(self.db.get_case(case_id), records, names, messages, closed_at=time.time())
            files = [discord.File(io.BytesIO(text.encode("utf-8")), filename=f"дело-{case_id}.txt")]

            archive = pack_attachments(attachments)
            note = ""
            if archive and len(archive) <= limit:
                files.append(discord.File(io.BytesIO(archive), filename=f"дело-{case_id}-вложения.zip"))
            elif archive:
                note = f"\nВложения ({len(archive) // 1024 // 1024} МБ) больше лимита файла — не приложены."

            if log_channel:
                await log_channel.send(f"📁 **Дело #{case_id} закрыто** — лог переписки.{note}", files=files)
            await channel.delete(reason=f"РСН: дело #{case_id} закрыто")
        except Exception:
            print(f"[RSN] Закрытие чата дела #{case_id}: {traceback.format_exc()}")

    async def _close_case_if_done(self, case_id: int, closed_by: int | None) -> None:
        """
        Дело закрывается, когда освобождён последний ЗАМУЧЕННЫЙ участник (кроме «чата без мута»).
        Бан чат не держит: забаненный его всё равно не видит (решение Alium: чат живёт, пока замучен хоть один).
        """
        case = self.db.get_case(case_id)
        if not case or case["status"] == "closed" or case["manual_chat"]:
            return
        if not self.db.get_case_active_users(case_id, kinds=("mute",)):
            await self._close_case(case_id, closed_by)

    # ---------- освобождение ----------

    async def _release(self, owner_id: int, released_by: int | None) -> dict | None:
        """
        Снять действующее наказание (мут — по сроку или досрочно; бан — с разбаном и 10 очками).
        :return: запись, по которой было наказание, или None.
        """
        punishment = self.db.get_active_punishment(owner_id)
        if not punishment:
            return None

        record = self.db.get_record(punishment["record_id"])
        self.db.delete_active_punishment(owner_id)
        self.db.release_record(punishment["record_id"], released_by)

        if punishment["kind"] == "ban":
            await self._unban_accounts(owner_id, "РСН: срок бана истёк" if released_by is None else "РСН: разбан")
            self.db.set_points(owner_id, RESET_POINTS_AFTER_BAN)
        else:
            await self._remove_punish_roles(owner_id, "РСН: мут снят")

        await self._sync_marker(owner_id)

        if record and record.get("case_id"):
            await self._remove_from_case_chat(record["case_id"], owner_id)
            await self._close_case_if_done(record["case_id"], released_by)

        return record

    # ---------- бан ----------

    async def _ban_accounts(self, owner_id: int, reason: str) -> int:
        """Забанить все аккаунты человека (по ID — работает и для ушедших). :return: сколько забанено."""
        guild = self._guild()
        if not guild:
            return 0
        _, account_ids = self._accounts(owner_id)
        banned = 0
        for account_id in account_ids:
            try:
                await guild.ban(discord.Object(id=account_id), reason=reason, delete_message_seconds=0)
                banned += 1
            except discord.HTTPException as error:
                print(f"[RSN] Не забанить {account_id}: {error}")
        return banned

    async def _unban_accounts(self, owner_id: int, reason: str) -> None:
        guild = self._guild()
        if not guild:
            return
        _, account_ids = self._accounts(owner_id)
        for account_id in account_ids:
            try:
                await guild.unban(discord.Object(id=account_id), reason=reason)
            except discord.NotFound:
                pass  # аккаунт и не был забанен
            except discord.HTTPException as error:
                print(f"[RSN] Не разбанить {account_id}: {error}")

    async def _ban(self, owner_id: int, minutes: int | None, reason: str, moderator_id: int,
                   case_id: int | None = None) -> int:
        """
        Бан через бота: запись в дело, действующее наказание «бан» (заменяет мут), все аккаунты.
        ЛС — до бана: после бана общего сервера нет, и Discord может не доставить.
        :return: номер записи.
        """
        score = self.db.ensure_score(owner_id)
        previous = self.db.get_active_punishment(owner_id)

        hours = minutes / 60 if minutes else None
        record_id = self.db.create_record(
            owner_id, "ban", reason, hours, 0, moderator_id, case_id=case_id, points_before=score["points"],
        )
        record = self.db.get_record(record_id)
        expires_at = int(time.time()) + minutes * 60 if minutes else None
        self.db.create_or_update_punishment(owner_id, "ban", expires_at, record_id)
        self.db.set_active(owner_id, 1)

        if previous:
            self.db.release_record(previous["record_id"], moderator_id)

        term = format_minutes(minutes)
        await self._send_dm(owner_id, f"🚫 Вам выдан бан на сервере ({term}).\n**Дело:** #{record['case_id']}\n**Причина:** {reason}")
        await self._ban_accounts(owner_id, f"РСН, дело #{record['case_id']}: {reason}")

        old_case = self.db.get_record(previous["record_id"])["case_id"] if previous else None
        for touched in {record["case_id"], old_case} - {None}:
            await self._remove_from_case_chat(touched, owner_id)
            await self._close_case_if_done(touched, moderator_id)

        return record_id

    async def _ask_ban(self, owner_id: int, case_id: int) -> None:
        """0 очков: спросить админов о сроке бана. До решения человек остаётся в муте."""
        channel = self.bot.get_channel(self.rsn_data.get("admin_notify_channel_id", 0))
        if not channel:
            return
        view = discord.ui.View(timeout=None)
        for term in ("7d", "30d", "perm", "custom"):
            view.add_item(BanTermButton(owner_id, case_id, term))
        embed = discord.Embed(
            title="⚠️ 0 очков порядочности",
            description=(
                f"<@{owner_id}> (`{owner_id}`) — дело #{case_id}.\n"
                f"Выберите срок бана. До решения — в муте.{self._accounts_note(owner_id)}"
            ),
            color=0x8B0000,
        )
        try:
            await channel.send(embed=embed, view=view)
        except discord.HTTPException as error:
            print(f"[RSN] Запрос бана: {error}")


def format_minutes(minutes: int | None) -> str:
    if not minutes:
        return "навсегда"
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    parts = [f"{days}д" if days else "", f"{hours}ч" if hours else "", f"{mins}м" if mins else ""]
    return " ".join(p for p in parts if p)


# ---------- Кнопки срока бана ----------
# Постоянные: в custom_id — чей бан, по какому делу и какой срок; работают и после перезапуска.

class BanTermButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"rsn:ban:(?P<user>\d+):(?P<case>\d+):(?P<term>7d|30d|perm|custom)",
):
    def __init__(self, owner_id: int, case_id: int, term: str):
        super().__init__(discord.ui.Button(
            label=BAN_TERM_LABELS[term],
            style=discord.ButtonStyle.danger if term == "perm" else discord.ButtonStyle.secondary,
            custom_id=f"rsn:ban:{owner_id}:{case_id}:{term}",
        ))
        self.owner_id, self.case_id, self.term = owner_id, case_id, term

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["user"]), int(match["case"]), match["term"])

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RsnCog")
        if cog is None:
            await interaction.response.send_message("РСН сейчас выключен.", ephemeral=True)
            return
        if not cog._is_admin(interaction.user):
            await interaction.response.send_message("Банить может только администрация.", ephemeral=True)
            return
        if self.term == "custom":
            await interaction.response.send_modal(BanTermModal(self.owner_id, self.case_id))
            return
        await interaction.response.defer()
        await finish_ban_choice(cog, interaction, self.owner_id, self.case_id, BAN_TERMS[self.term])


class BanTermModal(discord.ui.Modal, title="Срок бана"):
    term = discord.ui.TextInput(label="Срок: 30m, 12h, 7d…", placeholder="например, 14d", max_length=10)

    def __init__(self, owner_id: int, case_id: int):
        super().__init__()
        self.owner_id, self.case_id = owner_id, case_id

    async def on_submit(self, interaction: discord.Interaction):
        minutes = parse_term(str(self.term))
        if minutes is None:
            await interaction.response.send_message("Не понял срок. Пример: `30m`, `12h`, `14d`.", ephemeral=True)
            return
        cog = interaction.client.get_cog("RsnCog")
        await interaction.response.defer()
        await finish_ban_choice(cog, interaction, self.owner_id, self.case_id, minutes)


TERM_PATTERN = re.compile(r"^\s*(\d+)\s*([mhd])\s*$", re.IGNORECASE)


def parse_term(text: str) -> int | None:
    """«30m» / «12h» / «14d» → минуты (до года). Иначе None."""
    match = TERM_PATTERN.match(text or "")
    if not match:
        return None
    value = int(match.group(1))
    minutes = value * {"m": 1, "h": 60, "d": 24 * 60}[match.group(2).lower()]
    return minutes if 0 < minutes <= 365 * 24 * 60 else None


async def finish_ban_choice(cog, interaction: discord.Interaction, owner_id: int, case_id: int, minutes):
    """Админ выбрал срок: если человек ещё на нуле и не забанен — бан; кнопки гаснут."""
    punishment = cog.db.get_active_punishment(owner_id)
    if punishment and punishment["kind"] == "ban":
        text = f"Уже забанен (дело #{case_id})."
    else:
        await cog._ban(owner_id, minutes, f"0 очков порядочности (дело #{case_id})", interaction.user.id, case_id=case_id)
        text = f"🚫 <@{owner_id}> забанен: {format_minutes(minutes)} — {interaction.user.mention}"
        await cog._log_action("Бан (0 очков)", f"**Дело #{case_id}** | {text}", owner_id, interaction.user.id)

    try:
        await interaction.message.edit(content=text, view=None)
    except (discord.HTTPException, AttributeError):
        pass
