"""
Формулы кармы: уровни, ступени Сансары, Кубы, тексты повышений, форматирование чисел и времени.
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


def get_status(karma: DataTypes.Karma) -> dict:
    if karma.status in ("asur", "deva"):
        return _state.data.karma_roles[karma.status]

    return max(
        (
            stage
            for stage in _state.data.karma_roles.values()
            if (
                stage["required_karma"] is not None
                and karma.karma >= stage["required_karma"]
            )
        ),
        key=lambda stage: stage["required_karma"],
    )

def get_level(karma: int) -> int | None:
    if karma is None:
        return None

    if karma <= 0:
        return 0

    level = 0
    required_karma = 0
    step = 100

    while required_karma + step <= karma:
        required_karma += step
        level += 1

        if level % 10 == 0:
            step *= 2

    return level

def get_karma(level: int) -> int:
    if level <= 0:
        return 0

    each_level = 0
    required_karma = 0
    step = 100

    while each_level < level:
        required_karma += step
        each_level += 1

        if each_level % 10 == 0:
            step *= 2

    return required_karma

def get_cube(bind_karma: int) -> dict:
    return max(
        (
            cube
            for cube in _state.data.cube_roles.values()
            if cube["required_karma"] <= bind_karma
        ),
        key=lambda cube: cube["required_karma"],
    )

def get_cubes(ozernik_id: int) -> tuple[dict, dict, dict, dict]:
    binds = _state.db.get_user_top_karmic_binds(ozernik_id)

    cubes_count: dict = deepcopy(_state.data.cube_roles)

    for cube in cubes_count.values():
        cube["count"] = 0

    for ozernik, _bind in binds:
        bind_cube = get_cube(_bind.bind_karma)

        for cube in cubes_count.values():
            if cube["place"] <= bind_cube["place"]:
                cube["count"] += 1

    return tuple(cubes_count.values())

def get_cube_status(ozernik_id: int) -> dict | None:
    binds = _state.db.get_user_top_karmic_binds(ozernik_id)

    return get_cube_status_by_binds([bind.bind_karma for _, bind in binds])

def get_cube_status_by_binds(bind_karmas: list[int]) -> dict | None:
    """Текущий Куб по списку значений кармы связей пользователя."""
    cubes_count: dict[str, int] = {}

    for bind_karma in bind_karmas:
        bind_cube = get_cube(bind_karma)

        for cube in _state.data.cube_roles.values():
            if cube["place"] <= bind_cube["place"]:
                name = cube["name"]
                cubes_count[name] = cubes_count.get(name, 0) + 1

    available_cubes = [
        cube
        for cube in _state.data.cube_roles.values()
        if cubes_count.get(cube["name"], 0) >= (
            1 if cube["place"] == 1 else _state.data.required_cubes
        )
    ]

    if not available_cubes:
        return None

    return max(
        available_cubes,
        key=lambda _cube: _cube["required_karma"],
    )

def format_hours(minutes: int) -> str:
    """Короткая длительность в часах: '34 ч 14 мин', '6 ч', '45 мин'."""
    hours, minutes = divmod(minutes, 60)

    if hours and minutes:
        return f'{hours} ч {minutes} мин'
    if hours:
        return f'{hours} ч'
    return f'{minutes} мин'

def plural_ru(number: int, forms: tuple[str, str, str]) -> str:
    """Форма слова для числа: ('связь', 'связи', 'связей')."""
    n = number % 100

    if 11 <= n <= 14:
        return forms[2]

    n %= 10

    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]

    return forms[2]

def get_next_status(karma: DataTypes.Karma) -> dict:
    status = get_status(karma)

    if status['name'] in ("Человек", "Асур", "Дэва"):
        return None

    return next(
        (
            stage
            for stage in _state.data.karma_roles.values()
            if stage['required_karma'] > status['required_karma']
        ),
        None
    )

def get_next_cube(ozernik_id: int) -> dict | None:
    now_cube = get_cube_status(ozernik_id)

    if not now_cube:
        return _state.data.cube_roles['black_cube']

    return next(
        (
            cube
            for cube in _state.data.cube_roles.values()
            if cube['required_karma'] > now_cube['required_karma']
        ),
        _state.data.cube_roles.get('gold_cube', None)
    )

def get_next_cube_from_cube(cube_name: Literal['black_cube', 'white_cube', 'blue_cube', 'gold_cube']) -> dict | None:
    now_cube = _state.data.cube_roles.get(cube_name, None)

    if now_cube is None:
        raise ValueError(f'Cube "{cube_name}" not found')

    return next(
        (
            cube
            for cube in _state.data.cube_roles.values()
            if cube['required_karma'] > now_cube['required_karma']
        ),
        None
    )

def format_duration_minutes(minutes: int, backtick: bool = False) -> str:
    days, remainder = divmod(minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)

    def plural(number: int, forms: tuple[str, str, str]) -> str:
        n = number % 100

        if 11 <= n <= 14:
            return forms[2]

        n %= 10

        if n == 1:
            return forms[0]
        if 2 <= n <= 4:
            return forms[1]

        return forms[2]

    parts = []
    bt = ''
    if backtick:
        bt = '`'

    if days:
        parts.append(f"{bt}{days}{bt} {plural(days, ('день', 'дня', 'дней'))}")

    if hours:
        parts.append(f"{bt}{hours}{bt} {plural(hours, ('час', 'часа', 'часов'))}")

    if minutes:
        parts.append(f"{bt}{minutes}{bt} {plural(minutes, ('минута', 'минуты', 'минут'))}")

    return ", ".join(parts)

def format_duration_seconds(seconds: int, backtick: bool = False) -> str:
    days, remainder = divmod(seconds, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes, seconds = divmod(remainder, 60)

    def plural(number: int, forms: tuple[str, str, str]) -> str:
        n = number % 100

        if 11 <= n <= 14:
            return forms[2]

        n %= 10

        if n == 1:
            return forms[0]
        if 2 <= n <= 4:
            return forms[1]

        return forms[2]

    parts = []

    bt = ''
    if backtick:
        bt = '`'

    if days:
        parts.append(f"{bt}{days}{bt} {plural(days, ('день', 'дня', 'дней'))}")

    if hours:
        parts.append(f"{bt}{hours}{bt} {plural(hours, ('час', 'часа', 'часов'))}")

    if minutes:
        parts.append(f"{bt}{minutes}{bt} {plural(minutes, ('минута', 'минуты', 'минут'))}")

    if seconds:
        parts.append(f"{bt}{seconds}{bt} {plural(seconds, ('секунда', 'секунды', 'секунд'))}")

    return ", ".join(parts)

def collapse_dict(_data: dict) -> str:
    return ",\n".join(
        f"{key!r}: {collapse_value(value)}"
        for key, value in _data.items()
    )

def collapse_value(value) -> str:
    if isinstance(value, dict):
        return "{\n" + collapse_dict(value) + "\n}"

    return repr(value)

def expand_dict(text: str) -> dict:
    return ast.literal_eval("{" + text + "}")

def get_stage_by_karma(karma: int) -> dict:
    """Обычная ступень Сансары (без Асура/Дэвы) для количества кармы."""
    return max(
        (
            stage
            for stage in _state.data.karma_roles.values()
            if (
                stage["required_karma"] is not None
                and karma >= stage["required_karma"]
        )
        ),
        key=lambda stage: stage["required_karma"],
    )

def get_role_id_by_level(level: int) -> str:
    return get_stage_by_karma(get_karma(level))['role_id']

def get_new_stage_text(old_karma: int, new_karma: int) -> str:
    """Приписка «Теперь вы [роль]», если между old_karma и new_karma сменилась ступень Сансары."""
    old_stage = get_stage_by_karma(old_karma)
    new_stage = get_stage_by_karma(new_karma)

    if old_stage['tag_name'] == new_stage['tag_name']:
        return ''

    return f"Теперь вы <@&{new_stage['role_id']}>"

def get_level_up_text(level: int) -> str:
    text = _state.db.get_level_text(level)
    role_id = get_role_id_by_level(level)

    text = text.replace('{role}', f'<@&{role_id}>')

    return text

def get_level_up_line(level: int) -> str:
    """
    Строка «Вы … N уровня.» — глагол зависит от уровня, как в старой Сансаре (решение Alium и Габа):
    1–40 — «достигли», 41–80 — «добились», 81+ — «доползли до».

    Строка собирается целиком, а не подстановкой одного глагола: у «доползли»
    нужен предлог «до», и одна схема «Вы {глагол} N уровня» не подходит.
    """
    if level <= 40:
        return f'Вы достигли {level} уровня.'
    if level <= 80:
        return f'Вы добились {level} уровня.'
    return f'Вы доползли до {level} уровня.'

def build_level_up_description(level: int, previous_karma: int) -> str:
    """
    Текст сообщения о повышении, по шаблону старой Сансары (Amari):

        [текст уровня от администрации]
        Вы достигли N уровня.  ← глагол по уровню, см. get_level_up_line
        Теперь вы @Роль          ← только при смене ступени Сансары

    :param previous_karma: Карма, от которой считается переход (для смены ступени).
    """
    lines = []

    text = get_level_up_text(level)
    if text:
        # Текст администрации может быть многострочным — раскладываем по строкам.
        lines.extend(text.split('\n'))

    lines.append(get_level_up_line(level))

    stage_text = get_new_stage_text(previous_karma, get_karma(level))
    if stage_text:
        lines.append(stage_text)

    # Весь текст жирным (решение Alium). Каждую строку отдельно: выделение **…**
    # в Discord не переносится через перевод строки. Пустые строки не трогаем — «****» видно как текст.
    return '\n'.join(f'**{line}**' if line.strip() else line for line in lines)

# Функции PIL
