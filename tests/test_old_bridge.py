"""
Старый мост Discord ⇄ Telegram (bridge_main.py + cogs/) — включён временно, пока пишется новый.

Проверяется только то, что ломалось при выключении 2026-09-01: настройки (config_loader удалён),
запуск, context._bot вместо context.bot, и то, что новый мост выключен (иначе боты конфликтуют).
Сама пересылка сообщений тестами не покрыта — её проверять на тестовом сервере.
"""

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("telegram")  # python-telegram-bot

import config as config_stub  # заглушка из conftest
from modules.bridge import bridge_main
from modules.bridge.cogs import _old_config

BRIDGE_DIR = Path(__file__).resolve().parents[1] / "modules" / "bridge"

FULL = {
    "GUILD_ID": 1000,
    "BR_DISCORD_CHANNEL_ID": 111,
    "BR_TELEGRAM_CHANNEL_ID": -100222,
    "TELEGRAM_TOKEN_1": "token-1",
    "TELEGRAM_TOKEN_2": "token-2",
}


@pytest.fixture
def old_config(monkeypatch):
    for key in FULL:
        monkeypatch.delattr(config_stub.config, key, raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN_1", raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN_2", raising=False)

    def fill(**values):
        for key, value in values.items():
            monkeypatch.setattr(config_stub.config, key, value, raising=False)

    return fill


def test_reads_current_config_under_old_names(old_config):
    old_config(**FULL)
    cfg = _old_config.config

    assert (cfg.GUILD, cfg.BR_DISCORD_CHANNEL_ID, cfg.BR_TELEGRAM_CHANNEL_ID) == (1000, 111, -100222)
    assert (cfg.TELEGRAM_TOKEN_1, cfg.TELEGRAM_TOKEN_2) == ("token-1", "token-2")
    assert cfg.missing() == []


def test_tokens_from_env_take_priority(old_config, monkeypatch):
    old_config(**FULL)
    monkeypatch.setenv("TELEGRAM_TOKEN_1", "from-env")

    assert _old_config.config.TELEGRAM_TOKEN_1 == "from-env"


def test_missing_settings_listed(old_config):
    old_config(GUILD_ID=1000)

    assert _old_config.config.missing() == [
        "BR_DISCORD_CHANNEL_ID", "BR_TELEGRAM_CHANNEL_ID", "TELEGRAM_TOKEN_1", "TELEGRAM_TOKEN_2",
    ]


async def test_setup_skips_without_settings(old_config, capsys):
    old_config(GUILD_ID=1000)
    bot = AsyncMock()

    await bridge_main.setup(bot)

    bot.add_cog.assert_not_awaited()
    assert "[Старый мост] Не запущен: не заданы BR_DISCORD_CHANNEL_ID" in capsys.readouterr().out


async def test_setup_starts_with_settings(old_config, monkeypatch):
    old_config(**FULL)
    started = []
    monkeypatch.setattr(bridge_main, "BridgeMain", lambda bot: started.append(bot) or "cog")
    bot = AsyncMock()

    await bridge_main.setup(bot)

    bot.add_cog.assert_awaited_once_with("cog")


def test_no_private_context_bot():
    """У CallbackContext нет атрибута _bot — только свойство bot; с _bot падала пересылка медиа."""
    source = (BRIDGE_DIR / "cogs" / "bridge_telegram.py").read_text(encoding="utf-8")

    assert "context._bot" not in source


def test_discord_part_registers():
    tree = ast.parse((BRIDGE_DIR / "cogs" / "bridge_discord.py").read_text(encoding="utf-8"))
    setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "setup")

    assert "add_cog" in ast.unparse(setup)


def test_new_bridge_disabled():
    """Новый мост не регистрируется: с теми же Telegram-ботами он конфликтовал бы со старым."""
    tree = ast.parse((BRIDGE_DIR / "bridge.py").read_text(encoding="utf-8"))
    setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "setup")

    assert "add_cog" not in ast.unparse(setup)  # ast.unparse отбрасывает комментарии
