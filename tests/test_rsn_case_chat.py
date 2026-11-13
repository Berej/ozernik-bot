"""РСН 2.0: чат дела — лог переписки (текст, вложения), создание и доступ."""

import io
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from modules.rsn import _rsn_case_chat as chat

T0 = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc).timestamp()  # 12:00 МСК


def test_transcript_header_and_messages():
    case = {"id": 123, "created_at": T0}
    records = [
        {"user_id": 10, "kind": "mute", "duration_hours": 2.0, "points_before": 25, "points_delta": -10,
         "reason": "флуд", "moderator_id": 900, "created_at": T0, "released_at": T0 + 7200, "released_by": None},
        {"user_id": 11, "kind": "mute", "duration_hours": 3.0, "points_before": 8, "points_delta": -5,
         "reason": "оскорбление", "moderator_id": 900, "created_at": T0, "released_at": T0 + 600, "released_by": 901},
    ]
    names = {10: "Нарушитель", 11: "Второй", 900: "Модер", 901: "Админ"}
    messages = [
        chat.ChatMessage(T0 + 60, "Нарушитель", 10, "я не виноват", attachments=["0001_скрин.png"]),
        chat.ChatMessage(T0 + 120, "Модер", 900, "разберёмся", edited_at=T0 + 180),
    ]

    text = chat.format_transcript(case, records, names, messages, closed_at=T0 + 7200)

    assert text.startswith("РСН · Дело #123\nОткрыто: 07.11.2026 12:00 МСК · закрыто: 07.11.2026 14:00 МСК")
    assert "  • Нарушитель (ID 10): мут · 2ч (уровень 11–30, ×2) · баллы 25 → 15 (-10)" in text
    assert "    причина: флуд" in text
    assert "    освобождён: 07.11.2026 14:00 — по сроку" in text
    assert "  • Второй (ID 11): мут · 3ч (уровень 1–10, ×3) · баллы 8 → 3 (-5)" in text
    assert "    освобождён: 07.11.2026 12:10 — досрочно, Админ (ID 901)" in text
    assert "Переписка — сообщений: 2" in text
    assert "[07.11.2026 12:01] Нарушитель (ID 10): я не виноват\n    [вложение: 0001_скрин.png]" in text
    assert "[07.11.2026 12:02] Модер (ID 900): разберёмся\n    (изменено 07.11.2026 12:03)" in text


def test_transcript_warn_and_points_floor():
    case = {"id": 5, "created_at": T0}
    records = [{"user_id": 10, "kind": "warn", "duration_hours": None, "points_before": 3, "points_delta": -5,
                "reason": "спам", "moderator_id": 900, "created_at": T0}]

    text = chat.format_transcript(case, records, {}, [], closed_at=T0)

    assert "  • ID 10 (ID 10): без мута · баллы 3 → 0 (-5)" in text  # ниже нуля не опускается


@pytest.mark.parametrize(("hours", "text"), [(None, "бессрочно"), (0.5, "30м"), (26.5, "1д 2ч 30м"), (24, "1д")])
def test_format_hours(hours, text):
    assert chat.format_hours(hours) == text


class FakeAttachment:
    def __init__(self, filename, data=b"img", size=3, fail=False):
        self.filename, self.size, self._data, self._fail = filename, size, data, fail

    async def read(self):
        if self._fail:
            raise discord.HTTPException(SimpleNamespace(status=404, reason="gone"), "gone")
        return self._data


def fake_message(content, attachments=(), author=("Имя", 10), embeds=()):
    return SimpleNamespace(
        content=content, attachments=list(attachments), embeds=list(embeds),
        author=SimpleNamespace(display_name=author[0], id=author[1]),
        created_at=datetime.fromtimestamp(T0, timezone.utc), edited_at=None,
    )


class FakeChannel:
    def __init__(self, messages):
        self._messages = messages

    async def _iter(self):
        for message in self._messages:
            yield message

    def history(self, limit=None, oldest_first=False):
        assert limit is None and oldest_first  # вся переписка, по порядку
        return self._iter()


async def test_collect_chat_downloads_attachments():
    channel = FakeChannel([
        fake_message("скрин", [FakeAttachment("a.png", b"AAA")]),
        fake_message("", [FakeAttachment("big.mp4", size=50 * 1024 * 1024)]),
        fake_message("", [FakeAttachment("gone.png", fail=True)]),
        fake_message("", embeds=[SimpleNamespace(title="Дело #1 открыто", description=None)]),
    ])

    messages, files = await chat.collect_chat(channel, max_attachment_bytes=8 * 1024 * 1024)

    assert files == {"0001_a.png": b"AAA"}
    assert messages[0].attachments == ["0001_a.png"]
    assert messages[1].attachments == ["0002_big.mp4 (не сохранён: 50 МБ)"]
    assert messages[2].attachments == ["0003_gone.png (не удалось скачать)"]
    assert messages[3].content == "Дело #1 открыто"  # сообщения-плашки бота не теряются


def test_pack_attachments():
    assert chat.pack_attachments({}) is None

    data = chat.pack_attachments({"0001_a.png": b"AAA"})

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.read("0001_a.png") == b"AAA"


async def test_open_case_chat_outside_categories():
    guild = SimpleNamespace(default_role="everyone", me="bot", create_text_channel=AsyncMock(return_value="channel"))

    channel = await chat.open_case_chat(guild, 7, ["admin"], ["member"])

    kwargs = guild.create_text_channel.call_args.kwargs
    assert channel == "channel"
    assert kwargs["name"] == "дело-7"
    assert "category" not in kwargs  # вне категорий
    assert kwargs["overwrites"]["everyone"].view_channel is False
    assert kwargs["overwrites"]["member"].send_messages is True
