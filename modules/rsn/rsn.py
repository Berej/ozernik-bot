"""
RSN Cog (Реформа Системы Наказаний)
Main module with commands and background tasks
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import config as cfg
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
            
            duration_str = (
                f"{record['duration_hours']}h"
                if record['duration_hours']
                else "Перманент"
            )
            
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
        """Очистить истёкшие наказания при загрузке бота (максимум 1 раз).
        
        Проверяет все активные наказания и удаляет истёкшие роли мута,
        даже если бот был выключен во время наказания.
        """
        # Флаг для однократного выполнения за сеанс бота
        if not hasattr(self, '_startup_cleanup_done'):
            self._startup_cleanup_done = False
        
        if self._startup_cleanup_done:
            return
        
        self._startup_cleanup_done = True
        
        try:
            print("[RSN] Starting expired punishments cleanup on bot startup...")
            
            guild = self.bot.get_guild(GUILD_ID)
            if not guild:
                print("[RSN] Guild not found for startup cleanup")
                return
            
            # Получить все активные наказания
            cursor = self.db.conn.cursor()
            cursor.execute("SELECT * FROM rsn_active_punishments")
            all_punishments = [dict(row) for row in cursor.fetchall()]
            
            if not all_punishments:
                print("[RSN] No active punishments found")
                return
            
            now_ts = int(time.time())
            cleanup_count = 0
            
            for punishment in all_punishments:
                user_id = punishment['user_id']
                kind = punishment['kind']
                expires_at = punishment['expires_at']
                
                # Пропустить наказания без срока (перманентные) или ещё активные
                if expires_at is None or expires_at > now_ts:
                    continue
                
                print(f"[RSN] Found expired {kind} punishment for user {user_id}")
                
                # Пытаемся получить пользователя и удалить роли если это мут
                if kind == 'mute':
                    try:
                        member = guild.get_member(user_id)
                        if member:
                            mute_role_id = self.config.get_mute_role_id()
                            full_mute_role_id = self.config.get_full_mute_role_id()
                            
                            if mute_role_id:
                                mute_role = guild.get_role(mute_role_id)
                                if mute_role and mute_role in member.roles:
                                    try:
                                        await member.remove_roles(mute_role)
                                        print(f"[RSN] Removed mute role from user {user_id} during startup cleanup")
                                    except Exception as e:
                                        print(f"[RSN] Failed to remove mute role from {user_id}: {e}")
                            
                            if full_mute_role_id:
                                full_mute_role = guild.get_role(full_mute_role_id)
                                if full_mute_role and full_mute_role in member.roles:
                                    try:
                                        await member.remove_roles(full_mute_role)
                                        print(f"[RSN] Removed full mute role from user {user_id} during startup cleanup")
                                    except Exception as e:
                                        print(f"[RSN] Failed to remove full mute role from {user_id}: {e}")
                        else:
                            print(f"[RSN] Member {user_id} not found in guild for mute removal during startup")
                    except Exception as e:
                        print(f"[RSN] Error processing mute cleanup for user {user_id}: {e}")
                
                elif kind == 'ban':
                    try:
                        await guild.unban(discord.Object(id=user_id))
                        print(f"[RSN] Unbanned user {user_id} during startup cleanup")
                    except Exception as e:
                        print(f"[RSN] Failed to unban user {user_id}: {e}")
                
                # Удалить из активных наказаний
                self.db.delete_active_punishment(user_id)
                cleanup_count += 1
            
            print(f"[RSN] Startup cleanup completed: {cleanup_count} expired punishments processed")
            
        except Exception as e:
            print(f"[RSN] Error during startup cleanup: {traceback.format_exc()}")

    async def _check_permissions(self, interaction: discord.Interaction) -> bool:
        """Проверить, есть ли у пользователя права админа/модератора (по ролям)."""
        admin_role_ids = self.config.get_admin_role_ids()
        mod_role_ids = self.config.get_moderator_role_ids()
        
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Ошибка: не удалось определить вашего статуса на сервере.",
                ephemeral=True
            )
            return False

        user_role_ids = {role.id for role in interaction.user.roles}
        has_permission = bool(
            user_role_ids & set(admin_role_ids) or user_role_ids & set(mod_role_ids)
        )

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
                        
                        duration_str = (
                            f"{record['duration_hours']}h"
                            if record['duration_hours']
                            else "Перманент"
                        )
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
        duration_hours: float
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

            if duration_hours < 0:
                await interaction.response.send_message(
                    "❌ Длительность должна быть неотрицательным числом (≥ 0).",
                    ephemeral=True
                )
                return

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

            # Проверить активный мут и удалить роль если мута нет
            member = interaction.guild.get_member(user_id)
            if member:
                active_punishment = self.db.get_active_punishment(user_id)
                if not active_punishment or active_punishment['kind'] != 'mute':
                    # Нет активного мута - убрать роль
                    mute_role = interaction.guild.get_role(self.config.get_mute_role_id())
                    if mute_role and mute_role in member.roles:
                        try:
                            await member.remove_roles(mute_role)
                            print(f"[RSN] Removed mute role from user {user_id}")
                        except Exception as role_error:
                            print(f"[RSN] Failed to remove mute role: {role_error}")

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
        duration_hours: float,
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

            # Валидация времени наказания
            if duration_hours < 1.0:
                await interaction.response.send_message(
                    "❌ Минимальная длительность наказания — 1 час.",
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

            # Получить члена сервера
            member = interaction.guild.get_member(user.id)
            if not member:
                await interaction.response.send_message(
                    f"❌ {user.mention} не найден на сервере.",
                    ephemeral=True
                )
                return

            # Гарантировать запись о пользователе
            score = self.db.ensure_score(user.id)
            current_points = score['points']

            # Определить множитель по ТЕКУЩИМ баллам (перед списанием)
            multiplier = self.config.get_multiplier_for_points(current_points)

            # Итоговая длительность
            actual_duration = duration_hours * multiplier
            expires_at = int(time.time()) + (int(actual_duration * 3600))
            print(f"[RSN] Mute calculation: duration={duration_hours}h, multiplier={multiplier}, actual={actual_duration}h, expires_at={expires_at}")

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

            # Выдать роль мута
            mute_role_id = self.config.get_mute_role_id()
            if mute_role_id:
                mute_role = interaction.guild.get_role(mute_role_id)
                if mute_role:
                    await member.add_roles(mute_role, reason=reason)

            # Если множитель > 1, выдать дополнительный полный мут
            if multiplier > 1:
                full_mute_role_id = self.config.get_full_mute_role_id()
                if full_mute_role_id:
                    full_mute_role = interaction.guild.get_role(full_mute_role_id)
                    if full_mute_role:
                        await member.add_roles(full_mute_role, reason=reason)

            # Если баллы <= ban_trigger (0), выдать перманентный полный мут
            ban_trigger = self.config.get_scale_thresholds().get("ban_trigger", 0)
            if new_points <= ban_trigger:
                full_mute_role_id = self.config.get_full_mute_role_id()
                if full_mute_role_id:
                    full_mute_role = interaction.guild.get_role(full_mute_role_id)
                    if full_mute_role and full_mute_role not in member.roles:
                        await member.add_roles(full_mute_role, reason="Перманент")

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
                description=f"**Дело #{record_id}**\n{user.mention}: **{actual_duration:.1f}h** (x{multiplier})\n"
                          f"Причина: {reason}\nБаллы: {current_points} → {new_points}",
                color=0x2F3136
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)

            await self._log_action(
                "Выдача мута",
                f"**Дело #{record_id}** | {user.mention}: {reason}\nДлит: {actual_duration:.1f}h "
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
                    f"**Длительность:** {actual_duration:.1f} часов\n"
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

            member = interaction.guild.get_member(user.id)
            if not member:
                await interaction.response.send_message(
                    f"❌ {user.mention} не найден на сервере.",
                    ephemeral=True
                )
                return

            # Получить активное наказание
            punishment = self.db.get_active_punishment(user.id)
            if not punishment:
                await interaction.response.send_message(
                    f"❌ У {user.mention} нет активного мута.",
                    ephemeral=True
                )
                return

            # Снять роли
            mute_role_id = self.config.get_mute_role_id()
            full_mute_role_id = self.config.get_full_mute_role_id()

            if mute_role_id:
                mute_role = interaction.guild.get_role(mute_role_id)
                if mute_role and mute_role in member.roles:
                    await member.remove_roles(mute_role)

            if full_mute_role_id:
                full_mute_role = interaction.guild.get_role(full_mute_role_id)
                if full_mute_role and full_mute_role in member.roles:
                    await member.remove_roles(full_mute_role)

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
                f"{user.mention} освобожден",
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
        duration_hours: Optional[float] = None,
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

            # Валидация времени наказания
            if duration_hours is not None and duration_hours < 1.0:
                await interaction.response.send_message(
                    "❌ Минимальная длительность наказания — 1 час.",
                    ephemeral=True
                )
                return

            # Запись в БД
            self.db.ensure_score(user_id_int)
            self.db.set_active(user_id_int, 1)

            if duration_hours:
                expires_at = int(time.time()) + (int(duration_hours * 3600))
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

            duration_str = (
                f"{duration_hours}h" if duration_hours else "Перманент"
            )
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
                dm = await dm_user.create_dm()
                await dm.send(
                    f"🚫 На вас наложен бан на сервере.\n"
                    f"**Дело:** #{record_id}\n"
                    f"**Причина:** {reason}\n"
                    f"**Длительность:** {duration_str}"
                )
            except Exception as dm_error:
                print(f"[RSN] Could not send DM to user {user.id}: {dm_error}")

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
            scale_max = self.config.get_scale_max()
            weekly_gain = self.config.get_weekly_point_gain()

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
        """Проверка и снятие истёкших наказаний каждые 3 минуты.
        
        Примечания:
        - Проверяет только наказания с expires_at <= now
        - Перманентные наказания (expires_at = NULL) не удаляются автоматически
        - При скасании мута НЕ сбрасываются баллы
        - При разбане баллы сбрасываются на reset_points_on_return
        """
        try:
            if not self.bot.is_ready():
                return
            expired = self.db.get_expired_punishments()
            if expired:
                print(f"[RSN] Found {len(expired)} expired punishments to process")

            guild = self.bot.get_guild(GUILD_ID)
            if not guild:
                print("[RSN] Guild not found for expired punishments check")
                return

            for punishment in expired:
                user_id = punishment['user_id']
                kind = punishment['kind']

                user = self.bot.get_user(user_id)
                member = guild.get_member(user_id) if user else None

                if kind == 'mute':
                    # === СНЯТИЕ МУТА ===
                    if member:
                        mute_role_id = self.config.get_mute_role_id()
                        full_mute_role_id = self.config.get_full_mute_role_id()

                        if mute_role_id:
                            mute_role = guild.get_role(mute_role_id)
                            if mute_role and mute_role in member.roles:
                                try:
                                    await member.remove_roles(mute_role)
                                    print(f"[RSN] Removed mute role from user {user_id}")
                                except Exception as role_error:
                                    print(f"[RSN] Failed to remove mute role from {user_id}: {role_error}")

                        if full_mute_role_id:
                            full_mute_role = guild.get_role(full_mute_role_id)
                            if full_mute_role and full_mute_role in member.roles:
                                try:
                                    await member.remove_roles(full_mute_role)
                                    print(f"[RSN] Removed full mute role from user {user_id}")
                                except Exception as role_error:
                                    print(f"[RSN] Failed to remove full mute role from {user_id}: {role_error}")
                    else:
                        print(f"[RSN] Member {user_id} not found in guild for mute removal")

                    # НЕ сбрасываем баллы для мута - только удаляем наказание
                    # Отправить ЛС (best-effort)
                    if user:
                        try:
                            dm_user = await self.bot.fetch_user(user.id)
                            dm = await dm_user.create_dm()
                            await dm.send(
                                f"✓ Ваш мут на сервере автоматически снят (время истекло)."
                            )
                            print(f"[RSN] Sent unmute DM to user {user_id}")
                        except Exception as dm_error:
                            print(f"[RSN] Failed to send DM to {user_id}: {dm_error}")
                    else:
                        print(f"[RSN] User object not found for {user_id}, DM not sent")

                elif kind == 'ban':
                    # Выполнить разбан
                    try:
                        await guild.unban(discord.Object(id=user_id))
                        print(f"[RSN] Successfully unbanned user {user_id}")
                    except Exception as unban_error:
                        print(f"[RSN] Failed to unban user {user_id}: {unban_error}")

                    # Сбросить баллы на 10 (только для бана)
                    try:
                        reset_points = self.config.get_reset_points_on_return()
                        self.db.set_points(user_id, reset_points)
                        print(f"[RSN] Reset points for user {user_id} to {reset_points}")
                    except Exception as points_error:
                        print(f"[RSN] Failed to reset points for {user_id}: {points_error}")

                    # Отправить ЛС (best-effort)
                    if user:
                        try:
                            dm_user = await self.bot.fetch_user(user.id)
                            dm = await dm_user.create_dm()
                            await dm.send(
                                f"✓ Ваш бан на сервере автоматически снят.\n"
                                f"Баллы честности восстановлены: {reset_points}"
                            )
                            print(f"[RSN] Sent unban DM to user {user_id}")
                        except Exception as dm_error:
                            print(f"[RSN] Failed to send DM to {user_id}: {dm_error}")
                    else:
                        print(f"[RSN] User object not found for {user_id}, DM not sent")

                # === ФИНАЛИЗАЦИЯ ===
                # Удалить из активных наказаний
                self.db.delete_active_punishment(user_id)
                print(f"[RSN] Deleted active {kind} punishment for user {user_id}")

        except Exception as e:
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
