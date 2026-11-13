"""
Страницы карточек (,rs и ,rc): кнопки ◀ / ▶ под картинкой.

Сансара (,rs) — одна страница: «Где пишу/сижу», «Ивенты» и «Другие обличья» переехали в плашку
*i (модуль stats; решение Alium 2026-11-14). Старые кнопки ,rs открывают саму карточку.
Кубы: по 10 связей на страницу (1 — нынешняя карточка с первыми десятью).

Листать может только тот, кто вызвал карточку (решение Alium 2026-11-14), кнопки работают
всегда — и после перезапуска. У каждой карточки свой человек, поэтому одного постоянного меню
с фиксированными custom_id (как у вкладок ,lbc) мало: чья карточка, какая страница и кто вызвал
записаны в сам custom_id кнопки — «karma:card:rc:<Discord ID>:<страница>:next:<ID вызвавшего>».
Это DynamicItem: discord.py по шаблону узнаёт свою кнопку в любом сообщении, даже отправленном
до перезапуска, и собирает её из custom_id. Регистрация — bot.add_dynamic_items в cog_load.

У кнопок сообщений, отправленных до 2026-11-14, вызвавшего в custom_id нет — шаблон принимает
и их (иначе они стали бы мёртвыми, «Ошибка взаимодействия»), листать их может владелец карточки.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .karma_sistem import KarmaSistem

CARD_KINDS = ('rs', 'rc')

SANSARA_PAGE_NAMES = ('Карточка',)


class CardPageButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'karma:card:(?P<card>rs|rc):(?P<user>\d+):(?P<page>\d+):(?P<direction>prev|next)(?::(?P<invoker>\d+))?',
):
    """Кнопка ◀ / ▶: открывает страницу page карточки card человека user_id. Нажимать — только вызвавшему."""

    def __init__(self, card: str, user_id: int, page: int, direction: str, invoker_id: int | None = None):
        custom_id = f'karma:card:{card}:{user_id}:{page}:{direction}'
        if invoker_id is not None:
            custom_id += f':{invoker_id}'
        super().__init__(
            discord.ui.Button(
                label='◀' if direction == 'prev' else '▶',
                style=discord.ButtonStyle.secondary,
                custom_id=custom_id,
            )
        )
        self.card = card
        self.user_id = user_id
        self.page = page
        self.invoker_id = invoker_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        invoker = int(match['invoker']) if match['invoker'] else None
        return cls(match['card'], int(match['user']), int(match['page']), match['direction'], invoker)

    async def callback(self, interaction: discord.Interaction):
        # Старые кнопки (без вызвавшего в custom_id) — листает владелец карточки.
        allowed = self.invoker_id if self.invoker_id is not None else self.user_id
        if interaction.user.id != allowed:
            await interaction.response.send_message('Листать может только тот, кто вызвал карточку.', ephemeral=True)
            return

        cog: KarmaSistem | None = interaction.client.get_cog('KarmaSistem')

        if cog is None:  # модуль кармы выключен
            await interaction.response.send_message('Карма сейчас выключена.', ephemeral=True)
            return

        await cog.turn_card_page(interaction, self.card, self.user_id, self.page, allowed)


def page_label(card: str, page: int, pages: int) -> str:
    if card == 'rs':
        return f'{SANSARA_PAGE_NAMES[page]} · {page + 1}/{pages}'
    return f'Связи · {page + 1}/{pages}'


def card_pages_view(card: str, user_id: int, page: int, pages: int, invoker_id: int) -> discord.ui.View | None:
    """
    Кнопки под карточкой: ◀ [название · N/M] ▶. Листается по кругу, только вызвавшим (invoker_id).
    Одна страница — кнопок нет.
    """
    if pages <= 1:
        return None

    view = discord.ui.View(timeout=None)
    view.add_item(CardPageButton(card, user_id, (page - 1) % pages, 'prev', invoker_id))
    view.add_item(discord.ui.Button(
        label=page_label(card, page, pages),
        style=discord.ButtonStyle.primary,
        custom_id=f'karma:card_label:{card}:{user_id}',
        disabled=True,
    ))
    view.add_item(CardPageButton(card, user_id, (page + 1) % pages, 'next', invoker_id))
    return view
