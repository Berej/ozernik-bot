"""
Поиск и загрузка модулей при запуске (OzernikiBot.discover_modules / load_configured_extensions).

Кейс 2026-10-24: поиск шёл по относительному пути "modules" от папки запуска. Под systemd это
была другая папка (старая копия бота) — карма, РСН и Зал славы «не находились», а тогдашняя
чистка списка вычёркивала их из config.json. Теперь:
- модули ищутся через систему импорта Python (pkgutil) — там же, откуда их грузит load_extension;
- записи о не найденных модулях не удаляются, а пропускаются с предупреждением.
"""

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conftest import bot_module

ROOT = Path(__file__).resolve().parents[1]


class FakeConfig:
    def __init__(self, modules: dict[str, bool]):
        self.MODULES = dict(modules)

    def module_add(self, module_path):
        self.MODULES.setdefault(module_path, True)


@pytest.fixture
def package(tmp_path, monkeypatch):
    """
    Фейковый пакет модулей со своим именем (Python кэширует импортированные пакеты —
    одно имя на все тесты подсунуло бы второму тесту пакет первого).
    """
    name = f"fake_modules_{uuid.uuid4().hex[:8]}"
    root = tmp_path / "bot" / name

    def file(path: str):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("")

    file("__init__.py")
    file("karma/__init__.py")
    file("karma/karma.py")
    file("karma/_helper.py")            # вспомогательный файл — не модуль
    file("bridge/__init__.py")
    file("bridge/bridge_main.py")
    file("bridge/cogs/__init__.py")     # вложенный пакет — не модуль
    file("bridge/cogs/bridge_discord.py")
    file("_hidden/__init__.py")         # папка на «_» — пропускается
    file("_hidden/thing.py")

    monkeypatch.syspath_prepend(str(tmp_path / "bot"))
    return name


def discover(monkeypatch, name: str, modules: dict[str, bool]):
    config = FakeConfig(modules)
    monkeypatch.setattr(bot_module, "config", config)
    bot = SimpleNamespace(modules_folder=name)

    bot_module.OzernikiBot.discover_modules(bot)

    return bot, config.MODULES


def test_new_modules_added(package, monkeypatch):
    bot, modules = discover(monkeypatch, package, {})

    assert modules == {f"{package}.bridge.bridge_main": True, f"{package}.karma.karma": True}
    assert bot.found_modules == set(modules)


def test_switches_kept_and_missing_not_removed(package, monkeypatch):
    """Не найденный модуль остаётся в config.json (настройки не стираются), выключенный — выключен."""
    bot, modules = discover(monkeypatch, package, {
        f"{package}.bridge.bridge": True,          # файла нет (удалённый новый мост)
        f"{package}.bridge.bridge_main": False,    # выключен админом
    })

    assert modules == {
        f"{package}.bridge.bridge": True,
        f"{package}.bridge.bridge_main": False,
        f"{package}.karma.karma": True,
    }
    assert f"{package}.bridge.bridge" not in bot.found_modules


def test_launch_folder_does_not_matter(package, tmp_path, monkeypatch):
    """Запуск из другой папки, где лежит своя неполная modules/, ни на что не влияет."""
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "modules" / "bridge").mkdir(parents=True)
    (elsewhere / "modules" / "bridge" / "bridge_main.py").write_text("")
    monkeypatch.chdir(elsewhere)

    bot, _ = discover(monkeypatch, package, {})

    assert f"{package}.karma.karma" in bot.found_modules


async def test_load_skips_missing_with_warning(monkeypatch, capsys):
    monkeypatch.setattr(bot_module, "config", FakeConfig({
        "modules.gone.gone": True,
        "modules.off.off": False,
        "modules.ok.ok": True,
    }))
    bot = SimpleNamespace(found_modules={"modules.off.off", "modules.ok.ok"}, load_extension=AsyncMock())

    await bot_module.OzernikiBot.load_configured_extensions(bot)

    bot.load_extension.assert_awaited_once_with("modules.ok.ok")
    out = capsys.readouterr().out
    assert "[gone] Модуль не найден — пропущен" in out
    assert "[off] Cog is disabled" in out


def test_real_modules_found():
    """Все папки настоящих модулей — пакеты (с __init__.py), иначе поиск их не увидит."""
    folders = [p for p in (ROOT / "modules").iterdir() if p.is_dir() and not p.name.startswith(("_", "."))]

    assert all((folder / "__init__.py").exists() for folder in folders), [f.name for f in folders]
