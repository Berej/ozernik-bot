"""
Картинки плашки *i (Pillow). Вызываются через asyncio.to_thread и к базе не обращаются:
всё нужное уже лежит в словаре данных (_stats_data).

Оформление — как у страниц карточек кармы: тёмные скруглённые плашки на прозрачном фоне,
Roboto. Цвет имени — цвет участника на сервере (по старшей цветной роли).
"""

import math
import threading
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from utilities import assets, drop_missing_glyphs, fit_text

from ._stats_data import PERIODS, format_count, format_hours, format_short_date

WIDTH = 900
GAP = 5
RADIUS = 20
PADDING = 20

MAIN_COLOR = "#2A2C30"
ROW_COLOR = "#24272A"
SIGN_COLOR = "#8B8B8B"
TEXT_COLOR = "#BFBFBF"
VALUE_COLOR = "#ECECEC"
MESSAGES_COLOR = "#57C785"
VOICE_COLOR = "#E0679E"


# Шрифты — своя память у каждого потока. Картинки рисуются в пуле потоков (asyncio.to_thread),
# и две плашки могут рисоваться одновременно; один объект шрифта FreeType в двух потоках сразу
# — путь к редким и непонятным сбоям. Общая на процесс память (lru_cache) это допускала бы.
_fonts = threading.local()


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    cache = getattr(_fonts, "cache", None)
    if cache is None:
        cache = _fonts.cache = {}
    key = (size, bold)
    if key not in cache:
        cache[key] = ImageFont.truetype(assets.fonts["Roboto-Bold" if bold else "Roboto"], size=size)
    return cache[key]


def to_png(image: Image.Image) -> BytesIO:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def panel(draw: ImageDraw.ImageDraw, box) -> None:
    draw.rounded_rectangle(box, radius=RADIUS, fill=MAIN_COLOR)


def panel_title(draw: ImageDraw.ImageDraw, x: int, y: int, text: str) -> None:
    # Якорь «la» — по линии верха букв шрифта, а не по самой высокой букве строки: с «lt»
    # заголовок «ВОЙС» вставал ниже соседних — кратка над «Й» выше остальных букв.
    draw.text((x + PADDING, y + 16), text, font=font(17, True), fill=SIGN_COLOR, anchor="la")


def clean(text: str, used_font, fallback: str) -> str:
    """Без символов, которых нет в Roboto (эмодзи, «・»), — иначе на картинке квадратики."""
    return drop_missing_glyphs(text, used_font) or fallback


def draw_header(image: Image.Image, avatar: Image.Image, name: str, name_color: str, subtitle: str,
                height: int = 130, avatar_size: int = 100) -> None:
    draw = ImageDraw.Draw(image)
    panel(draw, (0, 0, WIDTH, height))

    margin = (height - avatar_size) // 2
    circle = avatar.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
    mask = Image.new("L", circle.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)
    circle.putalpha(mask)
    image.paste(circle, (margin, margin), circle)

    text_x = margin * 2 + avatar_size
    room = WIDTH - text_x - margin
    name_font, sub_font = font(38, True), font(22)

    # Ник и подпись — один блок по центру шапки (на уровне середины аватара). Раньше ник
    # прижимался к верху, подпись — к низу, и между ними зияла дыра. Высоты — по меркам
    # шрифта («Нд»: заглавная сверху, хвост «д» снизу), а не по конкретному тексту: иначе
    # ник без хвостатых букв сидел бы на другой высоте, чем ник с ними.
    _, name_cap, _, name_tail = name_font.getbbox("Нд", anchor="ls")
    _, sub_cap, _, sub_tail = sub_font.getbbox("Нд", anchor="ls")
    gap = 10
    block = -name_cap + name_tail + gap + -sub_cap + sub_tail
    name_baseline = (height - block) / 2 - name_cap
    sub_baseline = name_baseline + name_tail + gap - sub_cap

    draw.text((text_x, name_baseline), fit_text(draw, clean(name, name_font, "Озёрник"), name_font, room),
              font=name_font, fill=name_color, anchor="ls")
    draw.text((text_x, sub_baseline), fit_text(draw, subtitle, sub_font, room),
              font=sub_font, fill=TEXT_COLOR, anchor="ls")


