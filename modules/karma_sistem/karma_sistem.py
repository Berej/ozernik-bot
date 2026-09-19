import asyncio
import inspect
import random
import traceback
import typing # noqa
from copy import deepcopy
from io import BytesIO
from itertools import combinations
from pathlib import Path # noqa
from typing import Any # noqa

import discord
from discord import Interaction, app_commands, Role
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont, ImageOps # noqa
from PIL.ImageFont import FreeTypeFont
from discord.ui import Item, Button, View, LayoutView # noqa

from bot import OzernikiBot
from config import config
from utilities import *

PROJECT_DIR = Path(__file__).resolve().parents[2]
TEMP_DIR = PROJECT_DIR / "temp"

data_setup = {
    'log_channel_id': 0,
    'karma_message_delay': 1,
    'karma_voice_delay': 1,
    'karma_channel_id': 0,
    'blocked_channels_id': [],
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

data = DataWorker('modules/karma_sistem/data.json', setup=data_setup)
db = KarmaDatabase()

# Вспомогательные функции
def get_status(karma: DataTypes.Karma) -> dict:
    if karma.status in ("asur", "deva"):
        return data.karma_roles[karma.status]

    return max(
        (
            stage
            for stage in data.karma_roles.values()
            if (
                stage["required_karma"] is not None
                and karma.karma >= stage["required_karma"]
            )
        ),
        key=lambda stage: stage["required_karma"],
    )

def get_level(karma: int) -> int:
    if karma < 0:
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
            for cube in data.cube_roles.values()
            if cube["required_karma"] <= bind_karma
        ),
        key=lambda cube: cube["required_karma"],
    )

def get_cubes(ozernik_id: int) -> tuple[dict, dict, dict, dict]:
    binds = db.get_user_top_karmic_binds(ozernik_id)

    cubes_count: dict = deepcopy(data.cube_roles)

    for cube in cubes_count.values():
        cube["count"] = 0

    for ozernik, _bind in binds:
        bind_cube = get_cube(_bind.bind_karma)
        cube = next(
            cube for cube in cubes_count.values()
            if cube["name"] == bind_cube["name"]
        )
        cube['count'] = cube['count'] + 1

    return tuple(cube for cube in cubes_count.values())

def get_cube_status(ozernik_id: int) -> dict | None:
    binds = db.get_user_top_karmic_binds(ozernik_id)

    cubes_count: dict[str, int] = {}

    for ozernik, bind in binds:
        bind_cube = get_cube(bind.bind_karma)

        for cube in data.cube_roles.values():
            if cube["place"] <= bind_cube["place"]:
                name = cube["name"]
                cubes_count[name] = cubes_count.get(name, 0) + 1

    available_cubes = [
        cube
        for cube in data.cube_roles.values()
        if cubes_count.get(cube["name"], 0) >= data.required_cubes
    ]

    if not available_cubes:
        return None

    return max(
        available_cubes,
        key=lambda _cube: _cube["required_karma"],
    )

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

def get_next_status(karma: DataTypes.Karma) -> dict:
    status = get_status(karma)

    if status['name'] in ("Человек", "Асур", "Дэва"):
        return None

    return next(
        (
            stage
            for stage in data.karma_roles.values()
            if stage['required_karma'] > status['required_karma']
        ),
        None
    )

def get_next_cube(ozernik_id: int) -> dict | None:
    now_cube = get_cube_status(ozernik_id)

    if not now_cube:
        return cubes[0]

    return next(
        (
            cube
            for cube in data.cube_roles.values()
            if cube['required_karma'] > now_cube['required_karma']
        ),
        data.cube_roles.get('gold_cube', None)
    )

def get_next_cube_from_cube(cube_name: Literal['black_cube', 'white_cube', 'blue_cube', 'gold_cube']) -> dict | None:
    now_cube = data.cube_roles.get(cube_name, None)

    if now_cube is None:
        raise ValueError(f'Cube "{cube_name}" not found')

    return next(
        (
            cube
            for cube in data.cube_roles.values()
            if cube['required_karma'] > now_cube['required_karma']
        ),
        None
    )

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

def format_duration(minutes: int) -> str:
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

    if days:
        parts.append(f"{days} {plural(days, ('день', 'дня', 'дней'))}")

    if hours:
        parts.append(f"{hours} {plural(hours, ('час', 'часа', 'часов'))}")

    if minutes:
        parts.append(f"{minutes} {plural(minutes, ('минута', 'минуты', 'минут'))}")

    return ", ".join(parts)

