"""/modules_control (modules/panel/panel.py)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