def draw_rows(draw, x1: int, x2: int, top: int, rows: list[tuple[str, str]], value_color: str = VALUE_COLOR,
              row_height: int = 34, step: int = 40) -> None:
    """Строки-«пилюли»: подпись слева, значение справа."""
    for n, (label, value) in enumerate(rows):
        y = top + n * step
        draw.rounded_rectangle((x1 + 12, y, x2 - 12, y + row_height), radius=10, fill=ROW_COLOR)
        draw.text((x1 + 26, y + row_height // 2), label, font=font(21), fill=SIGN_COLOR, anchor="lm")
        draw.text((x2 - 26, y + row_height // 2), value, font=font(22, True), fill=value_color, anchor="rm")


def draw_speaker(draw, x: int, y: int, color: str) -> None:
    """Значок войса (в Roboto нет эмодзи 🔊): динамик и две дуги, высота ~18."""
    draw.polygon([(x, y + 6), (x + 5, y + 6), (x + 11, y), (x + 11, y + 18), (x + 5, y + 12), (x, y + 12)], fill=color)
    draw.arc((x + 9, y + 3, x + 19, y + 15), start=-55, end=55, fill=color, width=2)
    draw.arc((x + 9, y - 2, x + 25, y + 20), start=-55, end=55, fill=color, width=2)


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def known_points(values: list, x1: float, x2: float, y_of) -> list[tuple[float, float]]:
    """Точки линии без дней до начала учёта (None): линия начинается с первого посчитанного дня."""
    count = len(values)
    return [
        (x1 + (x2 - x1) * i / max(count - 1, 1), y_of(value))
        for i, value in enumerate(values) if value is not None
    ]


def draw_lines(image: Image.Image, box, series: list[tuple[list, str]], fill_alpha: int = 50) -> None:
    """Мини-график: каждая линия масштабируется к своему максимуму, под линией — полупрозрачная заливка."""
    x1, y1, x2, y2 = box
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    for n in range(3):
        y = y1 + (y2 - y1) * n / 2
        draw.line((x1, y, x2, y), fill=ROW_COLOR, width=2)

    for values, color in series:
        top = max((value for value in values if value is not None), default=0) or 1
        points = known_points(values, x1, x2, lambda value: y2 - (y2 - y1) * value / top)
        if not points:
            continue
        rgb = hex_to_rgb(color)
        if len(points) > 1:
            draw.polygon([(points[0][0], y2), *points, (points[-1][0], y2)], fill=(*rgb, fill_alpha))
            draw.line(points, fill=(*rgb, 255), width=3, joint="curve")
        else:
            x, y = points[0]
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(*rgb, 255))

    image.alpha_composite(layer)


def draw_legend(draw, right: int, y: int, items: list[tuple[str, str]]) -> None:
    """Легенда справа налево: ● Сообщения ● Войс."""
    x = right
    for label, color in reversed(items):
        width = draw.textlength(label, font=font(18))
        draw.text((x, y), label, font=font(18), fill=TEXT_COLOR, anchor="rm")
        x -= width + 8
        draw.ellipse((x - 10, y - 5, x, y + 5), fill=color)
        x -= 24


def footer_text(notes: list[str]) -> str:
    text = "Последние 30 дней · время МСК"
    if notes:
        text += " · учёт: " + ", ".join(notes)
    return text


# ---------- Главная страница ----------

OVERVIEW_HEIGHT = 574


def render_overview(avatar: Image.Image, name: str, name_color: str, data: dict) -> BytesIO:
    image = Image.new("RGBA", (WIDTH, OVERVIEW_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw_header(image, avatar, name, name_color, data["join_line"])

    # Ряд плашек: места, сообщения, войс
    top, bottom = 135, 305
    width = (WIDTH - 2 * GAP) // 3
    columns = [(n * (width + GAP), WIDTH if n == 2 else n * (width + GAP) + width) for n in range(3)]

    tiles = (
        ("МЕСТО НА СЕРВЕРЕ", [(label, f"#{place}" if place else "—") for label, place in data["places"]], VALUE_COLOR),
        ("СООБЩЕНИЯ", [(f"{days} д", format_count(data["totals"]["messages"][days])) for days in PERIODS], MESSAGES_COLOR),
        ("ВОЙС", [(f"{days} д", format_hours(data["totals"]["voice_presence"][days])) for days in PERIODS], VOICE_COLOR),
    )

    for (x1, x2), (title, rows, color) in zip(columns, tiles):
        panel(draw, (x1, top, x2, bottom))
        panel_title(draw, x1, top, title)
        draw_rows(draw, x1, x2, top + 48, rows, value_color=color)

    # Топ каналов и мини-график
    top, bottom = 310, 525
    split = 445
    panel(draw, (0, top, split, bottom))
    panel_title(draw, 0, top, "ТОП КАНАЛОВ")

    if data["channels"]:
        row_font = font(21)
        for n, (kind, channel_name, amount) in enumerate(data["channels"]):
            y = top + 48 + n * 40
            draw.rounded_rectangle((12, y, split - 12, y + 34), radius=10, fill=ROW_COLOR)
            if kind == "text":
                draw.text((37, y + 17), "#", font=font(22, True), fill=MESSAGES_COLOR, anchor="mm")
                value, color = f"{format_count(amount)} сообщ.", MESSAGES_COLOR
            else:
                draw_speaker(draw, 25, y + 8, VOICE_COLOR)
                value, color = format_hours(amount), VOICE_COLOR
            value_width = draw.textlength(value, font=font(21, True))
            room = split - 12 - 26 - value_width - 16 - 64
            label = fit_text(draw, clean(channel_name, row_font, "канал"), row_font, room)
            draw.text((64, y + 17), label, font=row_font, fill=TEXT_COLOR, anchor="lm")
            draw.text((split - 26, y + 17), value, font=font(21, True), fill=color, anchor="rm")
    else:
        draw.text((split // 2, (top + bottom) // 2 + 14), "Пока пусто", font=font(22), fill=SIGN_COLOR, anchor="mm")

    panel(draw, (split + GAP, top, WIDTH, bottom))
    panel_title(draw, split + GAP, top, "ГРАФИК")
    draw_legend(draw, WIDTH - PADDING, top + 25, [("Сообщения", MESSAGES_COLOR), ("Войс", VOICE_COLOR)])
    days = data["days"]
    draw_lines(image, (split + GAP + PADDING, top + 58, WIDTH - PADDING, bottom - 22), [
        ([day["messages"] for day in days], MESSAGES_COLOR),
        ([day["voice"] for day in days], VOICE_COLOR),
    ])

    # Подвал
    panel(draw, (0, 530, WIDTH, OVERVIEW_HEIGHT))
    draw.text((PADDING, 552), fit_text(draw, footer_text(data["notes"]), font(18), WIDTH - 2 * PADDING),
              font=font(18), fill=SIGN_COLOR, anchor="lm")

    return to_png(image)


# ---------- Страница «График» ----------

CHART_HEIGHT = 480


AXIS_DIVISIONS = 4
NICE_STEPS = (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10)


def nice_top(value: float, whole: bool) -> float:
    """
    Верх шкалы из круглого шага: 4 деления по 1, 1,5, 2, 2,5, 3, 4, 5, 7,5 × 10ⁿ.
    Округлять надо шаг, а не верх: верх 250 на 4 деления дал бы подписи «62,5» и «187,5».
    whole — шаг целый (сообщения не бывают дробными).
    """
    need = max(value, 0) / AXIS_DIVISIONS
    if need <= 0:
        return AXIS_DIVISIONS
    magnitude = 10 ** math.floor(math.log10(need))
    for step in NICE_STEPS:
        candidate = step * magnitude
        if candidate >= need and (not whole or candidate == int(candidate)):
            return candidate * AXIS_DIVISIONS
    return 10 * magnitude * AXIS_DIVISIONS


def axis_label(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def render_chart(avatar: Image.Image, name: str, name_color: str, data: dict) -> BytesIO:
    image = Image.new("RGBA", (WIDTH, CHART_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw_header(image, avatar, name, name_color, "График за 30 дней · время МСК", height=120, avatar_size=90)

    top = 125
    panel(draw, (0, top, WIDTH, CHART_HEIGHT))

    total = f"Сообщения: {format_count(data['messages'])}"
    draw.ellipse((PADDING, top + 22, PADDING + 10, top + 32), fill=MESSAGES_COLOR)
    draw.text((PADDING + 18, top + 27), total, font=font(19, True), fill=TEXT_COLOR, anchor="lm")
    x = PADDING + 18 + draw.textlength(total, font=font(19, True)) + 28
    draw.ellipse((x, top + 22, x + 10, top + 32), fill=VOICE_COLOR)
    draw.text((x + 18, top + 27), f"Войс: {format_hours(data['voice'])}", font=font(19, True),
              fill=TEXT_COLOR, anchor="lm")
    if data["notes"]:
        draw.text((WIDTH - PADDING, top + 27), "учёт: " + ", ".join(data["notes"]), font=font(16),
                  fill=SIGN_COLOR, anchor="rm")

    days = data["days"]
    left, right, plot_top, plot_bottom = 70, WIDTH - 70, top + 62, CHART_HEIGHT - 48
    messages_top = nice_top(max((day["messages"] or 0 for day in days), default=0), whole=True)
    hours_top = nice_top(max((day["voice"] or 0 for day in days), default=0) / 60, whole=False)

    # Сетка и подписи осей: слева — сообщения, справа — часы
    for n in range(AXIS_DIVISIONS + 1):
        y = plot_bottom - (plot_bottom - plot_top) * n / AXIS_DIVISIONS
        draw.line((left, y, right, y), fill=ROW_COLOR, width=2)
        draw.text((left - 10, y), axis_label(messages_top * n / AXIS_DIVISIONS), font=font(15),
                  fill=MESSAGES_COLOR, anchor="rm")
        draw.text((right + 10, y), axis_label(hours_top * n / AXIS_DIVISIONS), font=font(15),
                  fill=VOICE_COLOR, anchor="lm")

    slot = (right - left) / len(days)
    bar_width = max(slot * 0.6, 2)
    for i, day in enumerate(days):
        center = left + slot * (i + 0.5)
        height = (plot_bottom - plot_top) * (day["messages"] or 0) / messages_top
        if height >= 1:
            draw.rounded_rectangle((center - bar_width / 2, plot_bottom - height, center + bar_width / 2, plot_bottom),
                                   radius=3, fill=MESSAGES_COLOR)
        # Подпись дня — каждые 5 дней и последний (сегодня)
        if i % 5 == 0 or i == len(days) - 1:
            draw.text((center, plot_bottom + 12), format_short_date(day["day"]), font=font(14), fill=SIGN_COLOR, anchor="mt")

    points = [
        (left + slot * (i + 0.5), plot_bottom - (plot_bottom - plot_top) * day["voice"] / 60 / hours_top)
        for i, day in enumerate(days) if day["voice"] is not None
    ]
    if len(points) > 1:
        draw.line(points, fill=VOICE_COLOR, width=4, joint="curve")
    for x, y in points:
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=VOICE_COLOR)

    return to_png(image)


# ---------- Колонки со строками (страницы «Топ каналов» и «Личные войсы») ----------

PAGE_TOP = 125   # где начинаются плашки под шапкой высотой 120
LIST_ROW_HEIGHT = 46
LIST_ROW_STEP = 54


def draw_ranked_column(draw, x1: int, x2: int, title: str, rows: list[tuple[str, int]],
                       format_value, color: str, icon: str | None = None) -> None:
    """
    Плашка со строками-«пилюлями» «название — значение», как «Топ каналов» на главной.
    Без полосок (решение Alium 2026-11-14): полоска «доля от первого места» у первой строки
    всегда полная и ничего не сообщала сверх чисел.
    icon: «#» (текстовый канал), «voice» (динамик) или None.
    """
    panel(draw, (x1, PAGE_TOP, x2, CHART_HEIGHT))
    panel_title(draw, x1, PAGE_TOP, title)

    if not rows:
        draw.text(((x1 + x2) // 2, (PAGE_TOP + CHART_HEIGHT) // 2 + 14), "Пока пусто", font=font(22),
                  fill=SIGN_COLOR, anchor="mm")
        return

    row_font = font(23, True)
    y = PAGE_TOP + 50
    for label, amount in rows:
        middle = y + LIST_ROW_HEIGHT // 2
        draw.rounded_rectangle((x1 + 12, y, x2 - 12, y + LIST_ROW_HEIGHT), radius=10, fill=ROW_COLOR)

        text_x = x1 + 26
        if icon == "#":
            draw.text((text_x + 7, middle), "#", font=row_font, fill=color, anchor="mm")
            text_x += 26
        elif icon == "voice":
            draw_speaker(draw, text_x, middle - 9, color)
            text_x += 32

        value = format_value(amount)
        value_width = draw.textlength(value, font=row_font)
        room = x2 - 26 - value_width - 16 - text_x
        draw.text((text_x, middle), fit_text(draw, clean(label, row_font, "—"), row_font, room),
                  font=row_font, fill=TEXT_COLOR, anchor="lm")
        draw.text((x2 - 26, middle), value, font=row_font, fill=color, anchor="rm")
        y += LIST_ROW_STEP


def two_columns():
    width = (WIDTH - GAP) // 2
    return (0, width), (width + GAP, WIDTH)


def new_page(avatar: Image.Image, name: str, name_color: str, subtitle: str):
    image = Image.new("RGBA", (WIDTH, CHART_HEIGHT), (0, 0, 0, 0))
    draw_header(image, avatar, name, name_color, subtitle, height=120, avatar_size=90)
    return image, ImageDraw.Draw(image)


# ---------- Страница «Топ каналов» ----------

def render_channels(avatar: Image.Image, name: str, name_color: str, data: dict) -> BytesIO:
    subtitle = f"Топ каналов · {data['title']}"
    if data["note"]:
        subtitle += f" · {data['note']}"
    image, draw = new_page(avatar, name, name_color, subtitle)

    (l1, l2), (r1, r2) = two_columns()
    draw_ranked_column(draw, l1, l2, "ТЕКСТОВЫЕ КАНАЛЫ", data["text"], format_count, MESSAGES_COLOR, icon="#")
    draw_ranked_column(draw, r1, r2, "ГОЛОСОВЫЕ КАНАЛЫ", data["voice"], format_hours, VOICE_COLOR, icon="voice")
    return to_png(image)


# ---------- Страница «Личные войсы» ----------

def render_voices(avatar: Image.Image, name: str, name_color: str, data: dict) -> BytesIO:
    subtitle = "Личные войсы · за всё время"
    if data["own"]:
        subtitle += f" · в своём — {format_hours(data['own'])}"
    image, draw = new_page(avatar, name, name_color, subtitle)

    (l1, l2), (r1, r2) = two_columns()
    draw_ranked_column(draw, l1, l2, "В ЧУЖИХ ВОЙСАХ", data["hosts"], format_hours, VOICE_COLOR)
    draw_ranked_column(draw, r1, r2, "ГОСТИ ЛИЧНОГО ВОЙСА", data["guests"], format_hours, VOICE_COLOR)
    return to_png(image)


# ---------- Страница «Ивенты» (переехала из ,rs) ----------

KARMA_COLOR = "#E8C15A"


def render_events(avatar: Image.Image, name: str, name_color: str, data: dict) -> BytesIO:
    """Карма за ивенты (награды администрации через /karma_add) и последние награды."""
    image, draw = new_page(avatar, name, name_color,
                           f"Ивенты · карма за ивенты: {data['total']} · наград: {data['count']}")

    panel(draw, (0, PAGE_TOP, WIDTH, CHART_HEIGHT))
    panel_title(draw, 0, PAGE_TOP, "ПОСЛЕДНИЕ НАГРАДЫ")

    row_font = font(23, True)
    y = PAGE_TOP + 50
    for reason, amount, date, award_id in data["awards"]:
        draw.rounded_rectangle((12, y, WIDTH - 12, y + 56), radius=10, fill=ROW_COLOR)
        value = f"+{amount}"
        value_width = draw.textlength(value, font=row_font)
        label = fit_text(draw, clean(reason, row_font, "Ивент"), row_font, WIDTH - 12 - 26 - value_width - 16 - 26)
        draw.text((26, y + 8), label, font=row_font, fill=TEXT_COLOR, anchor="la")
        draw.text((26, y + 36), f"{date} · #{award_id}", font=font(17), fill=SIGN_COLOR, anchor="la")
        draw.text((WIDTH - 26, y + 28), value, font=row_font, fill=KARMA_COLOR, anchor="rm")
        y += 61
    return to_png(image)


# ---------- Страница «Другие обличья» (переехала из ,rs) ----------

def render_guises(avatar: Image.Image, name: str, name_color: str, data: dict) -> BytesIO:
    """
    Все аккаунты человека: основной первым, твинки с датой привязки; до 8, дальше «и ещё N».
    data['accounts']: [{'name', 'avatar' (Image или None — аккаунт не найти), 'main', 'since'}].
    """
    accounts, total = data["accounts"], data["total"]
    image, draw = new_page(avatar, name, name_color, f"Другие обличья · аккаунтов: {total}")
    panel(draw, (0, PAGE_TOP, WIDTH, CHART_HEIGHT))

    column_width = WIDTH // 2
    avatar_size = 58
    name_font = font(25, True)
    for n, account in enumerate(accounts):
        column, row = divmod(n, 4)
        x = column * column_width + PADDING
        y = PAGE_TOP + 18 + row * 82

        if account["avatar"] is not None:
            circle = account["avatar"].resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
            mask = Image.new("L", circle.size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)
            circle.putalpha(mask)
            image.paste(circle, (x, y), circle)
        else:
            draw.ellipse((x, y, x + avatar_size, y + avatar_size), fill=ROW_COLOR)

        text_x = x + avatar_size + 16
        room = (column + 1) * column_width - PADDING - text_x
        draw.text((text_x, y + 4), fit_text(draw, clean(account["name"], name_font, "Аккаунт"), name_font, room),
                  font=name_font, fill=name_color if account["main"] else TEXT_COLOR, anchor="la")
        if account["main"]:
            note = "основной аккаунт"
        else:
            note = f"твинк · с {account['since']}" if account["since"] else "твинк"
        draw.text((text_x, y + 40), note, font=font(18), fill=SIGN_COLOR, anchor="la")

    if total > len(accounts):
        draw.text((WIDTH - PADDING, CHART_HEIGHT - 14), f"и ещё {total - len(accounts)}", font=font(18),
                  fill=SIGN_COLOR, anchor="rb")
    return to_png(image)
