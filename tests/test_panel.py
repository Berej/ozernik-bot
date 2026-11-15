"""/modules_control (modules/panel/panel.py)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conftest import GUILD_ID, FakeMember, FakeRole
from modules.panel import panel


async def test_failed_load_reports_error():
    """
    Ошибка загрузки модуля должна дойти до админа сообщением. Раньше обработчик ошибок
    сам падал (inspect не был импортирован) — команда висела в «думаю…» без ответа.
    """
    bot = SimpleNamespace(load_extension=AsyncMock(side_effect=RuntimeError("модуль сломан")))
    cog = panel.Panel(bot)
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog.modules_control.callback(cog, interaction, "modules.x.y", "load")

    text = interaction.followup.send.call_args.args[0]
    assert "Произошла ошибка" in text and "модуль сломан" in text


# ---------- .bot_shutdown / .bot_restart (решение Alium 2026-11-15) ----------


def power_message(content, role_ids):
    author = FakeMember(1)
    author.roles = [FakeRole(role_id) for role_id in role_ids]
    return SimpleNamespace(author=author, content=content, guild=SimpleNamespace(id=GUILD_ID),
                           channel=SimpleNamespace(send=AsyncMock()))


@pytest.mark.parametrize(("content", "code"), [(".bot_shutdown", 64), (".BOT_RESTART", 0)])
async def test_shutdown_and_restart_exit_codes(content, code):
    """
    Под systemd (Restart=always) любой выход — перезапуск. Выключение выходит с кодом 64 —
    в юните RestartPreventExitStatus=64, и systemd его не поднимает; перезапуск — с кодом 0.
    """
    bot = SimpleNamespace(close=AsyncMock())
    cog = panel.Panel(bot)
    message = power_message(content, [panel.ALLOWED_ROLES[0]])

    await cog.on_message(message)

    bot.close.assert_awaited_once()
    assert bot.exit_code == code == (panel.SHUTDOWN_EXIT_CODE if "shutdown" in content else 0)


async def test_power_commands_need_role():
    bot = SimpleNamespace(close=AsyncMock())
    cog = panel.Panel(bot)
    message = power_message(".bot_shutdown", [42])

    await cog.on_message(message)

    bot.close.assert_not_awaited()
    assert "Нет прав" in message.channel.send.call_args.args[0]
