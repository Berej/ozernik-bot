"""
RSN Cog (Реформа Системы Наказаний)
Main module with commands and background tasks
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import config as cfg
import json
import re
import traceback
import time
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import sys

from ._rsn_db import RsnDatabase
from ._rsn_levels import level_for, MUTE_OVERWRITE, ISOLATION_OVERWRITE, plan_overwrites
from ._rsn_cases import CaseMixin, BanTermButton, RESET_POINTS_AFTER_BAN, format_minutes, parse_term

GUILD_ID = cfg.GUILD_ID

# Pagination constants
CASES_PER_PAGE = 5
MAX_REASON_LENGTH = 100
INTERACTION_TIMEOUT = 180

# ==================== INITIALIZATION: Hardcoded Configuration ====================
# Замени значения ниже на свои IDs/параметры
# ИЗМЕНЯЙ ТОЛЬКО ЭТИ ЗНАЧЕНИЯ для настройки модуля
# Роли админов/модераторов берутся из modules/rsn/rsn_data.json

# === CHANNEL IDs (берутся из modules/rsn/rsn_data.json) ===

# === SYSTEM PARAMETERS ===
SCALE_MAX = 50  # Maximum points
SCALE_THRESHOLDS = {
    "isolator": [31, 50],  # Green zone (points 31-50)
    "restricted": [11, 30],  # Yellow zone (points 11-30)
    "critical": [1, 10],  # Red zone (points 1-10)
    "ban_trigger": 0  # Permanent mute threshold (points <= 0)
}
DURATION_MULTIPLIER = {
    "isolator": 1,  # x1 duration multiplier
    "restricted": 2,  # x2 duration multiplier
    "critical": 3  # x3 duration multiplier
}
WEEKLY_POINT_GAIN = 1  # Points earned per week
RESET_POINTS_ON_RETURN = 10  # Points reset on unban

# === HELPER FUNCTION ===
def get_multiplier_for_points(points: int) -> int:
    """Множитель срока мута по очкам до списания (РСН 2.0: _rsn_levels.level_for)."""
    return level_for(points).multiplier


def record_term(record: dict) -> str:
    """Срок записи для личного дела: у записи без мута срока нет, бан без срока — навсегда."""
    if record["kind"] == "warn":
        return "без мута"
    if record["kind"] == "ban":
        return "бан " + (format_duration(record["duration_hours"]) if record["duration_hours"] else "навсегда")
    return format_duration(record["duration_hours"])


# === DURATION PARSING (minutes / hours only) ===
DURATION_PATTERN = re.compile(r"^\s*(\d+)\s*([mh])\s*$", re.IGNORECASE)
MIN_DURATION_MINUTES = 1
MAX_DURATION_MINUTES = 525600  # 1 year safety cap

DURATION_FORMAT_HINT = (
    "❌ Неверный формат длительности. Укажите минуты или часы, "
    "например `30m` или `2h` (значение должно быть положительным)."
)


def parse_duration(text: Optional[str]) -> Optional[int]:
    """
    Разобрать длительность в формате "30m" (минуты) или "2h" (часы).

    - Только целые положительные значения.
    - Только минуты (m) или часы (h), регистр не важен.
    - Возвращает количество минут или None, если формат неверный.
    """
    if not isinstance(text, str):
        return None

    match = DURATION_PATTERN.match(text)
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2).lower()

    if value <= 0:
        return None

    minutes = value if unit == "m" else value * 60
    if minutes < MIN_DURATION_MINUTES or minutes > MAX_DURATION_MINUTES:
        return None

    return minutes


def format_duration(hours: Optional[float]) -> str:
    """Сформатировать длительность (в часах) как "30м", "2ч" или "1ч 30м"."""
    if not hours:
        return "Перманент"

    minutes = int(round(hours * 60))
    if minutes < 60:
        return f"{minutes}м"

    whole_hours, rest_minutes = divmod(minutes, 60)
    if rest_minutes == 0:
        return f"{whole_hours}ч"
    return f"{whole_hours}ч {rest_minutes}м"


# ==================== END INITIALIZATION ====================
ADMIN_ROLE_ID = 725675581881974794
MOD_ROLE_ID = 779015800555176006


# === RSN CONFIG (modules/rsn/rsn_data.json) ===
RSN_CONFIG_PATH = Path(__file__).resolve().parent / "rsn_data.json"


def load_rsn_config() -> dict:
    # Прочитать modules/rsn/rsn_data.json напрямую (без внешнего config-модуля)
    try:
        with open(RSN_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("rsn_data.json must contain a JSON object")
        return data
    except FileNotFoundError:
        print(f"[RSN] Config file not found: {RSN_CONFIG_PATH}")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[RSN] Failed to parse rsn_data.json: {e}")
    except OSError as e:
        print(f"[RSN] Failed to read rsn_data.json: {e}")
    return {}


def save_rsn_config() -> None:
    # Сохранить настройки обратно в rsn_data.json
    try:
        with open(RSN_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(RSN_DATA, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"[RSN] Failed to save rsn_data.json: {e}")


RSN_DATA = load_rsn_config()

for _required_key in ("mute_role_id", "log_channel_id", "admin_notify_channel_id", "admin_role_ids", "moderator_role_ids"):
    if not RSN_DATA.get(_required_key):
        print(f"[RSN] WARNING: {_required_key} is not set in rsn_data.json")


class CaseViewPagination(discord.ui.View):
    """Pagination view for case records."""
    
    def __init__(self, records: list, user: discord.User, bot, points: int, scale_max: int, color: int, cases_per_page: int = 5):
        super().__init__(timeout=INTERACTION_TIMEOUT)
        self.records = records
        self.user = user
        self.bot = bot
        self.cases_per_page = cases_per_page
        self.current_page = 0
        self.total_pages = max(1, (len(records) + cases_per_page - 1) // cases_per_page)
        self.points = points
        self.scale_max = scale_max
        self.color = color
        
        if self.total_pages <= 1:
            self.prev_button.disabled = True
            self.next_button.disabled = True
    
    @discord.ui.button(label="◀️ Предыдущая", style=discord.ButtonStyle.grey)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            embed = await self._build_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()
    
    @discord.ui.button(label="Следующая ▶️", style=discord.ButtonStyle.grey)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            embed = await self._build_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()
    
    async def _build_embed(self) -> discord.Embed:
        """Build embed for current page."""
        start_idx = self.current_page * self.cases_per_page
        end_idx = start_idx + self.cases_per_page
        page_records = self.records[start_idx:end_idx]
        
        violations = []
        for record in page_records:
            reason = record["reason"][:MAX_REASON_LENGTH]
            if len(record["reason"]) > MAX_REASON_LENGTH:
                reason += "..."
            
            duration_str = record_term(record)
            
            created_dt = datetime.fromtimestamp(
                record['created_at'],
                tz=timezone.utc
            )
            date_str = created_dt.strftime("%d.%m.%Y %H:%M")
            
            try:
                moderator = await self.bot.fetch_user(record['moderator_id'])
                mod_name = moderator.name if moderator else f"ID:{record['moderator_id']}"
            except:
                mod_name = f"ID:{record['moderator_id']}"
            
            violation = (
                f"**Дело #{record.get('case_id') or record['id']}** — {reason} | {duration_str}\n"
                f"   Баллы: {record['points_delta']} | {mod_name} | {date_str}"
            )
            violations.append(violation)
        
        # Прогресс-бар (текстовый)
        filled = int((self.points / self.scale_max) * 20)
        progress_bar = "█" * filled + "░" * (20 - filled)
        
        embed = discord.Embed(
            title=f"Личное дело — {self.user.name}",
            description=f"```\n{progress_bar}\n```\nБаллы: {self.points}/{self.scale_max}\n\nСтраница {self.current_page + 1}/{self.total_pages} | Всего дел: {len(self.records)}",
            color=self.color
        )
        
        embed.add_field(
            name="Нарушения",
            value="\n".join(violations) if violations else "Нет записей",
            inline=False
        )
        
        return embed


class SetupConfirmView(discord.ui.View):
    """Подтверждение /rsn_setup: применить запреты на каналах. Нажимать может только вызвавший."""

    def __init__(self, author_id: int, plan: list):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.plan = plan

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="Применить", style=discord.ButtonStyle.danger)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=f"Ставлю запреты… ({len(self.plan)})", view=None)
        done = failed = 0
        # По одному: у Discord лимиты на правку каналов, discord.py сам выдерживает паузы.
        for channel, role, overwrite in self.plan:
            try:
                await channel.set_permissions(role, overwrite=overwrite, reason="РСН: настройка ролей наказания")
                done += 1
            except discord.HTTPException as e:
                failed += 1
                print(f"[RSN] setup: {channel} / {role}: {e}")
        result = f"✓ Готово: поставлено запретов — {done}."
        if failed:
            result += f" Не удалось — {failed} (скорее всего, у бота нет прав на эти каналы; см. консоль)."
        await interaction.edit_original_response(content=result)
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Отменено.", view=None)
        self.stop()


class RsnCog(CaseMixin, commands.Cog):
    """Система наказаний (баллы честности, дела, мут, бан). Логика дел — в _rsn_cases.CaseMixin."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = RsnDatabase()
        self.guild_id = GUILD_ID
        self.rsn_data = RSN_DATA
        self.save_rsn_config = save_rsn_config

        # Запуск фоновых задач
        self._weekly_gain_task.start()
        self._check_expired_punishments_task.start()
        self._monthly_export_reminder_task.start()

    async def cog_load(self):
        # Кнопки срока бана (0 очков) работают и после перезапуска: чей бан и срок — в custom_id.
        self.bot.add_dynamic_items(BanTermButton)

    def cog_unload(self):
        """Остановить фоновые задачи при выгрузке."""
        self._weekly_gain_task.cancel()
        self._check_expired_punishments_task.cancel()
        self._monthly_export_reminder_task.cancel()
        self.bot.remove_dynamic_items(BanTermButton)
        self.db.close()

    @commands.Cog.listener("on_ready")
    async def _cleanup_expired_on_startup(self):
        """Обработать наказания, истёкшие пока бот был выключен (максимум 1 раз).

        Модерация выполняется вручную: бот только фиксирует окончание срока,
        уведомляет модераторов и отправляет ЛС пользователю.
        """
        # Флаг для однократного выполнения за сеанс бота
        if not hasattr(self, '_startup_cleanup_done'):
            self._startup_cleanup_done = False

        if self._startup_cleanup_done:
            return

        self._startup_cleanup_done = True

        try:
            print("[RSN] Starting expired punishments cleanup on bot startup...")
            expired = self.db.get_expired_punishments()
            if not expired:
                print("[RSN] No expired punishments found on startup")
                return

            for punishment in expired:
                await self._process_expired_punishment(punishment)

            print(f"[RSN] Startup cleanup completed: {len(expired)} expired punishments processed")

        except Exception:
            print(f"[RSN] Error during startup cleanup: {traceback.format_exc()}")

    async def _send_dm(self, user_id: int, text: str):
        """Отправить ЛС пользователю (best-effort)."""
        try:
            dm_user = await self.bot.fetch_user(user_id)
            dm = await dm_user.create_dm()
            await dm.send(text)
        except Exception as dm_error:
            print(f"[RSN] Could not send DM to user {user_id}: {dm_error}")

    async def _notify_moderators(self, text: str):
        """Уведомить канал модерации/логов (best-effort)."""
        channel_id = RSN_DATA.get("admin_notify_channel_id", 0)
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
        try:
            embed = discord.Embed(
                title="⏰ Срок наказания истёк",
                description=text,
                color=0x8B0000,
                timestamp=datetime.now(timezone.utc)
            )
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[RSN] Error notifying moderators: {e}")

    # ===== Твинки =====
    # Человек со всеми своими аккаунтами — один подсудимый: личное дело, баллы и наказание
    # ведутся на основной аккаунт, роль мута ложится на все аккаунты. Какие аккаунты чьи —
    # знает общая база бота (users + user_twinks, привязка — в модуле кармы).

    def _accounts(self, user_id: int) -> tuple[int, list[int]]:
        """(основной аккаунт, все аккаунты человека — основной первым)."""
        ozernik = self.bot.db.get_user_by_discord_id(user_id)
        if ozernik is None or ozernik.discord_id is None:
            return user_id, [user_id]
        return ozernik.discord_id, self.bot.db.get_discord_ids(ozernik.id)

    def _case_owner(self, user_id: int) -> int:
        """
        ID, на который ведётся личное дело (основной аккаунт). Заодно переносит на него
        дела, заведённые на твинки до привязки.
        """
        main_id, account_ids = self._accounts(user_id)
        for account_id in account_ids:
            if account_id != main_id:
                self.db.merge_user(account_id, main_id)
        return main_id

    def _guild_members(self, user_id: int) -> list:
        """Все аккаунты человека, которые сейчас на сервере."""
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return []
        _, account_ids = self._accounts(user_id)
        return [member for member in map(guild.get_member, account_ids) if member is not None]

    def _accounts_note(self, user_id: int) -> str:
        """Строка со всеми аккаунтами человека (пусто, если аккаунт один)."""
        _, account_ids = self._accounts(user_id)
        if len(account_ids) < 2:
            return ""
        return "\nАккаунты: " + ", ".join(f"<@{account_id}>" for account_id in account_ids)

    async def _sync_twinks(self):
        """
        Дела твинков — на основной аккаунт, роль наказания — на все аккаунты замученного.
        Ловит твинки, привязанные или зашедшие на сервер уже во время мута, и перезаход.
        """
        for user_id in self.db.get_all_user_ids():
            if self._accounts(user_id)[0] != user_id:
                self._case_owner(user_id)

        for punishment in self.db.get_active_punishments("mute"):
            record = self.db.get_record(punishment["record_id"])
            role = self._punish_role(record.get("points_before") if record else None)
            if role:
                await self._give_role(punishment["user_id"], role, "Мут действует на все аккаунты человека")

    @commands.Cog.listener("on_member_join")
    async def _mute_on_join(self, member: discord.Member):
        """Замученный (или его твинк) перезашёл — роль наказания и метка 1–10 возвращаются сразу."""
        if member.guild.id != GUILD_ID or member.bot:
            return

        owner_id = self._case_owner(member.id)
        punishment = self.db.get_active_punishment(owner_id)
        if punishment and punishment["kind"] == "mute":
            record = self.db.get_record(punishment["record_id"])
            role = self._punish_role(record.get("points_before") if record else None)
            if role:
                try:
                    await member.add_roles(role, reason="Мут ещё действует")
                except discord.HTTPException as e:
                    print(f"[RSN] Failed to give mute role on join to {member.id}: {e}")

        await self._sync_marker(owner_id)

    async def _process_expired_punishment(self, punishment: dict):
        """
        Срок наказания истёк: мут — снять роли и доступ к чату дела (последний — дело закрывается
        с логом); бан — бот сам разбанивает все аккаунты, человек выходит ровно с 10 очками.
        """
        owner_id = punishment["user_id"]
        kind = punishment["kind"]
        record = await self._release(owner_id, None)
        case_id = record.get("case_id") if record else punishment.get("record_id")

        if kind == "ban":
            await self._send_dm(
                owner_id,
                f"✓ Срок вашего бана истёк — вы разбанены. Очков порядочности: {RESET_POINTS_AFTER_BAN}."
            )
        else:
            await self._send_dm(owner_id, "✓ Срок вашего мута истёк, ограничения сняты.")

        await self._log_action(
            "Истечение срока наказания",
            f"**Дело #{case_id}** | <@{owner_id}> (`{owner_id}`) | тип: {'бан' if kind == 'ban' else 'мут'}",
            owner_id,
            self.bot.user.id if self.bot.user else 0
        )

    async def _check_admin(self, interaction: discord.Interaction) -> bool:
        """Только роли из admin_role_ids (бан, разбан, настройка). Важны роли, а не тип объекта."""
        if self._is_admin(interaction.user):
            return True
        await interaction.response.send_message("❌ Только для администраторов.", ephemeral=True)
        return False

    @staticmethod
    async def _reply_error(interaction: discord.Interaction, text: str):
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def _chat_intro(self, channel, case_id: int, owner_id: int, what: str, reason: str, before: int, after: int):
        """Первое сообщение участнику в чате дела."""
        try:
            await channel.send(
                f"**Дело #{case_id}** · <@{owner_id}> — {what}.\n"
                f"Причина: {reason}\nБаллы: {before} → {after}\n"
                f"-# Здесь можно поговорить с администрацией. Переписка сохраняется в лог дела.",
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except discord.HTTPException as e:
            print(f"[RSN] Чат дела #{case_id}: {e}")

    async def _check_permissions(self, interaction: discord.Interaction) -> bool:
        """Проверить, есть ли у пользователя права администратора или модератора."""
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Ошибка: не удалось определить ваш статус на сервере.",
                ephemeral=True
            )
            return False

        user_role_ids = {role.id for role in interaction.user.roles}

        allowed_role_ids = (
            set(RSN_DATA.get("admin_role_ids", []))
            | set(RSN_DATA.get("moderator_role_ids", []))
        )

        if not allowed_role_ids:
            print(
                "[RSN] WARNING: no admin/moderator role IDs "
                "configured in rsn_data.json"
            )

        has_permission = bool(user_role_ids & allowed_role_ids)

        if not has_permission:
            await interaction.response.send_message(
                "❌ У вас нет прав на выполнение этой команды.",
                ephemeral=True
            )
            return False

        return True

    async def _log_action(
        self,
        title: str,
        description: str,
        user_id: int,
        moderator_id: int
    ):
        """Логировать действие в log_channel_id."""
        channel_id = RSN_DATA.get("log_channel_id", 0)
        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        try:
            embed = discord.Embed(
                title=title,
                description=description,
                color=0x2F3136,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text=f"Пользователь: {user_id} | Модератор: {moderator_id}")
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[RSN] Error logging action: {e}")

    # ===== Commands: Case View =====

    @app_commands.command(
        name="rsn_case_view",
        description="Просмотреть личное дело пользователя"
    )
    @app_commands.guilds(GUILD_ID)
    async def case_view(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.User] = None
    ):
        """Просмотр личного дела. Админ/модератор может смотреть кого угодно."""
        try:
            # Если user не указан, смотрим своё дело
            target_user = user or interaction.user
            # Дело ведётся на основной аккаунт: по твинку открывается дело основного.
            owner_id = self._case_owner(target_user.id)

            # Проверка: если это не свой аккаунт (с учётом твинков) и не админ/модератор - запретить
            if owner_id != self._case_owner(interaction.user.id):
                if not await self._check_permissions(interaction):
                    return

            score = self.db.get_score(owner_id)
            if not score or score['active'] == 0:
                embed = discord.Embed(
                    title=f"Личное дело — {target_user.name}",
                    description="Личного дела нет.",
                    color=0x2F3136
                )
                await interaction.response.send_message(
                    embed=embed,
                    ephemeral=True
                )
                return

            points = score['points']
            scale_max = RSN_DATA.get("scale_max", SCALE_MAX)
            
            # Определить цвет по диапазону
            if points >= 32:
                color = 0x2ECC71  # зелёный
            elif points == 31:
                color = 0xF1C40F  # жёлтый
            elif 11 <= points <= 30:
                color = 0xE67E22  # оранжевый
            elif 1 <= points <= 10:
                color = 0xE74C3C  # красный
            else:
                color = 0x8B0000  # тёмно-красный

            # Прогресс-бар (текстовый)
            filled = int((points / scale_max) * 20)
            progress_bar = "█" * filled + "░" * (20 - filled)

            embed = discord.Embed(
                title=f"Личное дело — {target_user.name}",
                description=f"```\n{progress_bar}\n```\nБаллы: {points}/{scale_max}",
                color=color
            )

            # Список нарушений
            records = self.db.get_user_records(owner_id)
            if records:
                # Использоват пагинацию при 6+ делах
                if len(records) >= 6:
                    # Создать View для пагинации
                    view = CaseViewPagination(records, target_user, self.bot, points, scale_max, color)
                    first_page_embed = await view._build_embed()
                    
                    await interaction.response.send_message(
                        embed=first_page_embed,
                        view=view,
                        ephemeral=True
                    )
                else:
                    # Для <6 дел показать все без пагинации
                    violations = []
                    for record in records:
                        try:
                            moderator = await self.bot.fetch_user(record['moderator_id'])
                            mod_name = moderator.name if moderator else f"ID:{record['moderator_id']}"
                        except:
                            mod_name = f"ID:{record['moderator_id']}"
                        
                        duration_str = record_term(record)
                        created_dt = datetime.fromtimestamp(
                            record['created_at'],
                            tz=timezone.utc
                        )
                        date_str = created_dt.strftime("%d.%m.%Y %H:%M")

                        violation = (
                            f"**Дело #{record.get('case_id') or record['id']}** — {record['reason']} | {duration_str}\n"
                            f"   Баллы: {record['points_delta']} | {mod_name} | {date_str}"
                        )
                        violations.append(violation)

                    embed.add_field(
                        name="Нарушения",
                        value="\n".join(violations),
                        inline=False
                    )

                    await interaction.response.send_message(
                        embed=embed,
                        ephemeral=True
                    )
            else:
                await interaction.response.send_message(
                    embed=embed,
                    ephemeral=True
                )

        except Exception as e:
            print(f"[RSN] Error in case_view: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ Ошибка при получении личного дела.",
                ephemeral=True
            )

    # ===== Commands: Scale Management =====

    @app_commands.command(
        name="rsn_scale_set",
        description="Установить баллы пользователю"
    )
    @app_commands.guilds(GUILD_ID)
    async def scale_set(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        points: int
    ):
        """Установить баллы вручную (0-50)."""
        try:
            if not await self._check_permissions(interaction):
                return

            # Валидация баллов
            if not 0 <= points <= 50:
                await interaction.response.send_message(
                    "❌ Баллы должны быть в диапазоне 0-50.",
                    ephemeral=True
                )
                return

            owner_id = self._case_owner(user.id)
            self.db.set_points(owner_id, points)
            self.db.set_active(owner_id, 1)
            await self._sync_marker(owner_id)

            embed = discord.Embed(
                title="✓ Баллы установлены",
                description=f"{user.mention}: **{points}** баллов",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Установка баллов",
                f"Пользователю {user.mention} установлено **{points}** баллов",
                owner_id,
                interaction.user.id
            )

        except Exception as e:
            print(f"[RSN] Error in scale_set: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ Ошибка при установке баллов.",
                ephemeral=True
            )

    @app_commands.command(
        name="rsn_scale_add",
        description="Добавить баллы пользователю"
    )
    @app_commands.guilds(GUILD_ID)
    async def scale_add(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        amount: int
    ):
        """Добавить баллы."""
        try:
            if not await self._check_permissions(interaction):
                return

            # Валидация суммы
            if amount <= 0:
                await interaction.response.send_message(
                    "❌ Сумма должна быть положительной.",
                    ephemeral=True
                )
                return

            owner_id = self._case_owner(user.id)
            score = self.db.ensure_score(owner_id)
            new_points = score['points'] + amount
            
            # Валидация диапазона [0; 50]
            if new_points < 0:
                await interaction.response.send_message(
                    f"❌ Не удалось добавить {amount} баллов: результат будет отрицательным.",
                    ephemeral=True
                )
                return
            elif new_points > 50:
                await interaction.response.send_message(
                    f"❌ Не удалось добавить {amount} баллов: максимум 50 баллов.",
                    ephemeral=True
                )
                return
            
            self.db.set_points(owner_id, new_points)
            self.db.set_active(owner_id, 1)
            await self._sync_marker(owner_id)

            embed = discord.Embed(
                title="✓ Баллы добавлены",
                description=f"{user.mention}: +{amount} (итого: **{new_points}**)",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Добавление баллов",
                f"{user.mention}: +{amount} (итого: {new_points})",
                owner_id,
                interaction.user.id
            )

        except Exception as e:
            print(f"[RSN] Error in scale_add: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ Ошибка при добавлении баллов.",
                ephemeral=True
            )

    @app_commands.command(
        name="rsn_scale_remove",
        description="Снять баллы пользователю"
    )
    @app_commands.guilds(GUILD_ID)
    async def scale_remove(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        amount: int
    ):
        """Снять баллы."""
        try:
            if not await self._check_permissions(interaction):
                return

            # Валидация суммы
            if amount <= 0:
                await interaction.response.send_message(
                    "❌ Сумма должна быть положительной.",
                    ephemeral=True
                )
                return

            owner_id = self._case_owner(user.id)
            score = self.db.ensure_score(owner_id)
            new_points = score['points'] - amount
            
            # Валидация диапазона [0; 50]
            if new_points < 0:
                await interaction.response.send_message(
                    f"❌ Не удалось снять {amount} баллов: результат будет отрицательным.",
                    ephemeral=True
                )
                return
            
            self.db.set_points(owner_id, new_points)
            self.db.set_active(owner_id, 1)
            await self._sync_marker(owner_id)

            embed = discord.Embed(
                title="✓ Баллы сняты",
                description=f"{user.mention}: -{amount} (итого: **{new_points}**)",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Снятие баллов",
                f"{user.mention}: -{amount} (итого: {new_points})",
                owner_id,
                interaction.user.id
            )

        except Exception as e:
            print(f"[RSN] Error in scale_remove: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ Ошибка при снятии баллов.",
                ephemeral=True
            )

    # ===== Commands: Case Management =====

    def _resolve_record(self, case_id: int, user: Optional[discord.User]):
        """
        Запись участника дела по номеру дела (номер видят люди; у дела может быть несколько
        участников). :return: (запись, None) или (None, текст ошибки).
        """
        records = self.db.get_case_records(case_id)
        if not records:
            return None, f"❌ Дело #{case_id} не найдено."
        if user is not None:
            owner_id = self._case_owner(user.id)
            records = [r for r in records if r["user_id"] == owner_id]
            if not records:
                return None, f"❌ {user.mention} не участник дела #{case_id}."
        elif len({r["user_id"] for r in records}) > 1:
            return None, f"❌ В деле #{case_id} несколько участников — укажите, чью запись (`user`)."
        return records[-1], None

    @app_commands.command(
        name="rsn_case_edit",
        description="Отредактировать запись участника дела"
    )
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe(case="Номер дела", reason="Причина", points_delta="Сколько баллов снято",
                           duration="Срок мута: 30m или 2h (для мута)", user="Чья запись (если участников несколько)")
    async def case_edit(
        self,
        interaction: discord.Interaction,
        case: int,
        reason: str,
        points_delta: int,
        duration: Optional[str] = None,
        user: Optional[discord.User] = None
    ):
        """Отредактировать запись."""
        try:
            if not await self._check_permissions(interaction):
                return

            record, error = self._resolve_record(case, user)
            if error:
                await interaction.response.send_message(error, ephemeral=True)
                return

            if points_delta < 0:
                await interaction.response.send_message(
                    "❌ Баллы должны быть неотрицательным числом (≥ 0).",
                    ephemeral=True
                )
                return

            duration_hours = None
            if record['kind'] == 'mute' and duration is not None:
                total_minutes = parse_duration(duration)
                if total_minutes is None:
                    await interaction.response.send_message(DURATION_FORMAT_HINT, ephemeral=True)
                    return
                duration_hours = total_minutes / 60

            # Знак баллов по виду записи: мут и запись без мута — списание, бан — без баллов
            if record['kind'] == 'ban':
                points_delta = 0
            else:
                points_delta = -abs(points_delta)

            self.db.edit_record(
                record_id=record['id'],
                reason=reason,
                points_delta=points_delta,
                duration_hours=duration_hours
            )

            embed = discord.Embed(
                title="✓ Запись отредактирована",
                description=f"Дело #{case} · <@{record['user_id']}>",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Редактирование записи",
                f"**Дело #{case}** | <@{record['user_id']}>: запись изменена",
                record['user_id'],
                interaction.user.id
            )

        except Exception:
            print(f"[RSN] Error in case_edit: {traceback.format_exc()}")
            await self._reply_error(interaction, "❌ Ошибка при редактировании записи.")

    @app_commands.command(
        name="rsn_case_delete",
        description="Удалить запись участника из дела"
    )
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe(case="Номер дела", user="Чья запись (если участников несколько)")
    async def case_delete(
        self,
        interaction: discord.Interaction,
        case: int,
        user: Optional[discord.User] = None
    ):
        """Удалить запись. Было действующее наказание по ней — снимаются роли."""
        try:
            if not await self._check_permissions(interaction):
                return

            record, error = self._resolve_record(case, user)
            if error:
                await interaction.response.send_message(error, ephemeral=True)
                return

            await interaction.response.defer()

            owner_id = record['user_id']
            punishment = self.db.get_active_punishment(owner_id)
            was_active = bool(punishment and punishment['record_id'] == record['id'])

            self.db.delete_record(record['id'])

            if was_active:
                if punishment['kind'] == 'ban':
                    await self._unban_accounts(owner_id, "РСН: запись о бане удалена")
                else:
                    await self._remove_punish_roles(owner_id, "РСН: запись о муте удалена")
                await self._remove_from_case_chat(case, owner_id)
            await self._sync_marker(owner_id)

            if not self.db.get_case_records(case):
                await self._close_case(case, interaction.user.id)
            else:
                await self._close_case_if_done(case, interaction.user.id)

            embed = discord.Embed(
                title="✓ Запись удалена",
                description=f"Дело #{case} · <@{owner_id}>",
                color=0x2F3136
            )
            await interaction.followup.send(embed=embed)

            await self._log_action(
                "Удаление записи",
                f"**Дело #{case}** | <@{owner_id}>: запись удалена",
                owner_id,
                interaction.user.id
            )

        except Exception:
            print(f"[RSN] Error in case_delete: {traceback.format_exc()}")
            await self._reply_error(interaction, "❌ Ошибка при удалении записи.")

    @app_commands.command(
        name="rsn_mute",
        description="Мут: роль по уровню, срок ×1/×2/×3 по очкам, чат дела"
    )
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe(user="Кого", duration="Срок: 30m или 2h (умножается по уровню)", reason="Причина",
                           points="Сколько баллов снять", case="Номер открытого дела — добавить в него (необязательно)")
    async def mute(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        duration: str,
        reason: str,
        points: int,
        case: Optional[int] = None
    ):
        """
        Мут (РСН 2.0). Уровень — по очкам ДО списания: 31–50 «Мут» ×1, 11–30 «Изоляция» ×2,
        0–10 «Изоляция» ×3. Участник получает доступ к чату дела (нет чата — создаётся).
        Дошёл до 0 — мут бессрочный, админам уходит запрос срока бана.
        """
        try:
            if not await self._check_permissions(interaction):
                return

            if user.bot:
                await interaction.response.send_message("❌ Нельзя мутить ботов.", ephemeral=True)
                return

            # Дело, баллы и наказание — на основной аккаунт, роли и чат — всем аккаунтам человека
            owner_id = self._case_owner(user.id)

            active = self.db.get_active_punishment(owner_id)
            if active:
                kind = "мут" if active['kind'] == 'mute' else "бан"
                await interaction.response.send_message(f"❌ На {user.mention} уже наложен {kind}.", ephemeral=True)
                return

            total_minutes = parse_duration(duration)
            if total_minutes is None:
                await interaction.response.send_message(DURATION_FORMAT_HINT, ephemeral=True)
                return

            if points <= 0:
                await interaction.response.send_message(
                    "❌ Баллы для списания должны быть положительными (> 0).", ephemeral=True
                )
                return

            if case is not None:
                case_row = self.db.get_case(case)
                if not case_row or case_row['status'] != 'open':
                    await interaction.response.send_message(f"❌ Открытого дела #{case} нет.", ephemeral=True)
                    return

            if not self._guild_members(owner_id):
                await interaction.response.send_message(f"❌ {user.mention} не найден на сервере.", ephemeral=True)
                return

            score = self.db.ensure_score(owner_id)
            before = score['points']
            level = level_for(before)
            role = self._punish_role(before)

            if role is None:
                await interaction.response.send_message(
                    "❌ Роли наказания не настроены — администратору: `/rsn_setup`.", ephemeral=True
                )
                return

            if role >= interaction.guild.me.top_role:
                await interaction.response.send_message(
                    f"❌ Роль {role.mention} выше роли бота — бот не может её выдать.", ephemeral=True
                )
                return

            # Дальше — создание канала и роли: дольше 3 секунд, которые Discord ждёт ответа.
            await interaction.response.defer()

            if not await self._give_role(owner_id, role, reason):
                await interaction.followup.send("❌ Не удалось выдать роль. Проверьте права бота.", ephemeral=True)
                return

            actual_minutes = total_minutes * level.multiplier
            hours = actual_minutes / 60
            after = max(before - points, 0)
            self.db.set_points(owner_id, after)
            self.db.set_active(owner_id, 1)

            case_id = case if case is not None else self.db.create_case(created_by=interaction.user.id)
            record_id = self.db.create_record(
                owner_id, "mute", reason, hours, -points, interaction.user.id,
                case_id=case_id, points_before=before,
            )

            # 0 очков — мут до решения админов о бане
            expires_at = None if after <= 0 else int(time.time()) + actual_minutes * 60
            self.db.create_or_update_punishment(owner_id, "mute", expires_at, record_id)

            channel = await self._add_to_case_chat(case_id, owner_id)
            await self._sync_marker(owner_id)

            term = format_duration(hours) if expires_at else "до решения администрации"
            what = f"мут {term} (уровень {level.name}, ×{level.multiplier})"
            if channel:
                await self._chat_intro(channel, case_id, owner_id, what, reason, before, after)
            if after <= 0:
                await self._ask_ban(owner_id, case_id)

            chat_line = f"\nЧат дела: {channel.mention}" if channel else ""
            embed = discord.Embed(
                title="✓ Мут выдан",
                description=f"**Дело #{case_id}**\n{user.mention}: **{term}** (уровень {level.name}, ×{level.multiplier})\n"
                            f"Причина: {reason}\nБаллы: {before} → {after}{chat_line}{self._accounts_note(owner_id)}",
                color=0x2F3136
            )
            await interaction.followup.send(embed=embed)

            await self._log_action(
                "Выдача мута",
                f"**Дело #{case_id}** | {user.mention}: {reason}\nСрок: {term} (уровень {level.name}, ×{level.multiplier})\n"
                f"Баллы: {before} → {after}{self._accounts_note(owner_id)}",
                owner_id,
                interaction.user.id
            )

            await self._send_dm(
                owner_id,
                f"⚠️ Вам выдан мут на сервере.\n**Дело:** #{case_id}\n**Причина:** {reason}\n"
                f"**Срок:** {term}\n**Баллы:** {before} → {after}"
                + (f"\nПоговорить с администрацией можно в чате дела: {channel.mention}" if channel else "")
            )

        except Exception:
            print(f"[RSN] Error in mute: {traceback.format_exc()}")
            await self._reply_error(interaction, "❌ Ошибка при выдаче мута.")

    @app_commands.command(
        name="rsn_unmute",
        description="Снять мут досрочно"
    )
    @app_commands.guilds(GUILD_ID)
    async def unmute(
        self,
        interaction: discord.Interaction,
        user: discord.User
    ):
        """Снять мут досрочно (баллы НЕ меняются). Освобождение записывается — кто и когда."""
        try:
            if not await self._check_permissions(interaction):
                return

            owner_id = self._case_owner(user.id)
            punishment = self.db.get_active_punishment(owner_id)
            if not punishment or punishment['kind'] != 'mute':
                hint = " Он забанен — `/rsn_unban`." if punishment else ""
                await interaction.response.send_message(f"❌ У {user.mention} нет активного мута.{hint}", ephemeral=True)
                return

            await interaction.response.defer()
            record = await self._release(owner_id, interaction.user.id)
            case_id = record.get('case_id') if record else None

            embed = discord.Embed(
                title="✓ Мут снят",
                description=f"**Дело #{case_id}**\n{user.mention}: освобождён досрочно",
                color=0x2F3136
            )
            await interaction.followup.send(embed=embed)

            await self._log_action(
                "Снятие мута",
                f"**Дело #{case_id}** | {user.mention} освобождён досрочно",
                owner_id,
                interaction.user.id
            )

            await self._send_dm(owner_id, "✓ Ваш мут на сервере был снят.")

        except Exception:
            print(f"[RSN] Error in unmute: {traceback.format_exc()}")
            await self._reply_error(interaction, "❌ Ошибка при снятии мута.")

    @app_commands.command(
        name="rsn_ban",
        description="Забанить (все аккаунты человека). Только для администраторов"
    )
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe(user="Кого (можно ID)", reason="Причина",
                           duration="Срок: 12h, 7d… Пусто — навсегда")
    async def ban(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reason: str,
        duration: Optional[str] = None
    ):
        """Бан через бота: все аккаунты (твинки), по сроку бот сам разбанит, человек выйдет с 10 очками."""
        try:
            if not await self._check_admin(interaction):
                return

            if user.bot:
                await interaction.response.send_message("❌ Нельзя банить ботов.", ephemeral=True)
                return

            minutes = None
            if duration is not None:
                minutes = parse_term(duration)
                if minutes is None:
                    await interaction.response.send_message(
                        "❌ Не понял срок. Пример: `30m`, `12h`, `14d` (или пусто — навсегда).", ephemeral=True
                    )
                    return

            owner_id = self._case_owner(user.id)
            active = self.db.get_active_punishment(owner_id)
            if active and active['kind'] == 'ban':
                await interaction.response.send_message(f"❌ {user.mention} уже забанен.", ephemeral=True)
                return

            await interaction.response.defer()
            record_id = await self._ban(owner_id, minutes, reason, interaction.user.id)
            case_id = self.db.get_record(record_id)['case_id']

            text = f"**Дело #{case_id}**\n{user.mention}: бан {format_minutes(minutes)}\nПричина: {reason}{self._accounts_note(owner_id)}"
            await interaction.followup.send(embed=discord.Embed(title="🚫 Бан", description=text, color=0x8B0000))
            await self._log_action("Бан", text, owner_id, interaction.user.id)

        except Exception:
            print(f"[RSN] Error in ban: {traceback.format_exc()}")
            await self._reply_error(interaction, "❌ Ошибка при бане.")

    @app_commands.command(
        name="rsn_unban",
        description="Разбанить досрочно (все аккаунты, 10 очков). Только для администраторов"
    )
    @app_commands.guilds(GUILD_ID)
    async def unban(
        self,
        interaction: discord.Interaction,
        user: discord.User
    ):
        """Досрочный разбан: бот снимает бан со всех аккаунтов, человек выходит ровно с 10 очками."""
        try:
            if not await self._check_admin(interaction):
                return

            owner_id = self._case_owner(user.id)
            punishment = self.db.get_active_punishment(owner_id)
            if not punishment or punishment['kind'] != 'ban':
                await interaction.response.send_message(f"❌ У {user.mention} нет активного бана.", ephemeral=True)
                return

            await interaction.response.defer()
            record = await self._release(owner_id, interaction.user.id)
            case_id = record.get('case_id') if record else None

            text = f"**Дело #{case_id}**\n{user.mention}: разбанен, очков — **{RESET_POINTS_AFTER_BAN}**{self._accounts_note(owner_id)}"
            await interaction.followup.send(embed=discord.Embed(title="✓ Бан снят", description=text, color=0x2F3136))
            await self._log_action("Досрочное снятие бана", text, owner_id, interaction.user.id)
            await self._send_dm(owner_id, "✓ Ваш бан на сервере был снят.")

        except Exception:
            print(f"[RSN] Error in unban: {traceback.format_exc()}")
            await self._reply_error(interaction, "❌ Ошибка при снятии бана.")

    @app_commands.command(
        name="rsn_case",
        description="Дело без мута: запись с причиной и баллами (0 — предупреждение), по желанию с чатом"
    )
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe(user="Кого", reason="Причина", points="Сколько баллов снять (0 — предупреждение)",
                           chat="Открыть чат дела для разговора (без мута)",
                           case="Номер открытого дела — добавить в него (необязательно)")
    async def case_record(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reason: str,
        points: int = 0,
        chat: bool = False,
        case: Optional[int] = None
    ):
        """Запись в дело без мута. Чат без мута закрывается командой /rsn_case_close (с логом)."""
        try:
            if not await self._check_permissions(interaction):
                return

            if user.bot:
                await interaction.response.send_message("❌ Нельзя заводить дела на ботов.", ephemeral=True)
                return

            if points < 0:
                await interaction.response.send_message("❌ Баллы — неотрицательное число.", ephemeral=True)
                return

            case_row = None
            if case is not None:
                case_row = self.db.get_case(case)
                if not case_row or case_row['status'] != 'open':
                    await interaction.response.send_message(f"❌ Открытого дела #{case} нет.", ephemeral=True)
                    return

            owner_id = self._case_owner(user.id)
            await interaction.response.defer()

            score = self.db.ensure_score(owner_id)
            before = score['points']
            after = max(before - points, 0)
            self.db.set_points(owner_id, after)
            self.db.set_active(owner_id, 1)

            case_id = case if case is not None else self.db.create_case(created_by=interaction.user.id, manual_chat=chat)
            self.db.create_record(
                owner_id, "warn", reason, None, -points, interaction.user.id,
                case_id=case_id, points_before=before,
            )

            channel = None
            if chat or (case_row and case_row['channel_id']):
                channel = await self._add_to_case_chat(case_id, owner_id)
            if channel is None and case is None:
                self.db.close_case(case_id, interaction.user.id)  # просто запись — дело сразу закрыто

            await self._sync_marker(owner_id)

            what = "предупреждение" if points == 0 else f"снято {points} баллов"
            if channel:
                await self._chat_intro(channel, case_id, owner_id, what, reason, before, after)

            active = self.db.get_active_punishment(owner_id)
            if after <= 0 < points and not (active and active['kind'] == 'ban'):
                await self._ask_ban(owner_id, case_id)

            chat_line = f"\nЧат дела: {channel.mention}" if channel else ""
            text = f"**Дело #{case_id}**\n{user.mention}: {what}\nПричина: {reason}\nБаллы: {before} → {after}{chat_line}"
            await interaction.followup.send(embed=discord.Embed(title="✓ Запись в деле", description=text, color=0x2F3136))
            await self._log_action("Дело без мута", text, owner_id, interaction.user.id)
            await self._send_dm(
                owner_id,
                f"⚠️ В ваше личное дело внесена запись.\n**Дело:** #{case_id}\n**Причина:** {reason}\n"
                f"**Баллы:** {before} → {after}" + (f"\nЧат дела: {channel.mention}" if channel else "")
            )

        except Exception:
            print(f"[RSN] Error in case_record: {traceback.format_exc()}")
            await self._reply_error(interaction, "❌ Ошибка при записи в дело.")

    @app_commands.command(
        name="rsn_case_close",
        description="Закрыть дело: лог переписки в канал логов, чат удаляется"
    )
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe(case="Номер дела")
    async def case_close(self, interaction: discord.Interaction, case: int):
        """Закрыть дело, по которому никто не наказан (обычно — чат без мута)."""
        try:
            if not await self._check_permissions(interaction):
                return

            case_row = self.db.get_case(case)
            if not case_row or case_row['status'] != 'open':
                await interaction.response.send_message(f"❌ Открытого дела #{case} нет.", ephemeral=True)
                return

            active = self.db.get_case_active_users(case, kinds=("mute",))
            if active:
                who = ", ".join(f"<@{user_id}>" for user_id in active)
                await interaction.response.send_message(
                    f"❌ По делу #{case} ещё действует наказание: {who}. Сначала снимите мут (`/rsn_unmute`).",
                    ephemeral=True
                )
                return

            await interaction.response.defer()
            await self._close_case(case, interaction.user.id)
            await interaction.followup.send(f"✓ Дело #{case} закрыто, лог — в канале логов РСН.")
            await self._log_action("Закрытие дела", f"**Дело #{case}** закрыто", 0, interaction.user.id)

        except Exception:
            print(f"[RSN] Error in case_close: {traceback.format_exc()}")
            await self._reply_error(interaction, "❌ Ошибка при закрытии дела.")

    # ===== Commands: Setup =====

    def _add_marker_to_karma(self, marker) -> str:
        """Метка 1–10 — в заблокированные роли кармы (карма знает о метке сама; решение Alium)."""
        # Без импорта: если модуль кармы не загружен, импорт создал бы его базу и файлы.
        karma_state = sys.modules.get("modules.karma_sistem._state")
        data = getattr(karma_state, "data", None)
        if data is None:
            return "⚠️ Модуль кармы не загружен — добавьте метку в заблокированные роли кармы вручную (/settings_karma)."
        ids = list(data.blocked_roles_id or [])
        if marker.id in ids:
            return "Метка уже в заблокированных ролях кармы."
        ids.append(marker.id)
        data.blocked_roles_id = ids
        return "Метка добавлена в заблокированные роли кармы — карма ей не начисляется."

    @app_commands.command(
        name="rsn_setup",
        description="Настроить роли наказания и запреты на каналах. Только для администраторов"
    )
    @app_commands.guilds(GUILD_ID)
    async def setup_roles(self, interaction: discord.Interaction):
        """
        Создаёт роли «Мут», «Изоляция», «Порицание» (если их нет) и показывает, сколько запретов
        поставит на категориях и каналах. Применяет по кнопке. Можно повторять после новых каналов.
        """
        try:
            if not await self._check_admin(interaction):
                return

            await interaction.response.defer(ephemeral=True)
            guild = interaction.guild
            created = []

            async def ensure(key: str, name: str):
                role = self._role(key)
                if role is None:
                    role = await guild.create_role(name=name, reason="РСН: роль наказания (/rsn_setup)")
                    RSN_DATA[key] = role.id
                    created.append(role.mention)
                return role

            mute = await ensure("mute_role_id", "Мут")
            isolation = await ensure("isolation_role_id", "Изоляция")
            marker = await ensure("marker_role_id", "Порицание")
            save_rsn_config()

            karma_note = self._add_marker_to_karma(marker)

            case_channels = {row["channel_id"] for row in map(dict, self.db.conn.execute(
                "SELECT channel_id FROM rsn_cases WHERE status = 'open' AND channel_id IS NOT NULL"
            ))}
            plan = plan_overwrites(
                guild.channels,
                [(mute, MUTE_OVERWRITE), (isolation, ISOLATION_OVERWRITE)],
                skip_channel_ids=case_channels,
            )

            header = (
                f"Роли: «Мут» {mute.mention} · «Изоляция» {isolation.mention} · «Порицание» {marker.mention}"
                + (f"\nСозданы: {', '.join(created)}" if created else "")
                + f"\n{karma_note}"
            )

            if not plan:
                await interaction.followup.send(header + "\n\n✓ Запреты на всех каналах уже стоят.", ephemeral=True)
                return

            channels = len({channel.id for channel, _, _ in plan})
            view = SetupConfirmView(interaction.user.id, plan)
            await interaction.followup.send(
                header + f"\n\nБудет поставлено запретов: **{len(plan)}** (каналов и категорий: **{channels}**). "
                         f"«Мут» — не писать, не реагировать, не заходить в войс; «Изоляция» — не видеть. Применить?",
                view=view,
                ephemeral=True,
            )

        except Exception:
            print(f"[RSN] Error in setup_roles: {traceback.format_exc()}")
            await self._reply_error(interaction, "❌ Ошибка настройки.")

    # ===== Commands: Manual Ban Recording =====

    @app_commands.command(
        name="rsn_ban_record",
        description="Записать бан в базу данных (бан выполняется другим ботом)"
    )
    @app_commands.guilds(GUILD_ID)
    async def ban_record(
        self,
        interaction: discord.Interaction,
        user_id: str,
        reason: str,
        duration: Optional[str] = None,
        username: Optional[str] = None
    ):
        """Записать информацию о бане в БД (по ID пользователя)."""
        try:
            if not await self._check_permissions(interaction):
                return

            # Валидация user_id (только цифры)
            if not user_id.isdigit():
                await interaction.response.send_message(
                    "❌ ID пользователя должен содержать только цифры.",
                    ephemeral=True
                )
                return

            # Преобразовать строку ID в число для БД; дело — на основной аккаунт человека
            user_id_int = self._case_owner(int(user_id))
            accounts_note = self._accounts_note(user_id_int)
            if accounts_note:
                accounts_note += "\n**Забаньте все аккаунты.**"

            # Валидация времени наказания (минуты/часы, только положительные)
            # duration = None означает перманентное наказание
            duration_hours = None
            total_minutes = None
            if duration is not None:
                total_minutes = parse_duration(duration)
                if total_minutes is None:
                    await interaction.response.send_message(
                        DURATION_FORMAT_HINT,
                        ephemeral=True
                    )
                    return
                duration_hours = total_minutes / 60

            # Запись в БД
            self.db.ensure_score(user_id_int)
            self.db.set_active(user_id_int, 1)

            if total_minutes is not None:
                expires_at = int(time.time()) + (total_minutes * 60)
            else:
                expires_at = None

            record_id = self.db.create_record(
                user_id=user_id_int,
                kind="ban",
                reason=reason,
                duration_hours=duration_hours,
                points_delta=0,
                moderator_id=interaction.user.id
            )

            self.db.create_or_update_punishment(
                user_id=user_id_int,
                kind="ban",
                expires_at=expires_at,
                record_id=record_id
            )

            duration_str = format_duration(duration_hours)
            user_info = f"{username} ({user_id})" if username else user_id
            embed = discord.Embed(
                title="✓ Бан записан в БД",
                description=f"**Дело #{record_id}**\nПользователь: `{user_info}`\n**Длительность:** {duration_str}\nПричина: {reason}{accounts_note}",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            user_info_log = f"{username} (ID: {user_id})" if username else f"ID: {user_id}"
            
            await self._log_action(
                "Запись бана в БД",
                f"**Дело #{record_id}** | {user_info_log} | {reason}\\nДлительность: {duration_str}{accounts_note}",
                user_id_int,
                interaction.user.id
            )



            # Отправить ЛС пользователю (best-effort)
            try:
                user = await self.bot.fetch_user(user_id_int)
                dm = await user.create_dm()
                await dm.send(
                    f"🚫 На вас наложен бан на сервере.\n"
                    f"**Дело:** #{record_id}\n"
                    f"**Причина:** {reason}\n"
                    f"**Длительность:** {duration_str}"
                )
            except Exception as dm_error:
                print(f"[RSN] Could not send DM to user {user_id_int}: {dm_error}")

        except Exception as e:
            print(f"[RSN] Error in ban_record: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ Ошибка при записи бана в БД.",
                ephemeral=True
            )

    # ===== Commands: Export =====

    @app_commands.command(
        name="rsn_export",
        description="Экспортировать записи за последние 35 дней"
    )
    @app_commands.guilds(GUILD_ID)
    async def export_records(self, interaction: discord.Interaction):
        """Экспортировать записи в CSV за последние 35 дней."""
        try:
            if not await self._check_permissions(interaction):
                return

            await interaction.response.defer(ephemeral=True)

            # Получить записи за последние 35 дней (используя время Москвы)
            now = datetime.now(timezone(timedelta(hours=3)))
            days_ago = now - timedelta(days=35)
            timestamp_ago = int(days_ago.timestamp())

            # Получить все записи и отфильтровать по времени
            records = self.db.get_all_records()
            records = [r for r in records if r['created_at'] >= timestamp_ago]

            if not records:
                await interaction.followup.send(
                    f"❌ Нет записей за последние 35 дней",
                    ephemeral=True
                )
                return

            # Формировать CSV-текст
            import csv
            import io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "ID", "Case", "User ID", "Kind", "Reason", "Duration (h)",
                "Points Delta", "Moderator ID", "Created At"
            ])

            for r in records:
                created_dt = datetime.fromtimestamp(
                    r['created_at'],
                    tz=timezone.utc
                )
                writer.writerow([
                    r['id'],
                    r.get('case_id'),
                    r['user_id'],
                    r['kind'],
                    r['reason'],
                    r['duration_hours'] if r['duration_hours'] else "NULL",
                    r['points_delta'],
                    r['moderator_id'],
                    created_dt.isoformat()
                ])

            csv_data = output.getvalue()
            now_str = now.strftime("%Y_%m_%d_%H%M")
            filename = f"rsn_export_{now_str}.csv"

            # Отправить файл в export_channel_id
            export_channel_id = RSN_DATA.get("export_channel_id", 0)
            if export_channel_id:
                export_channel = self.bot.get_channel(export_channel_id)
                if export_channel:
                    file = discord.File(
                        io.BytesIO(csv_data.encode('utf-8')),
                        filename=filename
                    )
                    await export_channel.send(file=file)

            embed = discord.Embed(
                title="✓ Экспорт выполнен",
                description=f"**{len(records)}** записей за последние 35 дней",
                color=0x2F3136
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            await self._log_action(
                "Экспорт данных",
                f"Экспортировано {len(records)} записей за последние 35 дней",
                interaction.user.id,
                interaction.user.id
            )

        except Exception as e:
            print(f"[RSN] Error in export_records: {traceback.format_exc()}")
            await interaction.followup.send(
                "❌ Ошибка при экспорте.",
                ephemeral=True
            )

    # ===== Background Tasks =====

    @tasks.loop(hours=1)
    async def _weekly_gain_task(self):
        """Еженедельное начисление баллов (понедельник 00:00 МСК).
        
        Проверяется каждый час. При нахождении понедельника в 00:00-01:00 МСК
        и отсутствии выполнения на текущей неделе - выполняет начисление.
        """
        try:
            # Получить текущее время в МСК (UTC+3)
            moscow_tz = timezone(timedelta(hours=3))
            now = datetime.now(moscow_tz)
            
            # Проверить, что это понедельник (weekday() == 0) в часе 00
            if now.weekday() != 0 or now.hour != 0:
                return
            
            # Получить timestamp последнего выполнения
            last_gain_ts = RSN_DATA.get("last_weekly_gain_timestamp", 0)
            last_gain_dt = datetime.fromtimestamp(last_gain_ts, tz=moscow_tz)
            
            # Вычислить начало текущего понедельника в 00:00
            week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Если последнее начисление было ранее на этой неделе, пропустить
            if last_gain_dt.date() == week_start.date():
                return
            
            # Выполнить начисление баллов
            active_users = self.db.get_all_active_users()
            # scale_max = SCALE_MAX
            # OLD: weekly_gain = WEEKLY_POINT_GAIN
            scale_max = SCALE_MAX
            weekly_gain = WEEKLY_POINT_GAIN

            guild = self.bot.get_guild(GUILD_ID)
            if not guild:
                print("[RSN] Guild not found for weekly gain task")
                return

            processed_count = 0
            for user_record in active_users:
                user_id = user_record['user_id']
                current_points = user_record['points']

                # Только если баллы < scale_max
                if current_points >= scale_max:
                    continue

                # Проверить, что пользователь на сервере (хоть одним из своих аккаунтов)
                if not self._guild_members(user_id):
                    continue

                # Начислить баллы (с защитой диапазона)
                new_points = current_points + weekly_gain
                if new_points > scale_max:
                    new_points = scale_max
                self.db.set_points(user_id, new_points)
                processed_count += 1

            # Обновить timestamp последнего выполнения
            RSN_DATA["last_weekly_gain_timestamp"] = int(now.timestamp())
            save_rsn_config()
            
            print(f"[RSN] Weekly gain executed: {processed_count} users updated at {now.strftime('%Y-%m-%d %H:%M:%S MSK')}")

        except Exception as e:
            print(f"[RSN] Error in weekly_gain_task: {traceback.format_exc()}")

    @_weekly_gain_task.before_loop
    async def before_weekly_gain_task(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=3)
    async def _check_expired_punishments_task(self):
        """Проверка истёкших наказаний каждые 3 минуты.

        Модерация выполняется вручную: бот снимает наказание только в БД,
        уведомляет модераторов и отправляет ЛС пользователю.
        Перманентные наказания (expires_at = NULL) не обрабатываются.
        """
        try:
            if not self.bot.is_ready():
                return

            expired = self.db.get_expired_punishments()
            if expired:
                print(f"[RSN] Found {len(expired)} expired punishments to process")
                for punishment in expired:
                    await self._process_expired_punishment(punishment)

            # После истёкших — чтобы не вернуть роль тому, у кого мут только что кончился
            await self._sync_twinks()

            # Метка 1–10: выдать тем, у кого ≤ 10, снять у тех, у кого стало больше (недельный +1)
            await self._sync_all_markers()

        except Exception:
            print(f"[RSN] Error in check_expired_punishments: {traceback.format_exc()}")

    @_check_expired_punishments_task.before_loop
    async def before_check_expired_task(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def _monthly_export_reminder_task(self):
        """Напоминание об экспорте 1 числа в 00:00 МСК."""
        try:
            now = datetime.now(timezone(timedelta(hours=3)))

            # Проверить, что это 1 число месяца в 00:00-01:00
            if now.day != 1 or now.hour != 0:
                return

            notify_channel_id = RSN_DATA.get("admin_notify_channel_id", 0)
            if notify_channel_id:
                notify_channel = self.bot.get_channel(notify_channel_id)
                if notify_channel:
                    embed = discord.Embed(
                        title="📋 Напоминание",
                        description="Не забудьте сделать `/rsn_export` "
                                  "за прошлый месяц!",
                        color=0x2F3136
                    )
                    try:
                        await notify_channel.send(embed=embed)
                    except:
                        pass

            print("[RSN] Monthly export reminder sent")

        except Exception as e:
            print(f"[RSN] Error in monthly_export_reminder: {traceback.format_exc()}")

    @_monthly_export_reminder_task.before_loop
    async def before_monthly_reminder_task(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    """Регистрация Cog."""
    await bot.add_cog(RsnCog(bot))
