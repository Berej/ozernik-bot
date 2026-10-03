"""
Роли Сансары и Кубов: поиск, пересоздание, восстановление названия, цвета и иконки.
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


# ---------------------------------------------------------
# |||||||||||||||| Класс работы с ролями ||||||||||||||||||
# ---------------------------------------------------------

class Roles:
    def __init__(self, bot: OzernikiBot):
        self._bot = bot

        self._sansara_roles = [
            "naraka",
            "preta",
            "animal",
            "human",
            "asur",
            "deva",
        ]

        self._cubes_roles = [
            "black_cube",
            "white_cube",
            "blue_cube",
            "gold_cube",
        ]

    async def _get_role(self, roles_key: str, icons: dict[str, Path], role_name: str, recreate: bool, restore: bool) -> Role | None:
        """
        Находит роль по ID из data (кэш сервера → запрос к Discord).

        - recreate: если роли нет — создать и сохранить новый ID;
        - restore: вернуть название и цвет из настроек.
        Иконка загружается только при создании роли или при restore —
        не при каждом обращении (иначе каждое повышение правило 7 ролей).
        """
        guild: discord.Guild = self._bot.guild

        if not guild:
            return None

        roles_data = getattr(_state.data, roles_key)
        role_data = roles_data[role_name]

        role = guild.get_role(role_data['role_id'])

        if role is None:
            try:
                role = await guild.fetch_role(role_data['role_id'])
            except discord.HTTPException:
                pass

        created = False

        if role is None and recreate:
            try:
                role = await guild.create_role(
                    reason="Восстановление роли.",
                    name=role_data['name'],
                    colour=discord.Colour.from_str(role_data['color']),
                    hoist=False,
                    mentionable=False,
                )

                role_data['role_id'] = role.id
                setattr(_state.data, roles_key, roles_data)
                created = True
            except Exception as e:
                print(e)
                print(traceback.format_exc())

        if role is None:
            return None

        if restore and not created:
            try:
                await role.edit(
                    reason="Восстановление роли.",
                    name=role_data['name'],
                    colour=discord.Colour.from_str(role_data['color']),
                )
            except Exception as e:
                print(f'Не удалось восстановить роль {role_name}: {e}')

        if created or restore:
            # Иконки ролей доступны только на сервере с 2 уровнем буста — ошибку глушим.
            try:
                with open(icons[role_name], "rb") as f:
                    icon_bytes = f.read()

                await role.edit(display_icon=icon_bytes)
            except Exception:
                pass

        return role

    async def get_sansara_role(
        self,
        role_name: Literal[
            'naraka',
            'preta',
            'animal',
            'human',
            'asur',
            'deva',
        ],
        recreate: bool = True,
        restore: bool = False,
    ) -> Role | None:
        return await self._get_role('karma_roles', assets.sansara, role_name, recreate, restore)

    async def get_cube_role(
        self,
        role_name: Literal[
            'black_cube',
            'white_cube',
            'blue_cube',
            'gold_cube',
        ],
        recreate: bool = True,
        restore: bool = False,
    ) -> Role | None:
        return await self._get_role('cube_roles', assets.cubes, role_name, recreate, restore)

    async def get_sansara_roles(self, recreate: bool = True, restore: bool = False) -> list[Role] | None:
        roles = []

        for role_name in self._sansara_roles:
            role = await self.get_sansara_role(role_name, recreate, restore)

            if role is not None:
                roles.append(role)

        return roles or None

    async def get_cube_roles(self, recreate: bool = True, restore: bool = False) -> list[Role] | None:
        roles = []

        for role_name in self._cubes_roles:
            role = await self.get_cube_role(role_name, recreate, restore)

            if role is not None:
                roles.append(role)

        return roles or None