async def get_full_binds(member: discord.Member, amount: int = 10) -> list[dict[str, Image.Image | str | DataTypes.KarmicBind]]:
    guild = member.guild
    ozernik = db.get_user_by_discord_id(member.id)
    binds = db.get_user_top_karmic_binds(ozernik.id)

    full_binds = []

    n = 0
    for _ozernik, bind in binds:
        n += 1

        _member = guild.get_member(_ozernik.discord_id)
        if _member is None:
            continue
        full_binds.append({
            'name': _member.display_name,
            'bind': bind,
            'avatar': await get_discord_avatar(_member),
        })

        if n >= amount:
            break

    return full_binds

# Функции PIL
def create_rank_card(ozernik: DataTypes.Ozernik, avatar_image: Image.Image, username: str):
    # Расчет кармы и уровней
    karma = db.get_karma(ozernik.id)
    level = get_level(karma.karma)
    status = get_status(karma)

    user_cubes = get_cubes(ozernik.id)
    user_cube = get_cube_status(ozernik.id)
    next_cube = get_next_cube(ozernik.id)

    global_rank = f'#{db.get_karma_rank(ozernik.id)}' if karma.karma > 0 else '-'
    weekly_rank = f'#{db.get_weekly_karma_rank(ozernik.id)}' if karma.weekly_karma > 0 else '-'
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
        count_available_cubes = sum(cube["count"] for cube in user_cubes)
        user_cube_name = 'Drink!'
    else:
        indent = user_cube['place']
        if user_cube['place'] == len(user_cubes):
            indent -= 1

        count_available_cubes = sum(cube["count"] for cube in user_cubes[indent:])
        user_cube_name = user_cube['name']

    cube_counter_box = draw_counter(
        cube_counter_xy,
        nums=(
            count_available_cubes,
            data.required_cubes if user_cube_name != 'Золотой Куб' else 0,
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
            data.required_cubes if user_cube_name != 'Золотой Куб' else 0,
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

def create_cube_cart(ozernik: DataTypes.Ozernik, avatar_image: Image.Image, username: str, full_binds: list[dict[str, Image.Image | str | DataTypes.KarmicBind]]) -> Image.Image:
    # Расчеты
    size = (1200, 800)
    size_factor = min(size) / 600

    main_objects_size = size[1] // 6
    gap = size[1] // 150

    radius = 20 * size_factor
    main_box_gap = main_objects_size // 4

    karma = db.get_karma(ozernik.id)
    status = get_status(karma)

    user_cubes = get_cubes(ozernik.id)
    user_cube = get_cube_status(ozernik.id)
    next_cube = get_next_cube(ozernik.id)

    full_binds = sorted(full_binds, key=lambda b: b["bind"].bind_karma, reverse=True)
    num_binds = 10

    # Определение цветов
    main_color = "#2A2C30"
    sep_color = "#24272A"
    sign_color = "#8B8B8B"
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
        fill=status_color,
        anchor='lt'
    )

    sign_font = ImageFont.truetype(
        assets.fonts['Roboto-Bold'],
        size=35 * size_factor,
    )

    sign_text = "Кармические связи"
    sign_xy = (avatar_full_box[2], main_box[3] - main_box_gap)
    max_sign_length = (main_box[2] - avatar_full_box[2]) - name_xy[0]

    if draw.textlength(username, font=name_font) >= max_sign_length:
        while username:
            username = username.rstrip() + '...'

            if draw.textlength(username, font=name_font) <= max_sign_length:
                break

            username = username[:-4]

    draw.text(
        sign_xy,
        sign_text,
        font=sign_font,
        fill='#9F9F9F',
        anchor='lb',
        spacing=10
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

            fill_width = bind_box_width * progress_factor
            fill_x = full_bind['box'][0] + fill_width

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
            format_duration(full_bind['bind'].bind_karma),
            font=bind_counter_font,
            fill=bind_color,
            anchor='ld'
        )

        if next_cube:
            bind_ambition_xy = (full_bind['box'][2] - bind_box_height / 4, bind_counter_xy[1])
            draw.text(
                bind_ambition_xy,
                format_duration(next_cube['required_karma']),
                font=bind_counter_font,
                fill=sign_color,
                anchor='rd'
            )

            print(bind_ambition_xy, bind_counter_xy)

    # Сохранение изображения
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

class Roles:
    def __init__(self, bot: OzernikiBot):
        self._bot = bot

        self._sansara_roles: dict[str, Role | None] = {
            'naraka': None,
            'preta': None,
            'animal': None,
            'human': None,
            'asur': None,
            'deva': None,
        }

        self._cubes_roles: dict[str, Role | None] = {
            'black_cube': None,
            'white_cube': None,
            'blue_cube': None,
            'gold_cube': None,
        }

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
    ) -> Role | None:
        guild: discord.Guild = self._bot.guild

        if not guild:
            return None

        role = self._sansara_roles[role_name]
        karma_roles = data.karma_roles

        if role is None:
            role = guild.get_role(karma_roles[role_name]['role_id'])

            if role is None:
                role = await guild.create_role(
                    reason="Восстановление роли.",
                    name=karma_roles[role_name]['name'],
                    colour=discord.Colour.from_str(
                        karma_roles[role_name]['color']
                    ),
                    hoist=False,
                    mentionable=False,
                )

                karma_roles[role_name]['role_id'] = role.id
                data.karma_roles = karma_roles

            self._sansara_roles[role_name] = role

        try:
            with open(assets.sansara[role_name], "rb") as f:
                icon_bytes = f.read()

            await role.edit(display_icon=icon_bytes)
        except Exception:
            pass

        return role

    async def get_cube_role(
        self,
        role_name: Literal[
            'black_cube',
            'white_cube',
            'blue_cube',
            'gold_cube',
        ],
    ) -> Role | None:
        guild: discord.Guild = self._bot.guild

        if not guild:
            return None

        role = self._cubes_roles[role_name]
        cube_roles = data.cube_roles

        if role is None:
            role = guild.get_role(cube_roles[role_name]['role_id'])

            if role is None:
                role = await guild.create_role(
                    reason="Восстановление роли.",
                    name=cube_roles[role_name]['name'],
                    colour=discord.Colour.from_str(
                        cube_roles[role_name]['color']
                    ),
                    hoist=False,
                    mentionable=False,
                )

                cube_roles[role_name]['role_id'] = role.id
                data.cube_roles = cube_roles

            self._cubes_roles[role_name] = role

        try:
            with open(assets.cubes[role_name], "rb") as f:
                icon_bytes = f.read()

            await role.edit(display_icon=icon_bytes)
        except Exception:
            pass

        return role

    async def get_sansara_roles(self) -> list[Role] | None:
        roles = []

        for role_name in self._sansara_roles:
            role = await self.get_sansara_role(role_name)

            if role is not None:
                roles.append(role)

        return roles or None

    async def get_cube_roles(self) -> list[Role] | None:
        roles = []

        for role_name in self._cubes_roles:
            role = await self.get_cube_role(role_name)

            if role is not None:
                roles.append(role)

        return roles or None

