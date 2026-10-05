"""
Картинки (Pillow): карточки Сансары и Кубов, открытка связи.

Рисуются в отдельном потоке (asyncio.to_thread), поэтому сами create_* к базе не обращаются:
всё из базы приходит заранее через get_card_stats (соединение SQLite нельзя трогать из другого потока).
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


def load_icon(path: str | Path, size: tuple[int, int]) -> Image.Image:
    """Загружает PNG-иконку и изменяет её размер."""
    with Image.open(path) as source:
        return source.convert("RGBA").resize(
            size,
            Image.Resampling.LANCZOS,
        )

async def get_discord_avatar(member: discord.Member) -> Image.Image:
    avatar_bytes = await member.display_avatar.read()

    with Image.open(BytesIO(avatar_bytes)) as source:
        return source.convert("RGBA").resize(
            (220, 220),
            Image.Resampling.LANCZOS,
        )

def draw_counter(xy: tuple[int, int], nums: tuple[int, int], fill: Any, main_font: FreeTypeFont, image: Image.Image) -> tuple[int, int, int, int]:
    num1 = nums[0]
    num2 = nums[1]

    sep_font = main_font.font_variant(size=main_font.size * 0.63)

    draw = ImageDraw.Draw(image)

    main_text = f'{num1}'
    sep_text = f'/{num2}' if num2 else ''

    main_length = draw.textlength(
        main_text,
        main_font,
    )
    sep_length = draw.textlength(
        sep_text,
        sep_font,
    )

    full_length = main_length + sep_length

    main_xy = (
        xy[0]- full_length // 2,
        xy[1],
    )

    main_box = draw.textbbox(
        main_xy,
        main_text,
        main_font,
        anchor='lm'
    )

    sep_xy = (
        main_xy[0] + main_length + 1,
        main_box[3],
    )

    draw.text(
        main_xy,
        main_text,
        fill,
        main_font,
        anchor='lm'
    )
    draw.text(
        sep_xy,
        sep_text,
        '#6F6F6F',
        sep_font,
        anchor='lb',
    )

    sep_box = draw.textbbox(
        sep_xy,
        sep_text,
        sep_font,
        anchor='lb',
    )

    full_box = (
        main_box[0],
        main_box[1],
        sep_box[2],
        main_box[3],
    )

    return full_box

def rounded_gradient(image: Image.Image, box: tuple[int, int, int, int], radius: int, start_color: str, end_color: str):
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1

    start_rgb = tuple(bytes.fromhex(start_color.lstrip("#")))
    end_rgb = tuple(bytes.fromhex(end_color.lstrip("#")))

    gradient = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(gradient)

    for x in range(width):
        progress = x / (width - 1)

        color = tuple(
            int(
                start_rgb[i]
                + (end_rgb[i] - start_rgb[i]) * progress
            )
            for i in range(3)
        )

        draw.line(
            [(x, 0), (x, height)],
            fill=color,
        )

    mask = Image.new("L", (width, height), 0)

    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width, height),
        radius=radius,
        fill=255,
    )

    image.paste(
        gradient,
        (x1, y1),
        mask,
    )

async def get_full_binds(member: discord.Member, bot, amount: int = 10) -> list[dict[str, Image.Image | str | DataTypes.KarmicBind]]:
    guild = member.guild
    ozernik = _state.db.get_user_by_discord_id(member.id)
    binds = _state.db.get_user_top_karmic_binds(ozernik.id)

    full_binds = []

    n = 0
    for _ozernik, bind in binds:
        n += 1

        _member = guild.get_member(_ozernik.discord_id)

        if _member is None:
            _member = await bot.fetch_user(_ozernik.discord_id)

        full_binds.append({
            'name': _member.display_name,
            'bind': bind,
            'avatar': await get_discord_avatar(_member),
        })

        if n >= amount:
            break

    return full_binds

def get_card_stats(ozernik_id: int) -> dict:
    """
    Данные из базы для карточек Сансары и Кубов.

    Считать в основном потоке: соединение SQLite нельзя использовать из другого
    потока, а сами карточки рисуются в отдельном (asyncio.to_thread), чтобы не
    останавливать бота.
    """
    return {
        'karma': _state.db.get_karma(ozernik_id),
        'user_cubes': get_cubes(ozernik_id),
        'user_cube': get_cube_status(ozernik_id),
        'next_cube': get_next_cube(ozernik_id),
        'karma_rank': _state.db.get_karma_rank(ozernik_id),
        'weekly_karma_rank': _state.db.get_weekly_karma_rank(ozernik_id),
    }

def create_rank_card(ozernik: DataTypes.Ozernik, avatar_image: Image.Image, username: str, stats: dict | None = None):
    if stats is None:
        stats = get_card_stats(ozernik.id)

    # Расчет кармы и уровней
    karma = stats['karma']
    level = get_level(karma.karma)
    status = get_status(karma)

    user_cubes = stats['user_cubes']
    user_cube = stats['user_cube']
    next_cube = stats['next_cube']

    global_rank = f'#{stats["karma_rank"]}' if karma.karma > 0 else '-'
    weekly_rank = f'#{stats["weekly_karma_rank"]}' if karma.weekly_karma > 0 else '-'
    weekly_karma = f'{karma.weekly_karma}' if karma.weekly_karma > 0 else '-'

    current_level_karma = get_karma(level)
    next_level_karma = get_karma(level + 1)

    karma_on_level = karma.karma - current_level_karma
    required_on_level = next_level_karma - current_level_karma

    progress_level_up = round(karma_on_level / required_on_level, 2)

    next_stage = get_next_status(karma)
    if next_stage:
        next_stage_level = get_level(next_stage['required_karma'])
    else:
        next_stage_level = None

    # Определение цветов
    main_color = "#2A2C30"
    sep_color = "#24272A"
    sign_color = "#8B8B8B"

    # Создание изображения и базовых прямоугольников
    image = Image.new(
        mode="RGBA",
        size=(900, 220),
        color=(0, 0, 0, 0),
    )
    height_factor = image.height / 220
    draw = ImageDraw.Draw(image)

    gap = round(5 * height_factor)

    sep_box_height = (image.height - gap * 2) // 3

    main_box = (
        0,
        0,
        image.width // 9 * 7,
        image.height,
    )

    sep_boxes_x1 = main_box[2] + gap

    sep_1_box = (
        sep_boxes_x1,
        0,
        image.width,
        sep_box_height,
    )

    sep_2_box = (
        sep_boxes_x1,
        sep_box_height + gap,
        image.width,
        sep_box_height * 2 + gap,
    )

    sep_3_box = (
        sep_boxes_x1,
        sep_box_height * 2 + gap * 2,
        image.width,
        image.height,
    )

    radius = 20 * height_factor

    draw.rounded_rectangle(
        main_box,
        radius=radius,
        fill=main_color,
    ) # Большой прямоугольник

    draw.rounded_rectangle(
        sep_1_box,
        radius=radius,
        fill=main_color,
    ) # Верхний маленький

    draw.rounded_rectangle(
        sep_2_box,
        radius=radius,
        fill=main_color,
    ) # Средний маленький

    draw.rounded_rectangle(
        sep_3_box,
        radius=radius,
        fill=main_color,
    ) # Нижний маленький

    sep_boxes_width = image.width - sep_boxes_x1

    # Создание полоски прогресса в своем уровне
    fill_x = main_box[2] * progress_level_up

    mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(mask)

    mask_draw.rounded_rectangle(
        main_box,
        radius=radius,
        fill=255,
    )

    mask_draw.rectangle(
        (main_box[0], main_box[1], main_box[2], main_box[3] - 12 * height_factor),
        fill=0,
    )

    image.paste(
        sep_color,
        (0, 0, image.width, image.height),
        mask,
    ) # Незаполненная часть полоски

    mask_draw.rectangle(
        (fill_x, main_box[1], main_box[2], main_box[3]),
        fill=0,
    )

    image.paste(
        status["color"],
        (0, 0, image.width, image.height),
        mask,
    ) # Заполненная часть полоски

    # Вставка аватара
    avatar_size = image.height // 3 * 2
    avatar_image = avatar_image.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)

    mask = Image.new("L", avatar_image.size, 0)
    mask_draw = ImageDraw.Draw(mask)

    mask_draw.ellipse((0, 0, avatar_image.width, avatar_image.height), fill=255)

    avatar_image.putalpha(mask)

    avatar_gap = image.height // 2 - avatar_image.height // 2

    image.paste(
        avatar_image,
        (avatar_gap, avatar_gap),
        avatar_image,
    )

    avatar_right_end = avatar_image.width + avatar_gap

    # Вставка имени
    font = ImageFont.truetype(
        assets.fonts['Roboto-Bold'],
            size=45 * height_factor,
        )

    name_xy = (avatar_right_end + avatar_gap, avatar_gap)
    max_name_length = (main_box[2] - avatar_gap) - name_xy[0]

    if draw.textlength(username, font=font) >= max_name_length:
        while username:
            username = username.rstrip() + '...'

            if draw.textlength(username, font=font) <= max_name_length:
                break

            username = username[:-4]

    draw.text(
        name_xy,
        username,
        font=font,
        fill=status["color"],
        anchor='lt'
    )

    # Вставка рангов
    sign_font = ImageFont.truetype(
        assets.fonts['Roboto'],
        size=22 * height_factor,
    )
    num_font = ImageFont.truetype(
        assets.fonts['Roboto-Bold'],
        size=40 * height_factor,
    )

    column_width = (main_box[2] - avatar_right_end) / 3

    ranks_x1 = avatar_right_end + column_width * 0.6
    ranks_x2 = avatar_right_end + column_width * 1.5
    ranks_x3 = avatar_right_end + column_width * 2.4

    ranks_y = image.height / 11 * 6
    ranks_y2 = image.height - (image.height - ranks_y) / 2

    draw.text(
        (ranks_x1, ranks_y),
        'РАНГ\nКАРМЫ',
        align="center",
        font=sign_font,
        fill=sign_color,
        anchor="mm",
    )

    draw.text(
        (ranks_x1, ranks_y2),
        global_rank,
        font=num_font,
        fill=status["color"],
        anchor="mm",
    )

    draw.text(
        (ranks_x2, ranks_y),
        'РАНГ\nНЕДЕЛИ',
        align="center",
        font=sign_font,
        fill=sign_color,
        anchor="mm",
    )

    draw.text(
        (ranks_x2, ranks_y2),
        weekly_rank,
        font=num_font,
        fill='#BFBFBF',
        anchor="mm",
    )

    draw.text(
        (ranks_x3, ranks_y),
        'КАРМА\nНЕДЕЛИ',
        align="center",
        font=sign_font,
        fill=sign_color,
        anchor="mm",
    )

    draw.text(
        (ranks_x3, ranks_y2),
        weekly_karma,
        font=num_font,
        fill='#BFBFBF',
        anchor="mm",
    )

    # Вставка уровня
    sign_font = ImageFont.truetype(
        assets.fonts['Roboto-Bold'],
        size=16 * height_factor,
    )

    num_font = ImageFont.truetype(
        assets.fonts['Roboto-Bold'],
        size=25 * height_factor,
    )

    sep_box_counters_x = round(sep_boxes_x1 + sep_boxes_width / 100 * 71)

    level_counter_xy = (
        sep_box_counters_x,
        sep_1_box[3] - sep_box_height // 3
    )

    level_box = draw_counter(
        level_counter_xy,
        nums=(
            level,
            next_stage_level
        ),
        fill=status["color"],
        main_font=num_font,
        image=image,
    )

    level_frame = (
        level_box[0] - 9 * height_factor,
        level_box[1] - 7 * height_factor,
        level_box[2] + 9 * height_factor,
        level_box[3] + 5 * height_factor
    )

    draw.rounded_rectangle(
        level_frame,
        radius=5 * height_factor,
        fill=sep_color,
    ) # Рамка уровня

    draw.text(
        (sep_box_counters_x, level_frame[1] - 10 * height_factor),
        f'УРОВЕНЬ',
        sign_color,
        font=sign_font,
        anchor="mm",
    )

    draw_counter(
        level_counter_xy,
        nums=(
            level,
            next_stage_level
        ),
        fill=status["color"],
        main_font=num_font,
        image=image,
    )

    level_sign_box = draw.textbbox(
        (sep_box_counters_x, level_frame[1] - 10 * height_factor),
        f'УРОВЕНЬ',
        font=sign_font,
        anchor="mm",
    )

    # Вставка иконки Сансары
    sansara_image = Image.open(assets.sansara[status['tag_name']]).convert("RGBA")

    icon_gap = round(8 * height_factor)
    icon_box = (
        sep_boxes_x1 + icon_gap * 2,
        icon_gap,
        level_sign_box[0] - icon_gap * 2,
        sep_1_box[3] - icon_gap
    )

    icon_box_width = icon_box[2] - icon_box[0]
    icon_box_height = icon_box[3] - icon_box[1]
    icon_center_xy = (icon_box[0] + icon_box[2]) // 2, (icon_box[1] + icon_box[3]) // 2
    icon_aspect_ratio = icon_box_width / icon_box_height

    sansara_aspect_ratio = sansara_image.width / sansara_image.height
    aligning = round(2 * height_factor)

    if sansara_aspect_ratio > icon_aspect_ratio:
        sansara_width = icon_box_width
        sansara_height = round(icon_box_width / sansara_aspect_ratio)

        sansara_box = (
            icon_box[0] + aligning,
            icon_center_xy[1] - sansara_height // 2 + aligning,
        )
    else:
        sansara_width = round(icon_box_height * sansara_aspect_ratio)
        sansara_height = icon_box_height

        sansara_box = (
            icon_center_xy[0] - sansara_width // 2 + aligning,
            icon_box[1] + aligning
        )

    sansara_image = sansara_image.resize((sansara_width, sansara_height), Image.Resampling.LANCZOS)

    image.paste(
        sansara_image,
        sansara_box,
        sansara_image,
    )

    # Вставка счетчика кубов
    cube_counter_xy = (
        sep_box_counters_x,
        sep_2_box[3] - sep_box_height // 3
    )

    if not user_cube:
        count_available_cubes = user_cubes[0]["count"]
        required_cubes = 1
    else:
        if user_cube["place"] == len(user_cubes):
            count_available_cubes = 0
            required_cubes = 0
        else:
            count_available_cubes = user_cubes[user_cube["place"]]["count"]
            required_cubes = _state.data.required_cubes

    cube_counter_box = draw_counter(
        cube_counter_xy,
        nums=(
            count_available_cubes,
            required_cubes,
        ),
        fill=next_cube['color'],
        main_font=num_font,
        image=image,
    )

    cube_counter_frame = (
        cube_counter_box[0] - 9 * height_factor,
        cube_counter_box[1] - 7 * height_factor,
        cube_counter_box[2] + 9 * height_factor,
        cube_counter_box[3] + 5 * height_factor,
    )

    draw.rounded_rectangle(
        cube_counter_frame,
        radius=5 * height_factor,
        fill=sep_color,
    )  # Рамка счетчика кубов

    draw_counter(
        cube_counter_xy,
        nums=(
            count_available_cubes,
            required_cubes,
        ),
        fill=next_cube['color'],
        main_font=num_font,
        image=image,
    )

    draw.text(
        (sep_box_counters_x, cube_counter_frame[1] - 10 * height_factor),
        f'СВЯЗЬ',
        sign_color,
        font=sign_font,
        anchor="mm"
    )

    # Вставка куба
    cube_size = sep_box_height // 4 * 3

    cube_center_xy = (
        icon_center_xy[0],
        sep_2_box[1] + sep_box_height // 2,
    )

    cube_xy = (
        cube_center_xy[0] - cube_size // 2,
        cube_center_xy[1] - cube_size // 2
    )

    if user_cube:
        cube_image = load_icon(
            assets.cubes[user_cube["tag_name"]],
            (cube_size, cube_size),
        )
    else:
        cube_image = load_icon(
            assets.cubes['drink'],
            (cube_size, cube_size),
        )

    image.paste(
        cube_image,
        cube_xy,
        cube_image,
    )

    # Счетчик опыта
    score_xy = (
        sep_boxes_x1 + sep_boxes_width // 2,
        sep_3_box[3] - sep_box_height // 3
    )

    score_box = draw_counter(
        score_xy,
        nums=(
            karma.karma,
            next_level_karma,
        ),
        fill='white',
        main_font=num_font,
        image=image,
    )

    score_frame = (
        score_box[0] - 9 * height_factor,
        score_box[1] - 7 * height_factor,
        score_box[2] + 8 * height_factor,
        score_box[3] + 4 * height_factor,
    )

    draw.rounded_rectangle(
        score_frame,
        radius=5 * height_factor,
        fill=sep_color,
    )  # Нижний маленький внутренний прямоугольник

    draw_counter(
        score_xy,
        nums=(
            karma.karma,
            next_level_karma,
        ),
        fill='white',
        main_font=num_font,
        image=image,
    )

    draw.text(
        (score_xy[0], score_frame[1] - 10 * height_factor),
        f'КАРМА',
        sign_color,
        font=sign_font,
        anchor="mm",
    )

    # Сохранение изображения
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

def create_bind_up_postcard(bind: DataTypes.KarmicBind, avatar_1_image, avatar_2_image) -> str:
    bind_karma = bind.bind_karma

    cube = get_cube(bind_karma)

    # Создание изображения и базовых прямоугольников
    image = Image.new(
        mode="RGBA",
        size=(850, 300),
        color=(30, 30, 30, 255)
    )

    background_path = random.choice(list(assets.backgrounds.values()))

    background = Image.open(background_path).convert("RGBA")
    background = background.resize(
        (image.width, background.height * image.width // background.width),
        Image.Resampling.LANCZOS,
    )

    background_box = (0, random.randint(image.height - background.height, 0))
    image.paste(
        background,
        background_box,
    )

    # Вставка аватарок
    mask = Image.new("L", avatar_1_image.size, 0)
    mask_draw = ImageDraw.Draw(mask)

    mask_draw.ellipse((0, 0, avatar_1_image.width, avatar_1_image.height), fill=255)

    avatar_1_image.putalpha(mask)
    avatar_2_image.putalpha(mask)

    height_center = (image.height // 2) - avatar_1_image.height // 2

    xy_1 = height_center, height_center
    xy_2 = image.width - (height_center + avatar_1_image.height), height_center

    image.paste(
        avatar_1_image,
        xy_1,
        avatar_1_image,
    )

    image.paste(
        avatar_2_image,
        xy_2,
        avatar_2_image,
    )

    # Вставка куба
    cube_image = Image.open(assets.cubes[cube["tag_name"]]).convert('RGBA')

    side = (image.height // 2) - image.height // 20
    cube_image = cube_image.resize(
        (side, side),
        Image.Resampling.LANCZOS,
    )

    cube_image_box = ((image.width // 2) - cube_image.width // 2, ((image.height // 2) - cube_image.height // 2))
    image.paste(
        cube_image,
        cube_image_box,
    )

    # Сохранение изображения
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

def create_cube_cart(ozernik: DataTypes.Ozernik, avatar_image: Image.Image, username: str, full_binds: list[dict[str, Image.Image | str | DataTypes.KarmicBind]], stats: dict | None = None) -> Image.Image:
    if stats is None:
        stats = get_card_stats(ozernik.id)

    # Расчеты
    size = (1200, 800)
    size_factor = min(size) / 600

    main_objects_size = size[1] // 5
    gap = size[1] // 150

    radius = 20 * size_factor
    main_box_gap = main_objects_size // 4

    karma = stats['karma']
    status = get_status(karma)

    user_cubes = stats['user_cubes']
    user_cube = stats['user_cube']
    next_cube = stats['next_cube']

    full_binds = sorted(full_binds, key=lambda b: b["bind"].bind_karma, reverse=True)
    num_binds = 10

    # Определение цветов
    main_color = "#2A2C30"
    sep_color = "#24272A"
    sign_color = "#BFBFBF"
    status_color = status["color"]

    # Создание изображения и базовых прямоугольников
    image = Image.new(
        mode="RGBA",
        size=size,
        color=(0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(image)

    main_box = (
        0,
        0,
        image.width,
        main_objects_size + main_box_gap * 2,
    )

    draw.rounded_rectangle(
        main_box,
        radius=radius,
        fill=main_color,
    )

    binds_full_box = (
        0,
        main_box[3],
        image.width,
        image.height,
    )

    binds_full_box_height = binds_full_box[3] - binds_full_box[1]
    bind_box_width = image.width // 2
    bind_box_height = round(binds_full_box_height // (num_binds // 2) - gap * 1.3)

    left_boxes = []
    right_boxes = []

    y1 = binds_full_box[1] + gap

    for n in range(num_binds // 2):
        left_box = (
            0,
            y1,
            bind_box_width - gap // 2,
            y1 + bind_box_height,
        )

        right_box = (
            bind_box_width + gap // 2,
            y1,
            image.width,
            y1 + bind_box_height,
        )

        left_boxes.append(left_box)
        right_boxes.append(right_box)

        draw.rounded_rectangle(left_box, radius=radius, fill=main_color)
        draw.rounded_rectangle(right_box, radius=radius, fill=main_color)

        y1 += bind_box_height + gap

    bind_boxes = left_boxes + right_boxes
    for n, full_bind in enumerate(full_binds):
        full_bind['box'] = bind_boxes[n]

    for bind_box in bind_boxes:
        mask = Image.new("L", image.size, 0)
        mask_draw = ImageDraw.Draw(mask)

        mask_draw.rounded_rectangle(
            bind_box,
            radius=radius,
            fill=255,
        )

        mask_draw.rectangle(
            (bind_box[0], bind_box[1], bind_box[2], bind_box[3] - bind_box_height / 13),
            fill=0,
        )

        image.paste(
            sep_color,
            (0, 0, image.width, image.height),
            mask,
        )  # Незаполненная часть полоски

        avatar_center_xy = (
            bind_box[0] + bind_box_height // 2,
            bind_box[3] - bind_box_height // 2
        )

        no_avatar_font = ImageFont.truetype(
            font=assets.fonts['Roboto-Bold'],
            size=25 * size_factor
        )

        draw.text(
            avatar_center_xy,
            '—',
            sign_color,
            font=no_avatar_font,
            align="center",
            anchor="mm"
        )

    # Вставка аватара
    avatar_size = main_objects_size
    avatar_image = avatar_image.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)

    mask = Image.new("L", avatar_image.size, 0)
    mask_draw = ImageDraw.Draw(mask)

    mask_draw.ellipse((0, 0, avatar_image.width, avatar_image.height), fill=255)

    avatar_image.putalpha(mask)

    image.paste(
        avatar_image,
        (main_box_gap, main_box_gap),
        avatar_image,
    )

    avatar_full_box = (0, 0, avatar_size + main_box_gap * 2, avatar_size + main_box_gap * 2)

    # Вставка куба
    cube_size = round(main_objects_size / 10 * 9)
    cube_gap = round(main_box[3] / 2 - cube_size / 2)

    cube_xy = (
        image.width - cube_size - cube_gap,
        cube_gap,
    )

    if user_cube:
        cube_image = load_icon(
            assets.cubes[user_cube["tag_name"]],
            (cube_size, cube_size),
        )
    else:
        cube_image = load_icon(
            assets.cubes['drink'],
            (cube_size, cube_size),
        )

    image.paste(
        cube_image,
        cube_xy,
        cube_image,
    )

    # Вставка имени
    name_font = ImageFont.truetype(
        assets.fonts['Roboto-Bold'],
        size=40 * size_factor,
    )
    name_text = f'{username}'

    name_xy = (avatar_full_box[2], cube_gap)
    max_name_length = (main_box[2] - avatar_full_box[2]) - name_xy[0]

    if draw.textlength(name_text, font=name_font) >= max_name_length:
        while name_text:
            name_text = name_text.rstrip() + '...'

            if draw.textlength(name_text, font=name_font) <= max_name_length:
                break

            name_text = name_text[:-4]

    draw.text(
        name_xy,
        name_text,
        font=name_font,
        fill=status["color"],
        anchor='lt'
    )

    # sign_font = ImageFont.truetype(
    #     assets.fonts['Roboto-Bold'],
    #     size=35 * size_factor,
    # )

    # sign_text = 'Связи:'
    # sign_xy = (avatar_full_box[2], main_box[3] - main_box_gap)
    # max_sign_length = (main_box[2] - avatar_full_box[2]) - name_xy[0]
    #
    # if draw.textlength(username, font=name_font) >= max_sign_length:
    #     while username:
    #         username = username.rstrip() + '...'
    #
    #         if draw.textlength(username, font=name_font) <= max_sign_length:
    #             break
    #
    #         username = username[:-4]
    #
    # draw.text(
    #     sign_xy,
    #     sign_text,
    #     font=sign_font,
    #     fill='#9F9F9F',
    #     anchor='lb',
    #     spacing=10
    # )

    # Вставка рангов
    sign_font = ImageFont.truetype(
        assets.fonts['Roboto'],
        size=20 * size_factor,
    )
    num_font = ImageFont.truetype(
        assets.fonts['Roboto-Bold'],
        size=30 * size_factor,
    )

    avatar_right_end = avatar_full_box[2]
    cube_left_end = main_box[2] - cube_size - cube_gap * 2

    column_width = (cube_left_end - avatar_right_end) / 3

    ranks_x1 = avatar_right_end + column_width * 0.3
    ranks_x2 = avatar_right_end + column_width * 1.1
    ranks_x3 = avatar_right_end + column_width * 1.9
    ranks_x4 = avatar_right_end + column_width * 2.7

    ranks_y = main_box[3] / 10 * 6
    ranks_y2 = main_box[3] - (main_box[3] - ranks_y) / 2

    gold_text =  f'{user_cubes[3]['count']}' if user_cubes[3]['count'] >= 10 else f'{user_cubes[3]['count']}/10'
    blue_text =  f'{user_cubes[2]['count']}' if user_cubes[2]['count'] >= 10 else f'{user_cubes[2]['count']}/10'
    white_text = f'{user_cubes[1]['count']}' if user_cubes[1]['count'] >= 10 else f'{user_cubes[1]['count']}/10'
    black_text = f'{user_cubes[0]['count']}' if user_cubes[0]['count'] >= 1 else f'{user_cubes[0]['count']}/1'

    draw.text(
        (ranks_x1, ranks_y),
        'ЗОЛОТЫЕ',
        align="center",
        font=sign_font,
        fill=sign_color,
        anchor="mm",
    )

    draw.text(
        (ranks_x1, ranks_y2),
        gold_text,
        font=num_font,
        fill='#BFBFBF',
        anchor="mm",
    )

    draw.text(
        (ranks_x2, ranks_y),
        'СИНИЕ+',
        align="center",
        font=sign_font,
        fill=sign_color,
        anchor="mm",
    )

    draw.text(
        (ranks_x2, ranks_y2),
        blue_text,
        font=num_font,
        fill='#BFBFBF',
        anchor="mm",
    )

    draw.text(
        (ranks_x3, ranks_y),
        'БЕЛЫЕ+',
        align="center",
        font=sign_font,
        fill=sign_color,
        anchor="mm",
    )

    draw.text(
        (ranks_x3, ranks_y2),
        white_text,
        font=num_font,
        fill='#BFBFBF',
        anchor="mm",
    )

    draw.text(
        (ranks_x4, ranks_y),
        'ЧЕРНЫЕ+',
        align="center",
        font=sign_font,
        fill=sign_color,
        anchor="mm",
    )

    draw.text(
        (ranks_x4, ranks_y2),
        black_text,
        font=num_font,
        fill='#BFBFBF',
        anchor="mm",
    )

    # Вставка связей
    bind_object_size = bind_box_height // 4 * 3
    bind_name_font = ImageFont.truetype(
        font=assets.fonts['Roboto-Bold'],
        size=25 * size_factor
    )
    bind_counter_font = ImageFont.truetype(
        font=assets.fonts['Roboto-Bold'],
        size=19 * size_factor
    )

    for full_bind in full_binds:
        bind_cube = get_cube(full_bind['bind'].bind_karma)
        next_cube = get_next_cube_from_cube(bind_cube['tag_name'])
        bind_color = bind_cube['color']
        name_color = '#AAAAAA'

        # Вставка полоски прогресса
        if bind_cube['tag_name'] != 'gold_cube':
            progress_factor = full_bind['bind'].bind_karma / next_cube['required_karma']

            # Длина заполненной части — от настоящей ширины плашки связи, а не от половины
            # картинки: плашка на несколько пикселей уже (зазор между колонками). Раньше при
            # прогрессе >99,7% правый край заливки вылезал за плашку, и Pillow падал с
            # «x1 must be greater than or equal to x0» — карточка Кубов не рисовалась.
            box = full_bind['box']
            fill_x = box[0] + (box[2] - box[0]) * min(max(progress_factor, 0), 1)

            mask = Image.new("L", image.size, 0)
            mask_draw = ImageDraw.Draw(mask)

            mask_draw.rounded_rectangle(
                full_bind['box'],
                radius=radius,
                fill=255,
            )

            mask_draw.rectangle(
                (full_bind['box'][0], full_bind['box'][1], full_bind['box'][2], full_bind['box'][3] - bind_box_height / 13),
                fill=0,
            )

            image.paste(
                sep_color,
                (0, 0, image.width, image.height),
                mask,
            )  # Незаполненная часть полоски

            mask_draw.rectangle(
                (fill_x, full_bind['box'][1], full_bind['box'][2], full_bind['box'][3]),
                fill=0,
            )

            image.paste(
                bind_color,
                (0, 0, image.width, image.height),
                mask,
            )  # Заполненная часть полоски
        else:
            rounded_gradient(
                image,
                full_bind['box'],
                radius,
                bind_color,
                "#FFC573",
            )
            bind_color = '#000000'
            name_color = '#000000'

        # Вставка аватара
        bind_avatar_image = full_bind['avatar'].resize((bind_object_size, bind_object_size), Image.Resampling.LANCZOS)

        mask = Image.new("L", bind_avatar_image.size, 0)
        mask_draw = ImageDraw.Draw(mask)

        mask_draw.ellipse((0, 0, bind_avatar_image.width, bind_avatar_image.height), fill=255)

        bind_avatar_image.putalpha(mask)

        avatar_center_xy = (
            full_bind['box'][0] + bind_box_height // 2,
            full_bind['box'][3] - bind_box_height // 2
        )
        avatar_xy = (
            avatar_center_xy[0] - bind_object_size // 2,
            avatar_center_xy[1] - bind_object_size // 2
        )
        image.paste(
            bind_avatar_image,
            avatar_xy,
            bind_avatar_image,
        )

        # Вставка имени
        bind_name_xy = (full_bind['box'][0] + bind_box_height, full_bind['box'][1] + bind_box_height / 5)
        max_bind_name_length = bind_box_width - bind_box_height * 1.2

        if draw.textlength(full_bind['name'], font=bind_name_font) >= max_bind_name_length:
            while full_bind['name']:
                full_bind['name'] = full_bind['name'].rstrip() + '...'

                if draw.textlength(full_bind['name'], font=bind_name_font) <= max_bind_name_length:
                    break

                full_bind['name'] = full_bind['name'][:-4]

        draw.text(
            bind_name_xy,
            full_bind['name'],
            font=bind_name_font,
            fill=name_color,
            anchor='lt'
        )

        # Вставка счётчика
        bind_counter_xy = (full_bind['box'][0] + bind_box_height, full_bind['box'][3] - bind_box_height / 6)
        draw.text(
            bind_counter_xy,
            format_duration_minutes(full_bind['bind'].bind_karma),
            font=bind_counter_font,
            fill=bind_color,
            anchor='ld'
        )

        if next_cube:
            bind_ambition_xy = (full_bind['box'][2] - bind_box_height / 4, bind_counter_xy[1])
            draw.text(
                bind_ambition_xy,
                format_duration_minutes(next_cube['required_karma']),
                font=bind_counter_font,
                fill=sign_color,
                anchor='rd'
            )

    # Сохранение изображения
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
