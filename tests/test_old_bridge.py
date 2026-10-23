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


def test_only_old_bridge_in_folder():
    """
    Новый мост (bridge.py) убран по просьбе Berej 2026-10-24: выключенный, он всё равно
    висел в списке модулей. Его код — в истории git (29e7c3d:modules/bridge/bridge.py).
    """
    extensions = sorted(p.stem for p in BRIDGE_DIR.glob("*.py") if not p.name.startswith("_"))

    assert extensions == ["bridge_main"]


# ---------- ДУБЛИ И ОСТАНОВКА (кейс 2026-10-24) -----------
# Симптом: сообщение из Telegram приходило в Discord дважды, лог забит ID чата.
# Причина: при выгрузке модуля Telegram-боты не останавливались, повторная загрузка
# запускала вторую пару с теми же токенами — getUpdates отдавал одно сообщение обоим.

import asyncio
from types import SimpleNamespace

from modules.bridge.cogs import bridge_telegram, bridge_discord

CHAT = -100222


@pytest.fixture
def tg(old_config, monkeypatch):
    """Настройки моста, пустая память пересланного и перехват очереди в Discord."""
    old_config(**FULL)
    monkeypatch.setattr(bridge_telegram, "_seen", set())
    monkeypatch.setattr(bridge_telegram, "_seen_order", __import__("collections").deque())
    monkeypatch.setattr(bridge_telegram, "BRIDGE_BOT_IDS", {7001, 7002})
    sent, requests = [], []
    monkeypatch.setattr(bridge_telegram, "storage", SimpleNamespace(
        add_ttd=sent.append, add_req=requests.append, load_link=lambda: [[555, 10, "hat", True]],
    ))
    context = SimpleNamespace(bot=SimpleNamespace(
        id=7002, get_user_profile_photos=AsyncMock(return_value=SimpleNamespace(total_count=0)),
    ))
    return SimpleNamespace(sent=sent, requests=requests, context=context)


def tg_update(message_id=10, user_id=42, chat_id=CHAT, text="привет"):
    user = SimpleNamespace(id=user_id, full_name="Человек", username="human", is_bot=False)
    message = SimpleNamespace(
        message_id=message_id, from_user=user, text=text, caption=None, reply_to_message=None,
        photo=None, video=None, animation=None, sticker=None, document=None, voice=None, video_note=None,
    )
    return SimpleNamespace(effective_message=message, message=message, effective_chat=SimpleNamespace(id=chat_id))


async def test_message_forwarded_once(tg, capsys):
    update = tg_update()

    await bridge_telegram.on_message(update, tg.context)
    await bridge_telegram.on_message(update, tg.context)  # то же сообщение второй раз

    assert [m["mess_text"] for m in tg.sent] == ["привет"]
    assert capsys.readouterr().out == ""  # без ID чата в логе на каждое сообщение


@pytest.mark.parametrize(("case", "update"), [
    ("сообщение бота моста (пересланное из Discord)", tg_update(user_id=7001)),
    ("другой чат", tg_update(chat_id=-100999)),
])
async def test_message_not_forwarded(tg, case, update):
    await bridge_telegram.on_message(update, tg.context)

    assert tg.sent == [], case


async def test_other_bots_forwarded(tg):
    """Не все боты — свои: анонимных админов Telegram подписывает ботом GroupAnonymousBot."""
    update = tg_update(user_id=1087968824)
    update.effective_message.from_user.is_bot = True

    await bridge_telegram.on_message(update, tg.context)

    assert len(tg.sent) == 1


async def test_edit_of_unbridged_message_skipped(tg):
    """Правка сообщения, которого нет в Discord, — без запроса (раньше — ошибка в лог)."""
    edited = SimpleNamespace(message_id=99, text="новое", caption=None, from_user=SimpleNamespace(id=42))
    update = SimpleNamespace(edited_message=edited, effective_chat=SimpleNamespace(id=CHAT))

    await bridge_telegram.on_message_edit(update, tg.context)

    assert tg.requests == []


