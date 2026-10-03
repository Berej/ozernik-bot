"""
Таблицы лидеров Связи (/leaderboard_cubes, ,lbc): данные трёх вкладок и постоянное меню вкладок.
"""

from __future__ import annotations

import asyncio # noqa
import inspect
import random
import traceback # noqa
import typing # noqa
import ast
import json
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from copy import deepcopy
from io import BytesIO
from itertools import combinations
from pathlib import Path # noqa
from typing import Any # noqa
from pprint import pformat # noqa

import discord # noqa
from discord import Interaction, app_commands, Role
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont, ImageOps # noqa
from PIL.ImageFont import FreeTypeFont
from discord.ui import Item, Button, View, LayoutView # noqa

from config import config
from utilities import *

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bot import OzernikiBot

from . import _state
from ._state import *
from ._formulas import *


# ---------- Таблицы лидеров Связи (/leaderboard_cubes, ,lbc) ----------

# Уровень отдельной связи (не путать с Кубом — ступенью самого человека).
BIND_LEVEL_NAMES = {
    'black_cube': 'Чёрная',
    'white_cube': 'Белая',
    'blue_cube': 'Синяя',
    'gold_cube': 'Золотая',
}

BIND_LEVEL_EMOJI = {
    'black_cube': '⚫',
    'white_cube': '⚪',
    'blue_cube': '🔵',
    'gold_cube': '🟡',
}

def get_binds_by_user() -> tuple[dict[int, DataTypes.Ozernik], dict[int, list[int]]]:
    """Все связи, разложенные по пользователям: (озерники по ID, карма каждой его связи)."""
    ozerniks: dict[int, DataTypes.Ozernik] = {}
    bind_karmas: dict[int, list[int]] = {}

    for first, second, bind in _state.db.get_top_karmic_binds():
        for ozernik in (first, second):
            ozerniks[ozernik.id] = ozernik
            bind_karmas.setdefault(ozernik.id, []).append(bind.bind_karma)

    return ozerniks, bind_karmas

def get_pairs_leaderboard() -> list[dict]:
    """
    Сильнейшие пары: кто больше всех времени провёл в войсе друг с другом.

    :return: Словари с ключами first, second, bind, level (название уровня связи), level_emoji.
    """
    return [
        {
            'first': first,
            'second': second,
            'bind': bind,
            'level': BIND_LEVEL_NAMES[get_cube(bind.bind_karma)['tag_name']],
            'level_emoji': BIND_LEVEL_EMOJI[get_cube(bind.bind_karma)['tag_name']],
        }
        for first, second, bind in _state.db.get_top_karmic_binds()
    ]

def get_together_leaderboard() -> list[dict]:
    """
    Связь «со всеми вместе»: сумма всех связей человека.

    Это не время в войсе, а время с каждым собеседником, сложенное:
    час втроём даёт по часу с каждым из двух — всего 2 часа.

    :return: Словари с ключами ozernik, total_bind_karma, binds.
    """
    ozerniks, bind_karmas = get_binds_by_user()

    leaderboard = [
        {
            'ozernik': ozerniks[ozernik_id],
            'total_bind_karma': sum(karmas),
            'binds': len(karmas),
        }
        for ozernik_id, karmas in bind_karmas.items()
    ]

    leaderboard.sort(key=lambda row: (-row['total_bind_karma'], row['ozernik'].id))

    return leaderboard

def get_colored_leaderboard() -> list[dict]:
    """
    Цветные связи (выше Чёрной).

    Порядок (решение Alium): золотые → синие → белые → связь со всеми вместе.
    Считается точно: золотая связь — только золотая, в синие/белые не идёт.
    Люди без цветных связей не попадают.

    :return: Словари с ключами ozernik, gold, blue, white, total_bind_karma.
    """
    ozerniks, bind_karmas = get_binds_by_user()

    leaderboard = []

    for ozernik_id, karmas in bind_karmas.items():
        levels = [get_cube(karma)['tag_name'] for karma in karmas]

        row = {
            'ozernik': ozerniks[ozernik_id],
            'gold': levels.count('gold_cube'),
            'blue': levels.count('blue_cube'),
            'white': levels.count('white_cube'),
            'total_bind_karma': sum(karmas),
        }

        if row['gold'] or row['blue'] or row['white']:
            leaderboard.append(row)

    leaderboard.sort(key=lambda row: (
        -row['gold'],
        -row['blue'],
        -row['white'],
        -row['total_bind_karma'],
        row['ozernik'].id,
    ))

    return leaderboard

class BindLeaderboardView(discord.ui.View):
    """
    Вкладки таблиц Связи (/leaderboard_cubes, ,lbc): Пары · Время · Кубы.

    Постоянное меню (решение Alium): переключать может любой, кнопки работают всегда,
    в том числе после перезапуска бота. Для этого:
    - у кнопок фиксированные custom_id, а при запуске Cog регистрирует меню
      через bot.add_view — Discord присылает нажатие, бот находит обработчик по custom_id;
    - меню ничего не помнит о конкретном сообщении: вкладку берёт из custom_id
      нажатой кнопки, цвет таблицы — у нажавшего.
    """

    CUSTOM_ID_PREFIX = 'karma:bind_tabs:'

    def __init__(self, cog: "KarmaSistem", tab: str = 'pairs'):
        super().__init__(timeout=None)
        self.cog = cog

        for key, (label, _, _) in cog.BIND_TABS.items():
            active = key == tab

            # Открытая вкладка подсвечена и неактивна.
            button = discord.ui.Button(
                label=label,
                custom_id=f'{self.CUSTOM_ID_PREFIX}{key}',
                style=discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary,
                disabled=active,
            )
            button.callback = self.make_callback(key)
            self.add_item(button)

    def make_callback(self, key: str):
        async def callback(interaction: discord.Interaction):
            await interaction.response.edit_message(
                embed=self.cog.build_bind_tab(key, interaction.user, interaction.guild),
                view=BindLeaderboardView(self.cog, key),
            )

        return callback
