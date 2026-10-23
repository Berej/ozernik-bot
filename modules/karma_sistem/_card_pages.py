"""
Страницы карточек (,rs и ,rc): кнопки ◀ / ▶ под картинкой.

Сансара: 1 — карточка, 2 — «Где пишу», 3 — «Где сижу», 4 — «Ивенты» (карма от администрации),
5 — «Другие обличья» (все аккаунты человека; только если есть твинки).
Кубы: по 10 связей на страницу (1 — нынешняя карточка с первыми десятью).

Листать может любой, кнопки работают всегда — и после перезапуска (решение Alium).
У каждой карточки свой человек, поэтому одного постоянного меню с фиксированными
custom_id (как у вкладок ,lbc) мало: чья карточка и какая страница записаны в сам
custom_id кнопки — «karma:card:rs:<Discord ID>:<страница>:next». Это DynamicItem:
discord.py по шаблону узнаёт свою кнопку в любом сообщении, даже отправленном до
перезапуска, и собирает её из custom_id. Регистрация — bot.add_dynamic_items в cog_load.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .karma_sistem import KarmaSistem

CARD_KINDS = ('rs', 'rc')

SANSARA_PAGE_NAMES = ('Карточка', 'Где пишу', 'Где сижу', 'Ивенты', 'Другие обличья')


class CardPageButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'karma:card:(?P<card>rs|rc):(?P<user>\d+):(?P<page>\d+):(?P<direction>prev|next)',
):
    """Кнопка ◀ / ▶: открывает страницу page карточки card человека user_id."""

    def __init__(self, card: str, user_id: int, page: int, direction: str):
        super().__init__(
            discord.ui.Button(
                label='◀' if direction == 'prev' else '▶',
                style=discord.ButtonStyle.secondary,
                custom_id=f'karma:card:{card}:{user_id}:{page}:{direction}',
            )
        )
        self.card = card
        self.user_id = user_id
        self.page = page

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        return cls(match['card'], int(match['user']), int(match['page']), match['direction'])

    async def callback(self, interaction: discord.Interaction):
        cog: KarmaSistem | None = interaction.client.get_cog('KarmaSistem')

        if cog is None:  # модуль кармы выключен
            await interaction.response.send_message('Карма сейчас выключена.', ephemeral=True)
            return

        await cog.turn_card_page(interaction, self.card, self.user_id, self.page)


def page_label(card: str, page: int, pages: int) -> str:
    if card == 'rs':
        return f'{SANSARA_PAGE_NAMES[page]} · {page + 1}/{pages}'
    return f'Связи · {page + 1}/{pages}'


def card_pages_view(card: str, user_id: int, page: int, pages: int) -> discord.ui.View | None:
    """
    Кнопки под карточкой: ◀ [название · N/M] ▶. Листается по кругу.
    Одна страница — кнопок нет.
    """
    if pages <= 1:
        return None

    view = discord.ui.View(timeout=None)
    view.add_item(CardPageButton(card, user_id, (page - 1) % pages, 'prev'))
    view.add_item(discord.ui.Button(
        label=page_label(card, page, pages),
        style=discord.ButtonStyle.primary,
        custom_id=f'karma:card_label:{card}:{user_id}',
        disabled=True,
    ))
    view.add_item(CardPageButton(card, user_id, (page + 1) % pages, 'next'))
    return view
