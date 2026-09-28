"""
RSN Cog (Реформа Системы Наказаний)
Main module with commands and background tasks
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import config as cfg
import re
import traceback
import time
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from ._rsn_db import RsnDatabase
from ._rsn_config import RsnConfig

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
    """
    Получить множитель длительности на основе текущих баллов.
    OLD: Was using self.config.get_multiplier_for_points()
    """
    if points >= SCALE_THRESHOLDS["isolator"][0]:
        return DURATION_MULTIPLIER["isolator"]
    elif points >= SCALE_THRESHOLDS["restricted"][0]:
        return DURATION_MULTIPLIER["restricted"]
    elif points > SCALE_THRESHOLDS["ban_trigger"]:
        return DURATION_MULTIPLIER["critical"]
    else:
        return DURATION_MULTIPLIER["critical"]


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
            
            duration_str = format_duration(record['duration_hours'])
            
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
                f"**Дело #{record['id']}** — {reason} | {duration_str}\n"
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


class RsnCog(commands.Cog):
    """Система наказаний (баллы честности, мут, бан)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = RsnDatabase()
        self.config = RsnConfig()
        
        # Запуск фоновых задач
        self._weekly_gain_task.start()
        self._check_expired_punishments_task.start()
        self._monthly_export_reminder_task.start()

    def cog_unload(self):
        """Остановить фоновые задачи при выгрузке."""
        self._weekly_gain_task.cancel()
        self._check_expired_punishments_task.cancel()
        self._monthly_export_reminder_task.cancel()
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
        channel_id = self.config.get_admin_notify_channel_id()
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

    async def _remove_mute_role(self, user_id: int) -> bool:
        """Снять роль мута с участника (best-effort). Возвращает True при успехе."""
        mute_role_id = self.config.get_mute_role_id()
        if not mute_role_id:
            print("[RSN] Mute role ID is not configured")
            return False

        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return False

        member = guild.get_member(user_id)
        if not member:
            print(f"[RSN] Member {user_id} not found in guild, cannot remove mute role")
            return False

        role = guild.get_role(mute_role_id)
        if not role:
            print(f"[RSN] Mute role {mute_role_id} not found on guild")
            return False

        if role not in member.roles:
            return True

        try:
            await member.remove_roles(role, reason="Срок мута истёк")
            print(f"[RSN] Removed mute role {mute_role_id} from user {user_id}")
            return True
        except Exception as e:
            print(f"[RSN] Failed to remove mute role from {user_id}: {e}")
            return False

    async def _process_expired_punishment(self, punishment: dict):
        """Обработать истёкшее наказание.

        - мут: снять роль мута (если участник на сервере) и очистить наказание;
        - бан: снять наказание в БД, сбросить баллы и уведомить модераторов
          (разбан выполняется вручную);
        - отправить ЛС пользователю и залогировать действие.
        """
        user_id = punishment['user_id']
        kind = punishment['kind']
        record_id = punishment.get('record_id')

        self.db.delete_active_punishment(user_id)

        if kind == 'ban':
            try:
                self.db.set_points(user_id, RESET_POINTS_ON_RETURN)
                print(f"[RSN] Reset points for user {user_id} to {RESET_POINTS_ON_RETURN}")
            except Exception as points_error:
                print(f"[RSN] Failed to reset points for {user_id}: {points_error}")
        else:
            # Автоматическое снятие роли мута (best-effort)
            await self._remove_mute_role(user_id)

        kind_ru = "мут" if kind == "mute" else "бан"
        print(f"[RSN] Expired {kind} punishment for user {user_id} processed")

        if kind == 'ban':
            await self._send_dm(
                user_id,
                "✓ Срок вашего бана истёк. Разбан будет выполнен модератором вручную."
            )
            await self._notify_moderators(
                f"Бан пользователя <@{user_id}> (`{user_id}`) истёк "
                f"(дело #{record_id}). Разбаньте вручную."
            )
        else:
            await self._send_dm(
                user_id,
                "✓ Срок вашего мута истёк, роль мута снята автоматически."
            )

        await self._log_action(
            "Истечение срока наказания",
            f"**Дело #{record_id}** | <@{user_id}> (`{user_id}`) | тип: {kind_ru}",
            user_id,
            self.bot.user.id if self.bot.user else 0
        )

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
            set(self.config.get_admin_role_ids())
            | set(self.config.get_moderator_role_ids())
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
        channel_id = self.config.get_log_channel_id()
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
            
            # Проверка: если это не свой аккаунт и не админ/модератор - запретить
            if target_user.id != interaction.user.id:
                if not await self._check_permissions(interaction):
                    return

            score = self.db.get_score(target_user.id)
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
            scale_max = self.config.get_scale_max()
            
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
            records = self.db.get_user_records(target_user.id)
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
                        
                        duration_str = format_duration(record['duration_hours'])
                        created_dt = datetime.fromtimestamp(
                            record['created_at'],
                            tz=timezone.utc
                        )
                        date_str = created_dt.strftime("%d.%m.%Y %H:%M")

                        violation = (
                            f"**Дело #{record['id']}** — {record['reason']} | {duration_str}\n"
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

            self.db.set_points(user.id, points)
            self.db.set_active(user.id, 1)

            embed = discord.Embed(
                title="✓ Баллы установлены",
                description=f"{user.mention}: **{points}** баллов",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Установка баллов",
                f"Пользователю {user.mention} установлено **{points}** баллов",
                user.id,
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

            score = self.db.ensure_score(user.id)
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
            
            self.db.set_points(user.id, new_points)
            self.db.set_active(user.id, 1)

            embed = discord.Embed(
                title="✓ Баллы добавлены",
                description=f"{user.mention}: +{amount} (итого: **{new_points}**)",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Добавление баллов",
                f"{user.mention}: +{amount} (итого: {new_points})",
                user.id,
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

            score = self.db.ensure_score(user.id)
            new_points = score['points'] - amount
            
            # Валидация диапазона [0; 50]
            if new_points < 0:
                await interaction.response.send_message(
                    f"❌ Не удалось снять {amount} баллов: результат будет отрицательным.",
                    ephemeral=True
                )
                return
            
            self.db.set_points(user.id, new_points)
            self.db.set_active(user.id, 1)

            embed = discord.Embed(
                title="✓ Баллы сняты",
                description=f"{user.mention}: -{amount} (итого: **{new_points}**)",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Снятие баллов",
                f"{user.mention}: -{amount} (итого: {new_points})",
                user.id,
                interaction.user.id
            )

        except Exception as e:
            print(f"[RSN] Error in scale_remove: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ Ошибка при снятии баллов.",
                ephemeral=True
            )

    # ===== Commands: Case Management =====

    @app_commands.command(
        name="rsn_case_edit",
        description="Отредактировать запись в личном деле"
    )
    @app_commands.guilds(GUILD_ID)
    async def case_edit(
        self,
        interaction: discord.Interaction,
        record_id: int,
        reason: str,
        points_delta: int,
        duration: str
    ):
        """Отредактировать запись."""
        try:
            if not await self._check_permissions(interaction):
                return

            record = self.db.get_record(record_id)
            if not record:
                await interaction.response.send_message(
                    f"❌ Запись #{record_id} не найдена.",
                    ephemeral=True
                )
                return

            # Валидация введённых данных
            if points_delta < 0:
                await interaction.response.send_message(
                    "❌ Баллы должны быть неотрицательным числом (≥ 0).",
                    ephemeral=True
                )
                return

            total_minutes = parse_duration(duration)
            if total_minutes is None:
                await interaction.response.send_message(
                    DURATION_FORMAT_HINT,
                    ephemeral=True
                )
                return
            duration_hours = total_minutes / 60

            # Обработать знак баллов в зависимости от типа нарушения
            if record['kind'] == 'mute':
                # Для мута баллы должны быть отрицательными (списание)
                points_delta = abs(points_delta) * (-1) if points_delta != 0 else 0
            elif record['kind'] == 'ban':
                # Для бана баллы всегда 0 (баллы не списываются)
                points_delta = 0

            self.db.edit_record(
                record_id=record_id,
                reason=reason,
                points_delta=points_delta,
                duration_hours=duration_hours
            )

            embed = discord.Embed(
                title="✓ Запись отредактирована",
                description=f"Запись #{record_id} обновлена",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Редактирование записи",
                f"**Дело #{record_id}** изменена",
                record['user_id'],
                interaction.user.id
            )

        except Exception as e:
            print(f"[RSN] Error in case_edit: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ Ошибка при редактировании записи.",
                ephemeral=True
            )


    @app_commands.command(
        name="rsn_case_delete",
        description="Удалить запись из личного дела"
    )
    @app_commands.guilds(GUILD_ID)
    async def case_delete(
        self,
        interaction: discord.Interaction,
        record_id: int
    ):
        """Удалить запись."""
        try:
            if not await self._check_permissions(interaction):
                return

            record = self.db.get_record(record_id)
            if not record:
                await interaction.response.send_message(
                    f"❌ Запись #{record_id} не найдена.",
                    ephemeral=True
                )
                return

            user_id = record['user_id']
            self.db.delete_record(record_id)

            # Если активного мута больше нет — снять роль мута (best-effort)
            active_punishment = self.db.get_active_punishment(user_id)
            if not active_punishment or active_punishment['kind'] != 'mute':
                await self._remove_mute_role(user_id)

            embed = discord.Embed(
                title="✓ Запись удалена",
                description=f"Запись #{record_id} удалена",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Удаление записи",
                f"**Дело #{record_id}** удалена",
                user_id,
                interaction.user.id
            )

        except Exception as e:
            print(f"[RSN] Error in case_delete: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ Ошибка при удалении записи.",
                ephemeral=True
            )

    
    @app_commands.command(
        name="rsn_mute",
        description="Выдать мут пользователю с автоматическим расчётом длительности"
    )
    @app_commands.guilds(GUILD_ID)
    async def mute(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        duration: str,
        reason: str,
        points: int
    ):
        """Выдать мут. Множитель длительности определяется по баллам ДО списания."""
        try:
            if not await self._check_permissions(interaction):
                return

            # Проверка: пользователь не должен быть ботом
            if user.bot:
                await interaction.response.send_message(
                    "❌ Нельзя мутить ботов.",
                    ephemeral=True
                )
                return

            # Проверка: у пользователя не должно быть активного мута
            active_punishment = self.db.get_active_punishment(user.id)
            if active_punishment and active_punishment['kind'] == 'mute':
                await interaction.response.send_message(
                    f"❌ На {user.mention} уже наложен мут.",
                    ephemeral=True
                )
                return

            # Валидация времени наказания (минуты/часы, только положительные)
            total_minutes = parse_duration(duration)
            if total_minutes is None:
                await interaction.response.send_message(
                    DURATION_FORMAT_HINT,
                    ephemeral=True
                )
                return

            # Валидация баллов для списания
            if points <= 0:
                await interaction.response.send_message(
                    "❌ Баллы для списания должны быть положительными (> 0).",
                    ephemeral=True
                )
                return

            # Получить участника сервера (для выдачи роли мута)
            member = interaction.guild.get_member(user.id)
            if not member:
                await interaction.response.send_message(
                    f"❌ {user.mention} не найден на сервере.",
                    ephemeral=True
                )
                return

            # Получить роль мута из конфига
            mute_role_id = self.config.get_mute_role_id()
            if not mute_role_id:
                await interaction.response.send_message(
                    "❌ Роль мута не настроена (mute_role_id в rsn_data.json).",
                    ephemeral=True
                )
                return

            mute_role = interaction.guild.get_role(mute_role_id)
            if not mute_role:
                await interaction.response.send_message(
                    f"❌ Роль мута с ID `{mute_role_id}` не найдена на сервере.",
                    ephemeral=True
                )
                return

            # Проверить иерархию ролей бота
            if mute_role >= interaction.guild.me.top_role:
                await interaction.response.send_message(
                    "❌ Не удалось выдать мут: роль мута выше роли бота.",
                    ephemeral=True
                )
                return

            # Гарантировать запись о пользователе
            score = self.db.ensure_score(user.id)
            current_points = score['points']

            # Определить множитель по ТЕКУЩИМ баллам (перед списанием)
            # OLD: multiplier = self.config.get_multiplier_for_points(current_points)
            multiplier = get_multiplier_for_points(current_points)

            # Итоговая длительность (целочисленная арифметика в минутах)
            actual_minutes = total_minutes * multiplier
            actual_duration = actual_minutes / 60
            expires_at = int(time.time()) + (actual_minutes * 60)
            print(f"[RSN] Mute calculation: duration={total_minutes}m, multiplier={multiplier}, actual={actual_minutes}m, expires_at={expires_at}")

            # Выдать роль мута (реальная модерация)
            try:
                await member.add_roles(mute_role, reason=reason)
                print(f"[RSN] Assigned mute role {mute_role_id} to user {user.id}")
            except Exception:
                print(f"[RSN] Failed to assign mute role to {user.id}: {traceback.format_exc()}")
                await interaction.response.send_message(
                    "❌ Не удалось выдать роль мута. Проверьте права бота.",
                    ephemeral=True
                )
                return

            # Списать баллы (с валидацией диапазона)
            new_points = current_points - points
            if new_points < 0:
                new_points = 0
            self.db.set_points(user.id, new_points)
            self.db.set_active(user.id, 1)

            # Создать запись
            record_id = self.db.create_record(
                user_id=user.id,
                kind="mute",
                reason=reason,
                duration_hours=actual_duration,
                points_delta=-points,
                moderator_id=interaction.user.id
            )

            # Если баллы <= ban_trigger (0), записать перманентное наказание
            ban_trigger = SCALE_THRESHOLDS.get("ban_trigger", 0)
            if new_points <= ban_trigger:
                # Перманентное наказание (expires_at = None)
                self.db.create_or_update_punishment(
                    user_id=user.id,
                    kind="mute",
                    expires_at=None,
                    record_id=record_id
                )

                # Отправить уведомление в admin_notify_channel_id
                notify_channel_id = self.config.get_admin_notify_channel_id()
                if notify_channel_id:
                    notify_channel = self.bot.get_channel(notify_channel_id)
                    if notify_channel:
                        embed = discord.Embed(
                            title="⚠️ Критический уровень",
                            description=f"{user.mention}: {new_points} ≤ {ban_trigger}",
                            color=0x8B0000
                        )
                        try:
                            await notify_channel.send(embed=embed)
                        except:
                            pass
            else:
                # Обычный таймер мута
                self.db.create_or_update_punishment(
                    user_id=user.id,
                    kind="mute",
                    expires_at=expires_at,
                    record_id=record_id
                )
                print(f"[RSN] Created mute punishment for user {user.id}: expires_at={expires_at}")

            embed = discord.Embed(
                title="✓ Мут выдан",
                description=f"**Дело #{record_id}**\n{user.mention}: **{format_duration(actual_duration)}** (x{multiplier})\n"
                          f"Причина: {reason}\nБаллы: {current_points} → {new_points}",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Выдача мута",
                f"**Дело #{record_id}** | {user.mention}: {reason}\nДлит: {format_duration(actual_duration)} "
                f"(x{multiplier})\nБаллы: {current_points} → {new_points}",
                user.id,
                interaction.user.id
            )

            # Отправить ЛС пользователю (best-effort)
            try:
                dm_user = await self.bot.fetch_user(user.id)
                dm = await dm_user.create_dm()
                await dm.send(
                    f"⚠️ Вам выдан мут на сервере.\n"
                    f"**Дело:** #{record_id}\n"
                    f"**Причина:** {reason}\n"
                    f"**Длительность:** {format_duration(actual_duration)}\n"
                    f"**Баллы:** {current_points} → {new_points}"
                )
            except Exception as dm_error:
                print(f"[RSN] Could not send DM to user {user.id}: {dm_error}")

        except Exception as e:
            print(f"[RSN] Error in mute: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ Ошибка при выдаче мута.",
                ephemeral=True
            )

    @app_commands.command(
        name="rsn_unmute",
        description="Снять мут пользователю досрочно"
    )
    @app_commands.guilds(GUILD_ID)
    async def unmute(
        self,
        interaction: discord.Interaction,
        user: discord.User
    ):
        """Снять мут досрочно (баллы НЕ меняются)."""
        try:
            if not await self._check_permissions(interaction):
                return

            # Получить активное наказание
            punishment = self.db.get_active_punishment(user.id)
            if not punishment:
                await interaction.response.send_message(
                    f"❌ У {user.mention} нет активного мута.",
                    ephemeral=True
                )
                return

            # Снять роль мута (best-effort)
            removed = await self._remove_mute_role(user.id)

            # Удалить из активных наказаний (баллы НЕ меняются)
            self.db.delete_active_punishment(user.id)

            embed = discord.Embed(
                title="✓ Мут снят",
                description=f"{user.mention}: освобожден",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Снятие мута",
                f"{user.mention} освобожден"
                + ("" if removed else " (роль мута не была снята)"),
                user.id,
                interaction.user.id
            )

            # Отправить ЛС пользователю (best-effort)
            try:
                dm_user = await self.bot.fetch_user(user.id)
                dm = await dm_user.create_dm()
                await dm.send(
                    f"✓ Ваш мут на сервере был снят."
                )
            except Exception as dm_error:
                print(f"[RSN] Could not send DM to user {user.id}: {dm_error}")

        except Exception as e:
            print(f"[RSN] Error in unmute: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ Ошибка при снятии мута.",
                ephemeral=True
            )

    @app_commands.command(
        name="rsn_unban",
        description="Зафиксировать досрочное снятие бана (вручную)"
    )
    @app_commands.guilds(GUILD_ID)
    async def unban(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reset_points: bool = True
    ):
        """Досрочное снятие бана: убрать активное наказание из БД (без действий в Discord)."""
        try:
            if not await self._check_permissions(interaction):
                return

            punishment = self.db.get_active_punishment(user.id)
            if not punishment or punishment['kind'] != 'ban':
                await interaction.response.send_message(
                    f"❌ У {user.mention} нет активного бана.",
                    ephemeral=True
                )
                return

            self.db.delete_active_punishment(user.id)

            if reset_points:
                self.db.set_points(user.id, RESET_POINTS_ON_RETURN)

            description = f"{user.mention}: освобождён"
            if reset_points:
                description += " | Баллы восстановлены: **" + str(RESET_POINTS_ON_RETURN) + "**"

            embed = discord.Embed(
                title="✓ Бан снят",
                description=description,
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Досрочное снятие бана",
                f"{user.mention} освобождён",
                user.id,
                interaction.user.id
            )

            await self._send_dm(
                user.id,
                "✓ Ваш бан на сервере был снят."
            )

        except Exception:
            print(f"[RSN] Error in unban: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ Ошибка при снятии бана.",
                ephemeral=True
            )

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

            # Преобразовать строку ID в число для БД
            user_id_int = int(user_id)

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
                description=f"**Дело #{record_id}**\nПользователь: `{user_info}`\n**Длительность:** {duration_str}\nПричина: {reason}",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            user_info_log = f"{username} (ID: {user_id})" if username else f"ID: {user_id}"
            
            await self._log_action(
                "Запись бана в БД",
                f"**Дело #{record_id}** | {user_info_log} | {reason}\\nДлительность: {duration_str}",
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
                "ID", "User ID", "Kind", "Reason", "Duration (h)",
                "Points Delta", "Moderator ID", "Created At"
            ])

            for r in records:
                created_dt = datetime.fromtimestamp(
                    r['created_at'],
                    tz=timezone.utc
                )
                writer.writerow([
                    r['id'],
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
            export_channel_id = self.config.get_export_channel_id()
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
            last_gain_ts = self.config.get_last_weekly_gain_timestamp()
            last_gain_dt = datetime.fromtimestamp(last_gain_ts, tz=moscow_tz)
            
            # Вычислить начало текущего понедельника в 00:00
            week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Если последнее начисление было ранее на этой неделе, пропустить
            if last_gain_dt.date() == week_start.date():
                return
            
            # Выполнить начисление баллов
            active_users = self.db.get_all_active_users()
            # OLD: scale_max = self.config.get_scale_max()
            # OLD: weekly_gain = self.config.get_weekly_point_gain()
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

                # Проверить, что пользователь на сервере
                member = guild.get_member(user_id)
                if not member:
                    continue

                # Начислить баллы (с защитой диапазона)
                new_points = current_points + weekly_gain
                if new_points > scale_max:
                    new_points = scale_max
                self.db.set_points(user_id, new_points)
                processed_count += 1

            # Обновить timestamp последнего выполнения
            self.config.set_last_weekly_gain_timestamp(int(now.timestamp()))
            
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
            if not expired:
                return

            print(f"[RSN] Found {len(expired)} expired punishments to process")
            for punishment in expired:
                await self._process_expired_punishment(punishment)

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

            notify_channel_id = self.config.get_admin_notify_channel_id()
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
