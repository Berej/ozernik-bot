"""
Чат дела РСН 2.0 (решения Alium 2026-11-07).

Текстовый канал «дело-N» вне категорий; видят его только участники дела, роли администраторов
и модераторов из rsn_data.json и сам бот. Живёт, пока по делу замучен хоть один участник
(или пока админ не закроет «чат без мута»). При закрытии — полный лог переписки файлом в канал
логов РСН, затем канал удаляется.

Лог — .txt (шапка дела + все сообщения с временем, автором, правками) и, если были вложения,
архив с ними. Вложения скачиваются заранее: ссылки на файлы Discord теперь «протухают»
примерно через сутки, и лог со ссылками через неделю показывал бы пустоту.
"""

import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import discord

from ._rsn_levels import MEMBER_CHAT_OVERWRITE, case_chat_overwrites, level_for

MOSCOW_TZ = timezone(timedelta(hours=3))

KIND_NAMES = {"mute": "мут", "ban": "бан", "warn": "без мута"}


def chat_name(case_id: int) -> str:
    return f"дело-{case_id}"


def moscow(ts: float | int | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")


def format_hours(hours: float | None) -> str:
    if not hours:
        return "бессрочно"
    minutes = int(round(hours * 60))
    days, rest = divmod(minutes, 24 * 60)
    h, m = divmod(rest, 60)
    parts = [f"{days}д" if days else "", f"{h}ч" if h else "", f"{m}м" if m else ""]
    return " ".join(p for p in parts if p)


# ---------- Канал ----------

async def open_case_chat(guild: discord.Guild, case_id: int, staff_roles, members) -> discord.TextChannel:
    """Создать чат дела вне категорий. В списке Discord такие каналы стоят над категориями."""
    return await guild.create_text_channel(
        name=chat_name(case_id),
        overwrites=case_chat_overwrites(guild, staff_roles, members),
        reason=f"РСН: чат дела #{case_id}",
    )


async def add_to_chat(channel: discord.TextChannel, member: discord.abc.User, case_id: int) -> None:
    await channel.set_permissions(member, overwrite=MEMBER_CHAT_OVERWRITE, reason=f"РСН: участник дела #{case_id}")


async def remove_from_chat(channel: discord.TextChannel, user: discord.abc.User, case_id: int) -> None:
    """Снять личный доступ (для ушедших с сервера нужен discord.User — голый ID discord.py не принимает)."""
    await channel.set_permissions(user, overwrite=None, reason=f"РСН: освобождён по делу #{case_id}")


# ---------- Лог переписки ----------

@dataclass
class ChatMessage:
    created_at: float
    author_name: str
    author_id: int
    content: str
    edited_at: float | None = None
    attachments: list[str] = field(default_factory=list)   # имена файлов в архиве


def format_transcript(case: dict, records: list[dict], names: dict[int, str], messages: list[ChatMessage],
                      closed_at: float) -> str:
    """
    Текст лога дела. Чистая функция (без Discord): всё нужное собрано заранее.

    :param names: Discord ID → имя (участники, модераторы, освободившие).
    """
    def who(user_id: int | None) -> str:
        if user_id is None:
            return "—"
        return f"{names.get(user_id, 'ID ' + str(user_id))} (ID {user_id})"

    lines = [
        f"РСН · Дело #{case['id']}",
        f"Открыто: {moscow(case['created_at'])} МСК · закрыто: {moscow(closed_at)} МСК",
        "",
        "Участники:",
    ]

    for record in records:
        kind = KIND_NAMES.get(record["kind"], record["kind"])
        parts = [kind]
        if record["kind"] == "mute":
            level = level_for(record.get("points_before"))
            parts.append(f"{format_hours(record['duration_hours'])} (уровень {level.name}, ×{level.multiplier})")
        elif record["kind"] == "ban":
            parts.append(format_hours(record["duration_hours"]))

        before = record.get("points_before")
        delta = record["points_delta"]
        if before is not None:
            parts.append(f"баллы {before} → {max(before + delta, 0)} ({delta:+d})")
        else:
            parts.append(f"баллы {delta:+d}")

        lines.append(f"  • {who(record['user_id'])}: {' · '.join(parts)}")
        lines.append(f"    причина: {record['reason']}")
        lines.append(f"    выдал: {who(record['moderator_id'])}, {moscow(record['created_at'])}")
        if record.get("released_at"):
            how = f"досрочно, {who(record['released_by'])}" if record.get("released_by") else "по сроку"
            lines.append(f"    освобождён: {moscow(record['released_at'])} — {how}")

    lines += ["", f"Переписка — сообщений: {len(messages)}", ""]

    for message in messages:
        lines.append(f"[{moscow(message.created_at)}] {message.author_name} (ID {message.author_id}): {message.content}")
        for attachment in message.attachments:
            lines.append(f"    [вложение: {attachment}]")
        if message.edited_at:
            lines.append(f"    (изменено {moscow(message.edited_at)})")

    return "\n".join(lines) + "\n"


async def collect_chat(channel: discord.TextChannel, max_attachment_bytes: int) -> tuple[list[ChatMessage], dict[str, bytes]]:
    """Вся переписка канала по порядку и скачанные вложения (имя в архиве → байты)."""
    messages: list[ChatMessage] = []
    files: dict[str, bytes] = {}
    index = 0

    async for message in channel.history(limit=None, oldest_first=True):
        names = []
        for attachment in message.attachments:
            index += 1
            name = f"{index:04d}_{attachment.filename}"
            if attachment.size > max_attachment_bytes:
                names.append(f"{name} (не сохранён: {attachment.size // 1024 // 1024} МБ)")
                continue
            try:
                files[name] = await attachment.read()
                names.append(name)
            except discord.HTTPException:
                names.append(f"{name} (не удалось скачать)")

        content = message.content
        if message.embeds and not content:
            content = " | ".join(filter(None, (e.title or e.description for e in message.embeds)))

        messages.append(ChatMessage(
            created_at=message.created_at.timestamp(),
            author_name=message.author.display_name,
            author_id=message.author.id,
            content=content,
            edited_at=message.edited_at.timestamp() if message.edited_at else None,
            attachments=names,
        ))

    return messages, files


def pack_attachments(files: dict[str, bytes]) -> bytes | None:
    if not files:
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()