# ---------------------------------------------------------
# Базовый класс для view настроек
# ---------------------------------------------------------

class BaseSettingsView(discord.ui.LayoutView):
    def __init__(
        self,
        author: discord.Member,
        *,
        timeout: float = 300,
    ):
        super().__init__(timeout=timeout)

        self.author = author
        self.message: discord.Message | None = None

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Это меню принадлежит другому пользователю.",
                ephemeral=True,
            )
            return False

        return True

    async def update_message(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """
        Обновляет текущее сообщение этим View.

        Если interaction ещё не обработан —
        используем response.edit_message().

        Если уже был defer()/response —
        редактируем message напрямую.
        """

        if interaction.response.is_done():
            await interaction.message.edit(view=self)
        else:
            await interaction.response.edit_message(view=self)

    async def on_timeout(self) -> None:
        self.disable_all()

        if self.message is not None:
            await self.message.edit(view=self)

    def disable_all(self) -> None:
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        func_name = getattr(
            item.callback,
            "__name__",
            "unknown",
        )

        traceback_text = traceback.format_exc()

        print(
            f"Error in {func_name}:\n"
            f"{traceback_text}"
        )

        if interaction.response.is_done():
            await interaction.followup.send(
                f"Произошла ошибка:\n```python\n{error}\n```",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Произошла ошибка:\n```python\n{error}\n```",
                ephemeral=True,
            )


# ---------------------------------------------------------
# Вспомогательные классы кнопок
# ---------------------------------------------------------

class SecInputModal(discord.ui.Modal):
    def __init__(
        self,
        author: discord.Member,
        title: str,
        label: str,
        default: int,
        callback,
        min_value: int = 0,
        max_value: int = 1000,
    ):
        super().__init__(title=title)

        self.author = author
        self.callback_function = callback
        self.min_value = min_value
        self.max_value = max_value

        self.input = discord.ui.TextInput(
            label=label,
            default=str(default),
            placeholder=f"{min_value}–{max_value}",
            max_length=len(str(max_value)),
            required=True,
        )

        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(self.input.value)
        except ValueError:
            await interaction.response.send_message(
                "Введите целое число.",
                ephemeral=True,
            )
            return

        if not self.min_value <= value <= self.max_value:
            await interaction.response.send_message(
                f"Введите число от {self.min_value} "
                f"до {self.max_value}.",
                ephemeral=True,
            )
            return

        await self.callback_function(interaction, value)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        func_name = self.callback_function.__name__

        traceback_text = traceback.format_exc()

        print(
            f"Error in {func_name}:\n"
            f"{traceback_text}"
        )

        if interaction.response.is_done():
            await interaction.followup.send(
                f"Произошла ошибка:\n```python\n{error}\n```",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Произошла ошибка:\n```python\n{error}\n```",
                ephemeral=True,
            )

# ---------------------------------------------------------
# Общие настройки
# ---------------------------------------------------------

class GeneralSettingsView(BaseSettingsView):
    def __init__(
        self,
        author: discord.Member,
        main_view: "MainSettingsView",
    ):
        super().__init__(
            author,
            timeout=300,
        )

        self.main_view = main_view

        # Изменяемые значения
        self.karma_channel_id = data.karma_channel_id
        self.log_channel_id = data.log_channel_id
        self.blocked_channels_id = set(data.blocked_channels_id)

        # Флаги изменений
        self.karma_channel_edited = False
        self.log_channel_edited = False
        self.original_blocked_channels_id = set(data.blocked_channels_id)

        # -------------------------------------------------
        # Канал оповещений
        # -------------------------------------------------

        self.karma_channel_select = discord.ui.ChannelSelect(
            placeholder="Изменить канал",
            channel_types=[
                discord.ChannelType.text,
            ],
        )
        self.karma_channel_select.callback = (
            self.karma_channel_callback
        )

        # -------------------------------------------------
        # Канал логов
        # -------------------------------------------------

        self.blocked_channels_select = discord.ui.ChannelSelect(
            placeholder="Изменить каналы",
            channel_types=[
                discord.ChannelType.text,
            ]
        )
        self.blocked_channels_select.callback = (
            self.blocked_channels_callback
        )

        # -------------------------------------------------
        # Блокировка каналов
        # -------------------------------------------------

        self.log_channel_select = discord.ui.ChannelSelect(
            placeholder="Изменить канал",
            channel_types=[
                discord.ChannelType.text,
            ],
        )
        self.log_channel_select.callback = (
            self.log_channel_callback
        )

        # -------------------------------------------------
        # Кнопки
        # -------------------------------------------------

        self.confirm_button = discord.ui.Button(
            label="Подтвердить",
            style=discord.ButtonStyle.primary,
        )
        self.confirm_button.callback = self.confirm_callback

        self.back_button = discord.ui.Button(
            label="Назад",
            style=discord.ButtonStyle.secondary,
        )
        self.back_button.callback = self.back_callback

        # -------------------------------------------------
        # Layout
        # -------------------------------------------------

        self.update_container()

        self.update_buttons()

    def update_container(self):
        container = discord.ui.Container()

        container.add_item(
            discord.ui.TextDisplay("# Карма -> Общие")
        )

        container.add_item(
            discord.ui.Separator()
        )

        blocked_channels_text = discord.ui.TextDisplay(
            self.get_blocked_channels_text()
        )
        container.add_item(
            blocked_channels_text
        )

        blocked_channels_row = discord.ui.ActionRow(
            self.blocked_channels_select
        )
        container.add_item(
            blocked_channels_row
        )

        karma_channel_text = discord.ui.TextDisplay(
            self.get_karma_channel_text()
        )
        container.add_item(
            karma_channel_text
        )

        container.add_item(
            discord.ui.ActionRow(
                self.karma_channel_select
            )
        )

        log_channel_text = discord.ui.TextDisplay(
            self.get_log_channel_text()
        )
        container.add_item(
            log_channel_text
        )

        container.add_item(
            discord.ui.ActionRow(
                self.log_channel_select
            )
        )

        container.add_item(
            discord.ui.Separator()
        )

        container.add_item(
            discord.ui.ActionRow(
                self.confirm_button,
                self.back_button,
            )
        )

        self.clear_items()
        self.add_item(container)

    # -----------------------------------------------------
    # Текст
    # -----------------------------------------------------

    def get_karma_channel_text(self) -> str:
        if self.karma_channel_id is None:
            return "### Канал оповещений: не выбран"

        marker = "*" if self.karma_channel_edited else ""
        form = "__" if self.karma_channel_edited else ""

        return (
            f"### Канал оповещений: "
            f"{form}<#{self.karma_channel_id}>{form}{marker}"
        )

    def get_log_channel_text(self) -> str:
        if self.log_channel_id is None:
            return "### Канал логов: не выбран"

        marker = "*" if self.log_channel_edited else ""
        form = "__" if self.log_channel_edited else ""

        return (
            f"### Канал логов: "
            f"{form}<#{self.log_channel_id}>{form}{marker}"
        )

    def get_blocked_channels_text(self) -> str:
        if not self.blocked_channels_id and (
                self.original_blocked_channels_id
                == self.blocked_channels_id
        ):
            return "### Заблокированные каналы: не выбраны"

        parts = []

        for channel_id in (self.original_blocked_channels_id | self.blocked_channels_id):
            removed = (
                    channel_id in self.original_blocked_channels_id
                    and channel_id not in self.blocked_channels_id
            )

            added = (
                    channel_id not in self.original_blocked_channels_id
                    and channel_id in self.blocked_channels_id
            )

            if removed:
                parts.append(f"~~<#{channel_id}>~~\\*")
            elif added:
                parts.append(f"__<#{channel_id}>__\\*")
            else:
                parts.append(f"<#{channel_id}>")

        if not parts:
            return "### Заблокированные каналы: не выбраны"

        return (
                "### Заблокированные каналы: "
                + ", ".join(parts)
        )

    # -----------------------------------------------------
    # Кнопки
    # -----------------------------------------------------

    def update_buttons(self) -> None:
        edited = (
            self.karma_channel_edited
            or self.log_channel_edited
            or (
                self.blocked_channels_id
                != self.original_blocked_channels_id
            )
        )

        self.confirm_button.disabled = not edited

    # -----------------------------------------------------
    # Выбор каналов
    # -----------------------------------------------------

    async def karma_channel_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        channel = self.karma_channel_select.values[0]

        self.karma_channel_id = channel.id
        self.karma_channel_edited = (
            self.karma_channel_id != data.karma_channel_id
        )

        self.update_container()

        self.update_buttons()

        await self.update_message(interaction)

    async def log_channel_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        channel = self.log_channel_select.values[0]

        self.log_channel_id = channel.id
        self.log_channel_edited = (
            self.log_channel_id != data.log_channel_id
        )

        self.update_container()

        self.update_buttons()

        await self.update_message(interaction)

    async def blocked_channels_callback(
            self,
            interaction: discord.Interaction,
    ) -> None:

        channels = [channel.id for channel in self.blocked_channels_select.values]

        self.blocked_channels_id.symmetric_difference_update(channels)
        data_blocked_channels_id = data.blocked_channels_id

        self.blocked_channels_edited = (
            [id_ not in data_blocked_channels_id for id_ in self.blocked_channels_id]
        )
        if len(self.blocked_channels_id) < len(data_blocked_channels_id):
            self.blocked_channels_edited.append(True)

        self.update_container()
        
        self.update_buttons()

        await self.update_message(interaction)

    # -----------------------------------------------------
    # Подтверждение
    # -----------------------------------------------------

    async def confirm_callback(
            self,
            interaction: discord.Interaction,
    ) -> None:

        if self.karma_channel_edited:
            data.karma_channel_id = self.karma_channel_id
            self.karma_channel_edited = False

        if self.log_channel_edited:
            data.log_channel_id = self.log_channel_id
            self.log_channel_edited = False

        if self.blocked_channels_id != self.original_blocked_channels_id:
            data.blocked_channels_id = list(self.blocked_channels_id)
            self.original_blocked_channels_id = set(
                self.blocked_channels_id
            )

        self.update_container()

        self.update_buttons()

        await self.update_message(interaction)

    # -----------------------------------------------------
    # Назад
    # -----------------------------------------------------

    async def back_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        self.stop()

        self.main_view.active_view = None

        await interaction.response.edit_message(
            view=self.main_view
        )

class SansaraSettingsView(BaseSettingsView):
    def __init__(
        self,
        author: discord.Member,
        main_view: "MainSettingsView",
    ):
        super().__init__(
            author,
            timeout=300,
        )

        self.main_view = main_view

        # Оригинальные значения
        self.og_karma_message_delay = data.karma_message_delay
        self.og_karma_roles = data.karma_roles

        # Изменяемые значения
        self.karma_message_delay = data.karma_message_delay
        self.karma_roles = data.karma_roles

        # -------------------------------------------------
        # Канал оповещений
        # -------------------------------------------------

        self.karma_message_delay_button = discord.ui.Button(
            label="Изменить",
            style=discord.ButtonStyle.secondary,
        )
        self.karma_message_delay_button.callback = (
            self.karma_message_delay_callback
        )

        # -------------------------------------------------
        # Канал логов
        # -------------------------------------------------



        # -------------------------------------------------
        # Блокировка каналов
        # -------------------------------------------------



        # -------------------------------------------------
        # Кнопки
        # -------------------------------------------------

        self.confirm_button = discord.ui.Button(
            label="Подтвердить",
            style=discord.ButtonStyle.primary,
        )
        self.confirm_button.callback = self.confirm_callback

        self.back_button = discord.ui.Button(
            label="Назад",
            style=discord.ButtonStyle.secondary,
        )
        self.back_button.callback = self.back_callback

        # -------------------------------------------------
        # Layout
        # -------------------------------------------------

        self.container = discord.ui.Container()

        self.container.add_item(
            discord.ui.TextDisplay("# Карма -> Сансара")
        )

        self.container.add_item(
            discord.ui.Separator()
        )

        self.karma_delay_text = discord.ui.TextDisplay(
            self.get_karma_delay_text()
        )

        self.karma_delay_section = discord.ui.Section(
            self.karma_delay_text,
            accessory=self.karma_message_delay_button,
        )

        self.container.add_item(
            self.karma_delay_section
        )

        self.container.add_item(
            discord.ui.Separator()
        )

        self.container.add_item(
            discord.ui.ActionRow(
                self.confirm_button,
                self.back_button,
            )
        )

        self.add_item(self.container)

        self.update_buttons()

    # -----------------------------------------------------
    # Текст
    # -----------------------------------------------------

    def get_karma_delay_text(self) -> str:
        if self.og_karma_message_delay != self.karma_message_delay:
            return (
                f"### Задержка между сообщениями: "
                f"__`{self.karma_message_delay}`__*"
            )
        else:
            return (
                f"### Задержка между сообщениями: "
                f"`{self.karma_message_delay}`"
            )

    # -----------------------------------------------------
    # Кнопки
    # -----------------------------------------------------

    def update_buttons(self) -> None:
        edited = (
            self.og_karma_message_delay != self.karma_message_delay
        )
        print(edited)

        self.confirm_button.disabled = not edited

    # -----------------------------------------------------
    # Выбор каналов
    # -----------------------------------------------------

    async def karma_message_delay_callback(
            self,
            interaction: discord.Interaction,
    ):
        async def set_karma_message_delay(
                set_interaction: discord.Interaction,
                value: int,
        ):
            self.karma_message_delay = value

            self.karma_delay_text.content = self.get_karma_delay_text()

            self.update_buttons()

            await self.update_message(set_interaction)

        await interaction.response.send_modal(
            SecInputModal(
                author=self.author,
                title="Пауза выдачи кармы между сообщениями.",
                label="Количество секунд",
                default=self.karma_message_delay,
                min_value=0,
                max_value=10,
                callback=set_karma_message_delay,
            )
        )

    # -----------------------------------------------------
    # Подтверждение
    # -----------------------------------------------------

    async def confirm_callback(
            self,
            interaction: discord.Interaction,
    ) -> None:

        if self.og_karma_message_delay != self.karma_message_delay:
            data.karma_message_delay = self.karma_message_delay
            self.og_karma_message_delay = self.karma_message_delay

        self.karma_delay_text.content = self.get_karma_delay_text()

        self.update_buttons()

        await self.update_message(interaction)

    # -----------------------------------------------------
    # Назад
    # -----------------------------------------------------

    async def back_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        self.stop()

        self.main_view.active_view = None

        await interaction.response.edit_message(
            view=self.main_view
        )


# ---------------------------------------------------------
# Главное меню
# ---------------------------------------------------------

class MainSettingsView(BaseSettingsView):
    def __init__(
        self,
        author: discord.Member,
    ):
        super().__init__(
            author,
            timeout=300,
        )

        self.active_view: discord.ui.LayoutView | None = None

        # -------------------------------------------------
        # Кнопки
        # -------------------------------------------------

        self.general_button = discord.ui.Button(
            label="Общие",
            style=discord.ButtonStyle.primary,
        )
        self.general_button.callback = (
            self.general_callback
        )

        self.sansara_button = discord.ui.Button(
            label="Сансара",
            style=discord.ButtonStyle.primary,
        )
        self.sansara_button.callback = (
            self.sansara_callback
        )

        self.cubes_button = discord.ui.Button(
            label="Кубы",
            style=discord.ButtonStyle.primary,
        )
        self.cubes_button.callback = (
            self.cubes_callback
        )

        self.level_up_button = discord.ui.Button(
            label="Текст повышений",
            style=discord.ButtonStyle.primary,
        )
        self.level_up_button.callback = (
            self.level_up_callback
        )

        # -------------------------------------------------
        # Layout
        # -------------------------------------------------

        container = discord.ui.Container()

        container.add_item(
            discord.ui.TextDisplay(
                "# Карма"
            )
        )

        container.add_item(
            discord.ui.Separator()
        )

        container.add_item(
            discord.ui.ActionRow(
                self.general_button
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.sansara_button
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.cubes_button
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                self.level_up_button
            )
        )

        container.add_item(
            discord.ui.Separator()
        )

        self.add_item(container)

    # -----------------------------------------------------
    # General
    # -----------------------------------------------------

    async def general_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        self.active_view = GeneralSettingsView(
            self.author,
            self,
        )

        await interaction.response.edit_message(
            view=self.active_view
        )

    # -----------------------------------------------------
    # Остальные разделы
    # -----------------------------------------------------

    async def sansara_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        self.active_view = SansaraSettingsView(
            self.author,
            self,
        )

        await interaction.response.edit_message(
            view=self.active_view
        )

    async def cubes_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.send_message(
            "Настройки Кубов",
            ephemeral=True,
        )

    async def level_up_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.send_message(
            "Настройки текстов повышений",
            ephemeral=True,
        )


# ---------------------------------------------------------
# Класс модуля
# ---------------------------------------------------------

class KarmaSistem(commands.Cog):
    def __init__(self, bot: OzernikiBot):
        self.bot = bot
        self.roles = Roles(bot)
        self.delays = set()

    def cog_load(self):
        self.voice_karma_check.start()

    def cog_unload(self):
        self.voice_karma_check.cancel()

    async def add_delay(self, user_id: int, delay: int) -> None:
        self.delays.add(user_id)
        await asyncio.sleep(delay)
        self.delays.discard(user_id)

    def check_delay(self, user_id: int) -> bool:
        if user_id in self.delays:
            return True
        asyncio.create_task(self.add_delay(user_id, data.karma_message_delay))
        return False

    @staticmethod
    def check_channel(channel_id: int) -> bool:
        if channel_id in data.blocked_channels_id:
            return True
        return False

    async def update_cube_roles(self, member: discord.Member) -> Role:
        ozernik = db.get_user_by_discord_id(member.id)
        member_cube = get_cube_status(ozernik.id)

        member_cube_role = await self.roles.get_cube_role(
            member_cube['tag_name']
        )

        cube_roles = await self.roles.get_cube_roles()

        for role in member.roles:
            if role in cube_roles and role != member_cube_role:
                await member.remove_roles(role)

        await member.add_roles(member_cube_role)

        return member_cube_role

    async def update_sansara_roles(self, member: discord.Member) -> None:
        ozernik = db.get_user_by_discord_id(member.id)
        karma = db.get_karma(ozernik.id)
        member_status = get_status(karma)

        member_sansara_role = await self.roles.get_sansara_role(
            member_status['tag_name']
        )

        sansara_roles = await self.roles.get_sansara_roles()

        for role in member.roles:
            if role in sansara_roles and role != member_sansara_role:
                await member.remove_roles(role)

        await member.add_roles(member_sansara_role)

        return member_sansara_role

    async def give_level_up_message(self, member: discord.Member, old_karma: int, new_karma: int) -> None:
        channel = self.bot.get_channel(data.karma_channel_id if data.karma_channel_id else 0)

        if not channel:
            return

        if new_karma <= old_karma:
            return

        old_level = get_level(old_karma)
        new_level = get_level(new_karma)

        await self.update_sansara_roles(member)

        for level in range(old_level + 1, new_level + 1):
            await channel.send(f"{member.mention} достиг {level} уровня!")
            await asyncio.sleep(0.2)

    async def give_bind_up_message(self, member_1: discord.Member, member_2: discord.Member, old_karma: int, new_karma: int) -> None:
        channel = self.bot.get_channel(data.karma_channel_id if data.karma_channel_id else 0)

        if not channel:
            print('no karma channel')
            return

        if new_karma <= old_karma:
            return

        old_cube = get_cube(old_karma)
        new_cube = get_cube(new_karma)

        print(new_cube['name'], old_cube['name'])

        if new_cube['name'] != old_cube['name']:
            ozernik_1 = db.get_user_by_discord_id(member_1.id)
            ozernik_2 = db.get_user_by_discord_id(member_2.id)

            bind = db.get_karmic_bind(ozernik_1.id, ozernik_2.id)

            avatar_1 = await get_discord_avatar(member_1)
            avatar_2 = await get_discord_avatar(member_2)

            print(avatar_1.size)
            print(avatar_2.size)

            postcard_bytes = create_bind_up_postcard(
                bind=bind,
                avatar_1_image=avatar_1,
                avatar_2_image=avatar_2
            )

            file = discord.File(postcard_bytes, filename=f"{member_1.name}_{member_2.name}_postcard.png")

            await channel.send(
                f"{member_1.mention} и {member_2.mention} получили воспоминание!",
                file=file,
            )
            await asyncio.sleep(0.2)

    async def give_cube_up_message(self, member: discord.Member, old_cube: dict, new_cube: dict) -> None:
        try:
            channel = self.bot.get_channel(
                data.karma_channel_id if data.karma_channel_id else 0
            )

            if not channel:
                return

            if old_cube == new_cube:
                return

            role = await self.update_cube_roles(member)
            cube_name = get_cube_status(db.get_user_by_discord_id(member.id).id)['name']

            title = f"**{member.display_name} получил новый Куб!**"
            description = ''

            match cube_name:
                case 'Черный Куб':
                    description = ("**Столкнись со своими демонами.**\n"
                                   f"Теперь у вас есть {role.mention}")

                case 'Белый Куб':
                    description = ("***«Переживи свою прошлую жизнь вновь»***\n"
                                   "**Вы достигли 10 Белых связей.\n"
                                   f"Теперь у вас есть {role.mention}**")

                case 'Синий Куб':
                    description = ("**Прошлое никогда не умирает, оно даже не прошлое.**\n"
                                   f"**Теперь у вас есть {role.mention}**")

                case 'Золотой Куб':
                    description = ("**Воспоминания — это не только ключ к прошлому, но и к будущему.**\n"
                                   f"**Теперь у вас есть {role.mention}**")

            embed = discord.Embed(
                title=title,
                description=description,
                colour=role.colour
            )

            embed.set_thumbnail(url=member.avatar.url)

            embed.set_footer(text=role.guild.name, icon_url=role.guild.icon.url)

            await channel.send(
                member.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                ),
            )
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            if message.guild is None:
                return

            member = message.guild.get_member(message.author.id)

            if member is None:
                return

            if message.content == 'qwe':
                print('qwe')
                await self.give_cube_up_message(member, None, data.cube_roles['gold_cube'])

            ozernik = self.bot.db_ensure_user(member)

            if message.content == ',r':
                avatar = await get_discord_avatar(member)
                rank_card = create_rank_card(ozernik, avatar, member.display_name)
                file = discord.File(rank_card)
                await message.reply(file=file)

            if self.check_delay(ozernik.id):
                return

            if self.check_channel(message.channel.id):
                return

            old_karma, new_karma = db.add_karma(ozernik.id, 1, True)

            await self.give_level_up_message(member, old_karma.karma, new_karma.karma)
        except Exception:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")

    @tasks.loop(seconds=5)
    async def voice_karma_check(self):
        try:
            if not self.bot.ready:
                return
            for channel in self.bot.guild.voice_channels:
                members = channel.members

                if not members:
                    continue

                print(channel.name)

                for user_1, user_2 in combinations(members, 2):
                    ozernik_1 = self.bot.db_ensure_user(user_1)
                    ozernik_2 = self.bot.db_ensure_user(user_2)

                    bind = db.add_bind_karma(ozernik_1.id, ozernik_2.id, karma=1)
                    print("    ", user_1.name, user_2.name, bind.bind_karma)

                    await self.give_bind_up_message(user_1, user_2, bind.bind_karma - 1, bind.bind_karma)
        except Exception:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")

    # Команды
    @app_commands.command(name='settings_karma', description='Настройка системы Кармы.')
    @app_commands.guilds(config.GUILD_ID)
    async def settings_karma(self, interaction: Interaction):
        try:
            # if not interaction.user.guild_permissions.administrator:
            #     await interaction.response.send_message(
            #         "Только для Администраторов.",
            #         ephemeral=True
            #     )
            #     return

            view = MainSettingsView(interaction.user)

            await interaction.response.send_message(view=view)
            view.message = await interaction.original_response()
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='rank_sansara', description='Просмотр ранга Сансары.')
    @app_commands.guilds(config.GUILD_ID)
    async def rank_sansara(self, interaction: Interaction, user: discord.Member = None):
        try:
            if user is None:
                user = interaction.user
            ozernik = db.get_user_by_discord_id(user.id)

            avatar = await get_discord_avatar(user)

            rank_card_bytes = create_rank_card(ozernik, avatar, user.display_name)

            file = discord.File(rank_card_bytes, filename=f"{user.name}_sansara_rank.png")

            await interaction.response.send_message(file=file)
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

    @app_commands.command(name='rank_cube', description='Просмотр ранга Куба.')
    @app_commands.guilds(config.GUILD_ID)
    async def rank_cube(self, interaction: Interaction, user: discord.Member = None):
        try:
            await interaction.response.defer()
            if user is None:
                user = interaction.user
            ozernik = db.get_user_by_discord_id(user.id)

            avatar = await get_discord_avatar(user)
            full_binds = await get_full_binds(user)

            rank_card_bytes = create_cube_cart(
                ozernik,
                avatar,
                user.display_name,
                full_binds,
            )

            file = discord.File(rank_card_bytes, filename=f"{user.name}_cube_rank.png")

            await interaction.followup.send(file=file)
        except Exception as e:
            func_name = inspect.currentframe().f_code.co_name
            tb = traceback.format_exc()
            print(f"Error in {func_name}: {tb}")
            await interaction.followup.send(f"Произошла ошибка:\n```\n{e}\n```", ephemeral=True)

async def setup(bot):
    await bot.add_cog(KarmaSistem(bot))
    pass