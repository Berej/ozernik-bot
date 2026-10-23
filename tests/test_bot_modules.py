"""
Поиск модулей при запуске (OzernikiBot.discover_modules).

config.json помнит модули между запусками. Удалённый файл модуля раньше оставался
в списке навсегда: бот при каждом запуске пытался его загрузить, а /modules_control
продолжал его показывать. Теперь такие записи убираются.
"""

from types import SimpleNamespace

import pytest

from conftest import bot_module


class FakeConfig:
    def __init__(self, modules: dict[str, bool]):
        self.MODULES = dict(modules)

    def module_add(self, module_path):
        self.MODULES.setdefault(module_path, True)

    def module_remove(self, module_path):
        self.MODULES.pop(module_path, None)


@pytest.fixture
def modules_dir(tmp_path, monkeypatch):
    """Папка бота tmp_path/bot с modules/: два модуля и вспомогательный файл."""
    root = tmp_path / "bot"
    (root / "modules" / "karma").mkdir(parents=True)
    (root / "modules" / "karma" / "karma.py").write_text("")
    (root / "modules" / "karma" / "_helper.py").write_text("")
    (root / "modules" / "bridge").mkdir()
    (root / "modules" / "bridge" / "bridge_main.py").write_text("")
    return root


def discover(monkeypatch, modules: dict[str, bool], root) -> dict[str, bool]:
    config = FakeConfig(modules)
    monkeypatch.setattr(bot_module, "config", config)
    bot = SimpleNamespace(modules_folder="modules", modules_root=str(root))

    bot_module.OzernikiBot.discover_modules(bot)

    return config.MODULES


def test_new_modules_added(modules_dir, monkeypatch):
    assert discover(monkeypatch, {}, modules_dir) == {
        "modules.bridge.bridge_main": True,
        "modules.karma.karma": True,
    }


def test_removed_module_dropped_and_switches_kept(modules_dir, monkeypatch):
    result = discover(monkeypatch, {
        "modules.bridge.bridge": True,         # файл удалён (новый мост)
        "modules.bridge.bridge_main": False,   # выключен админом — так и остаётся
        "modules.karma.karma": True,
    }, modules_dir)

    assert result == {"modules.bridge.bridge_main": False, "modules.karma.karma": True}


def test_found_from_bot_folder_not_from_launch_folder(modules_dir, tmp_path, monkeypatch):
    """
    Кейс 2026-10-24: systemd запускал бота из другой папки, где была старая modules/ без кармы.
    Поиск шёл от папки запуска — карма «пропадала» и вычёркивалась из config.json.
    """
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "modules" / "bridge").mkdir(parents=True)
    (elsewhere / "modules" / "bridge" / "bridge_main.py").write_text("")
    monkeypatch.chdir(elsewhere)

    result = discover(monkeypatch, {"modules.karma.karma": True, "modules.bridge.bridge_main": True}, modules_dir)

    assert result == {"modules.karma.karma": True, "modules.bridge.bridge_main": True}


def test_bot_folder_is_where_bot_py_is():
    import os
    assert bot_module.BASE_DIR == os.path.dirname(os.path.abspath(bot_module.__file__))