def test_bot_id_from_token():
    assert bridge_telegram.bot_id_from_token("123456:ABC-def") == 123456
    assert bridge_telegram.bot_id_from_token("") is None


class FakeApp:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on
        self.bot = SimpleNamespace(id=1)
        self.updater = SimpleNamespace(start_polling=self._step("start_polling"), stop=self._step("updater.stop"))
        self.initialize, self.start = self._step("initialize"), self._step("start")
        self.stop, self.shutdown = self._step("stop"), self._step("shutdown")

    def _step(self, name):
        async def step(*args, **kwargs):
            self.calls.append(name)
            if name == self.fail_on:
                raise RuntimeError(name)
        return step

    def add_handler(self, handler):
        pass


def fake_builder(apps):
    queue = iter(apps)
    builder = SimpleNamespace()
    for method in ("token", "connect_timeout", "read_timeout"):
        setattr(builder, method, lambda *a, **k: builder)
    builder.build = lambda: next(queue)
    return lambda: builder


async def test_start_and_stop(tg, monkeypatch):
    apps = [FakeApp(), FakeApp()]
    monkeypatch.setattr(bridge_telegram, "ApplicationBuilder", fake_builder(apps))
    monkeypatch.setattr(bridge_telegram, "send_tel_message", AsyncMock())
    monkeypatch.setattr(bridge_telegram, "do_request", AsyncMock())
    monkeypatch.setattr(bridge_telegram.config.__class__, "TELEGRAM_TOKEN_1", property(lambda self: "7001:a"))
    monkeypatch.setattr(bridge_telegram.config.__class__, "TELEGRAM_TOKEN_2", property(lambda self: "7002:b"))

    bridge = await bridge_telegram.start_telegram_bot()

    assert bridge_telegram.BRIDGE_BOT_IDS == {7001, 7002}
    assert len(bridge.tasks) == 2
    await bridge.stop()
    for app in apps:
        assert app.calls[-3:] == ["updater.stop", "stop", "shutdown"]
    # cancel() лишь просит задачу остановиться — дождаться, пока она это сделает.
    await asyncio.gather(*bridge.tasks, return_exceptions=True)
    assert all(task.done() for task in bridge.tasks)


async def test_failed_start_leaves_nothing_running(tg, monkeypatch, capsys):
    apps = [FakeApp(), FakeApp(fail_on="start_polling")]
    monkeypatch.setattr(bridge_telegram, "ApplicationBuilder", fake_builder(apps))

    assert await bridge_telegram.start_telegram_bot() is None

    assert apps[0].calls[-3:] == ["updater.stop", "stop", "shutdown"]  # первый бот не остался работать
    assert "Ошибка запуска Telegram-ботов моста" in capsys.readouterr().out


async def test_unload_stops_everything(old_config, monkeypatch):
    old_config(**FULL)
    telegram = SimpleNamespace(stop=AsyncMock())
    monkeypatch.setattr(bridge_main, "start_telegram_bot", AsyncMock(return_value=telegram))
    bot = SimpleNamespace(
        loop=asyncio.get_running_loop(),
        extensions={},
        load_extension=AsyncMock(),
        unload_extension=AsyncMock(),
    )
    cog = bridge_main.BridgeMain(bot)
    await asyncio.sleep(0)
    bot.extensions[bridge_main.DISCORD_PART] = object()

    await cog.cog_unload()

    telegram.stop.assert_awaited_once()
    bot.unload_extension.assert_awaited_once_with(bridge_main.DISCORD_PART)


async def test_discord_part_loops_stop_on_unload(monkeypatch):
    monkeypatch.setattr(bridge_discord, "storage", SimpleNamespace(pop_ttd=lambda: None, pop_req=lambda way: None))
    cog = bridge_discord.BridgeDiscord(SimpleNamespace())
    assert cog.send_dis_message.is_running() and cog.do_request.is_running()

    await cog.cog_unload()
    for _ in range(3):
        await asyncio.sleep(0)

    assert not cog.send_dis_message.is_running()
    assert not cog.do_request.is_running()
