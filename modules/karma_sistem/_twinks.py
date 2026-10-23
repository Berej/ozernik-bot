"""
Твинки: подтверждение привязки в ЛС твинка.

Человек сам запускает привязку (/twink_add) с основного аккаунта, бот пишет твинку в ЛС
с кнопками «Подтвердить» / «Отклонить» — так нельзя присвоить чужой аккаунт без согласия
его владельца. Подтверждение действует сутки и не переживает перезапуск бота — тогда
привязку нужно запустить заново.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from . import _state

if TYPE_CHECKING:
    from .karma_sistem import KarmaSistem

# Сколько живёт запрос на привязку (секунды).
TWINK_CONFIRM_TIMEOUT = 24 * 60 * 60


def twink_request_text(main: discord.abc.User, twink: discord.abc.User) -> str:
    return (
        f'**{main.display_name}** хочет привязать этот аккаунт (**{twink.display_name}**) как свой твинк.\n\n'
        f'После подтверждения:\n'
        f'- карма и связи этого аккаунта прибавятся к {main.mention};\n'
        f'- всё, что вы наберёте с этого аккаунта, будет засчитываться {main.mention};\n'
        f'- роли Сансары и Кубов у этого аккаунта будут такими же, как у основного;\n'
        f'- в канале кармы появится объявление о новом облике {main.mention}.\n\n'
        f'Отвязать твинк потом сможет только администрация.'
    )


class TwinkConfirmView(discord.ui.View):
    """Кнопки в ЛС твинка. Нажимать может только сам твинк."""

    def __init__(self, cog: "KarmaSistem", main_id: int, twink_id: int):
        super().__init__(timeout=TWINK_CONFIRM_TIMEOUT)
        self.cog = cog
        self.main_id = main_id
        self.twink_id = twink_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.twink_id

    def finish(self) -> None:
        for item in self.children:
            item.disabled = True
        self.stop()

    @discord.ui.button(label="Подтвердить", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.finish()

        result = await self.cog.confirm_twink(self.main_id, self.twink_id)

        await interaction.response.edit_message(content=result, view=self)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.finish()

        await interaction.response.edit_message(content='Привязка отклонена.', view=self)
        await self.cog.notify_user(self.main_id, f'<@{self.twink_id}> отклонил привязку как твинка.')

    async def on_timeout(self) -> None:
        self.finish()

        if self.message:
            try:
                await self.message.edit(content='Запрос на привязку истёк.', view=self)
            except discord.HTTPException:
                pass


def check_twink_request(main: discord.Member, twink: discord.Member) -> str | None:
    """
    Можно ли отправить запрос на привязку twink к main.

    :return: Причина отказа для человека или None, если можно.
    """
    db = _state.db

    if twink.bot:
        return 'Ботов привязывать нельзя.'

    if twink.id == main.id:
        return 'Это и есть ваш аккаунт.'

    if db.is_twink(main.id):
        return 'Этот аккаунт сам привязан как твинк. Привязывайте с основного аккаунта.'

    if db.is_twink(twink.id):
        return f'{twink.mention} уже привязан как твинк.'

    twink_user = db.get_user_by_discord_id(twink.id)
    if twink_user is not None and db.get_twinks(twink_user.id):
        return f'У {twink.mention} есть свои твинки — сначала их нужно отвязать.'

    return None


# ---------- Таблица всех твинков (для администрации) ----------

# Сколько владельцев твинков на одной странице /twinks_all.
TWINK_OWNERS_PER_PAGE = 15


class AllTwinksView(discord.ui.View):
    """
    Страницы таблицы всех твинков: «<@основной> → <@твинк>, <@твинк>».
    Листать может только вызвавший; через 5 минут кнопки гаснут.
    """

    def __init__(self, author_id: int, groups: list[tuple[int, list[int]]]):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.groups = groups
        self.page = 0
        self.pages = max(1, -(-len(groups) // TWINK_OWNERS_PER_PAGE))  # деление с округлением вверх
        self.update_buttons()

    def embed(self) -> discord.Embed:
        start = self.page * TWINK_OWNERS_PER_PAGE
        lines = [
            f'<@{main_id}> → ' + ', '.join(f'<@{twink_id}>' for twink_id in twink_ids)
            for main_id, twink_ids in self.groups[start:start + TWINK_OWNERS_PER_PAGE]
        ]
        twinks_total = sum(len(twink_ids) for _, twink_ids in self.groups)

        embed = discord.Embed(title='Твинки сервера', description='\n'.join(lines))
        embed.set_footer(
            text=f'Страница {self.page + 1}/{self.pages} · владельцев: {len(self.groups)} · твинков: {twinks_total}'
        )
        return embed

    def update_buttons(self) -> None:
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= self.pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def turn(self, interaction: discord.Interaction, step: int) -> None:
        self.page = min(max(self.page + step, 0), self.pages - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label='◀', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.turn(interaction, -1)

    @discord.ui.button(label='▶', style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.turn(interaction, 1)
