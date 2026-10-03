"""
Общее состояние модуля кармы: пути, настройки (data.json), база и тексты повышений.

Остальные файлы модуля обращаются к базе и настройкам только как `_state.db` /
`_state.data`, а не `from ._state import db`: при таком импорте у каждого файла была бы
своя копия ссылки, и подмена базы (в тестах или при перезагрузке) действовала бы не везде.
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

from ._db import KarmaDatabase

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bot import OzernikiBot


PROJECT_DIR = Path(__file__).resolve().parents[0]
TEMP_DIR = PROJECT_DIR / "temp"
DATA_DIR = PROJECT_DIR / "data.json"

# Старое хранилище текстов повышений. Тексты теперь в базе (таблица karma_level_texts);
# файл читается один раз при запуске для переноса — см. import_legacy_level_texts.
LEGACY_LEVELS_PATH = PROJECT_DIR / "levels.json"

# Сколько уровней можно подписать текстом в /settings_karma → «Текст повышений».
LEVEL_TEXTS_COUNT = 100

data_setup = {
    'log_channel_id': 0,
    'karma_message_delay': 1,
    'karma_voice_delay': 1,
    'karma_channel_id': 0,
    'blocked_channels_id': [],
    'blocked_users_id': [],
    'blocked_roles_id': [],
    'required_cubes': 10,
    'karma_roles': {
        'naraka': {
            'name': 'Нарака',
            'role_id': 0,
            'required_karma': 0,
            'tag_name': 'naraka',
            'color': "#d47b33",
        },
        'preta': {
            'name': 'Прета',
            'role_id': 0,
            'required_karma': 100,
            'tag_name': 'preta',
            'color': "#eb3434",
        },
        'animal': {
            'name': 'Зверь',
            'role_id': 0,
            'required_karma': 1000,
            'tag_name': 'animal',
            'color': "#405ee4",
        },
        'human': {
            'name': 'Человек',
            'role_id': 0,
            'required_karma': 5000,
            'tag_name': 'human',
            'color': "#c1f7f7",
        },
        'asur': {
            'name': 'Асур',
            'role_id': 0,
            'required_karma': None,
            'tag_name': 'asur',
            'color': "#bf0895",
        },
        'deva': {
            'name': 'Дэва',
            'role_id': 0,
            'required_karma': None,
            'tag_name': 'deva',
            'color': "#FDD439",
        },
    },
    'cube_roles': {
        'black_cube': {
            "name": 'Черный Куб',
            "color": "#0D0D0D",
            "role_id": 0,
            "required_karma": 0,
            'tag_name': 'black_cube',
            "place": 1
        },
        'white_cube': {
            "name": 'Белый Куб',
            "color": "#E7E7E7",
            "role_id": 0,
            "required_karma": 360,
            'tag_name': 'white_cube',
            "place": 2
        },
        'blue_cube': {
            "name": 'Синий Куб',
            "color": "#4b69d4",
            "role_id": 0,
            "required_karma": 1440,
            'tag_name': 'blue_cube',
            "place": 3
        },
        'gold_cube': {
            "name": 'Золотой Куб',
            "color": "#FFAB33",
            "role_id": 0,
            "required_karma": 5760,
            'tag_name': 'gold_cube',
            "place": 4
        },
    }
}
data = DataWorker(DATA_DIR, setup=data_setup)
db = KarmaDatabase()

def import_legacy_level_texts(path: Path) -> int:
    """
    Переносит тексты повышений из старого levels.json в базу (один раз).

    Тексты переносятся, только если в базе их ещё нет — чтобы старый файл
    не затёр уже отредактированное. Файл после этого переименовывается
    в levels.json.imported: остаётся резервной копией и больше не читается.

    :return: Сколько непустых текстов перенесено.
    """
    if not path.exists():
        return 0

    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as error:
        print(f"[Карма] Не удалось прочитать {path}: {error}")
        return 0

    texts = {
        int(level): text
        for level, text in raw.items()
        if str(level).isdigit()
        and 1 <= int(level) <= LEVEL_TEXTS_COUNT
        and isinstance(text, str)
        and text
    }

    imported = 0

    if texts and not db.get_level_texts():
        db.set_level_texts(texts)
        imported = len(texts)
        print(f"[Карма] Тексты повышений перенесены из {path.name} в базу: {imported} шт.")

    path.replace(path.with_name(path.name + ".imported"))

    return imported

def parse_level_texts(raw: dict) -> dict[int, str]:
    """
    Проверяет тексты из окна «Текст повышений».

    :raises ValueError: С понятным админу объяснением, что не так.
    """
    texts = {}

    for level, text in raw.items():
        if not str(level).isdigit() or not 1 <= int(level) <= LEVEL_TEXTS_COUNT:
            raise ValueError(f"Уровень {level!r}: нужен номер от 1 до {LEVEL_TEXTS_COUNT}.")
        if not isinstance(text, str):
            raise ValueError(f"Уровень {level}: текст должен быть в кавычках.")

        texts[int(level)] = text

    return texts


__all__ = [
    'PROJECT_DIR', 'TEMP_DIR', 'DATA_DIR', 'LEGACY_LEVELS_PATH', 'LEVEL_TEXTS_COUNT',
    'data_setup', 'import_legacy_level_texts', 'parse_level_texts',
]
