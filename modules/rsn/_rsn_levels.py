"""
Уровни наказания РСН 2.0 (решение Alium 2026-11-07). Уровень считается по очкам ДО списания.

  31–50  роль «Мут»:       видит каналы, не пишет нигде, кроме чата дела, не заходит в войс; срок ×1
  11–30  роль «Изоляция»:  не видит каналы (кроме чата дела); срок ×2
   0–10  роль «Изоляция»:  то же; срок ×3

Отдельно от мута — метка (роль), пока очков ≤ 10: карма не начисляется, роли за ивенты админы
не выдают. Выдаётся и снимается по текущим очкам, а не по уровню мута.

Нативный тайм-аут Discord не подходит: он запрещает писать везде, включая чат дела.
"""

from dataclasses import dataclass

import discord

MARKER_MAX_POINTS = 10


@dataclass(frozen=True)
class Level:
    name: str
    multiplier: int     # во сколько раз умножается срок мута
    isolation: bool     # True — роль «Изоляция» (не видит каналы), False — роль «Мут»


LEVEL_HIGH = Level("31–50", 1, False)
LEVEL_MIDDLE = Level("11–30", 2, True)
LEVEL_LOW = Level("1–10", 3, True)


def level_for(points_before: int | None) -> Level:
    """Уровень по очкам до списания. Нет данных (старые записи) — как у верхнего уровня."""
    if points_before is None or points_before >= 31:
        return LEVEL_HIGH
    if points_before >= 11:
        return LEVEL_MIDDLE
    return LEVEL_LOW


def needs_marker(points: int) -> bool:
    """Метка уровня 1–10 нужна, пока очков не больше 10."""
    return points <= MARKER_MAX_POINTS


# ---------- Запреты ролей на каналах (/rsn_setup) ----------

# «Мут»: видит, но не пишет (и в ветках), не реагирует, не пользуется командами, не заходит в войс.
MUTE_OVERWRITE = discord.PermissionOverwrite(
    send_messages=False,
    send_messages_in_threads=False,
    create_public_threads=False,
    create_private_threads=False,
    add_reactions=False,
    use_application_commands=False,
    connect=False,
    speak=False,
)

# «Изоляция»: не видит. Этого достаточно — чего не видно, туда не напишешь и не зайдёшь.
ISOLATION_OVERWRITE = discord.PermissionOverwrite(view_channel=False)


def plan_overwrites(channels, roles_and_overwrites, skip_channel_ids=()) -> list[tuple]:
    """
    Какие запреты надо поставить: (канал, роль, запрет) для каждого канала и категории,
    где у роли ещё нет ровно такого запрета. Чаты дел пропускаются — там свои права.

    Каналы обходятся все, включая вне категорий: права категории в Discord не применяются
    к каналам сами — «синхронизирован» в клиенте лишь значит «сейчас совпадают».
    """
    plan = []
    for channel in channels:
        if channel.id in skip_channel_ids:
            continue
        for role, overwrite in roles_and_overwrites:
            if channel.overwrites_for(role) != overwrite:
                plan.append((channel, role, overwrite))
    return plan


def case_chat_overwrites(guild, staff_roles, members) -> dict:
    """
    Права чата дела: никто не видит, кроме админов/модераторов, участников дела и самого бота.
    Личное разрешение участнику сильнее запрета его ролей «Мут»/«Изоляция» в этом канале.
    """
    allow = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        attach_files=True, embed_links=True, add_reactions=True,
    )
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    for role in staff_roles:
        overwrites[role] = allow
    for member in members:
        overwrites[member] = allow
    overwrites[guild.me] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        manage_channels=True, manage_permissions=True, attach_files=True,
    )
    return overwrites


MEMBER_CHAT_OVERWRITE = discord.PermissionOverwrite(
    view_channel=True, send_messages=True, read_message_history=True,
    attach_files=True, embed_links=True, add_reactions=True,
)
