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
    """Папка modules/ с двумя модулями и вспомогательным файлом; работаем из tmp_path."""
    (tmp_path / "modules" / "karma").mkdir(parents=True)
    (tmp_path / "modules" / "karma" / "karma.py").write_text("")
    (tmp_path / "modules" / "karma" / "_helper.py").write_text("")
    (tmp_path / "modules" / "bridge").mkdir()
    (tmp_path / "modules" / "bridge" / "bridge_main.py").write_text("")
    monkeypatch.chdir(tmp_path)


def discover(monkeypatch, modules: dict[str, bool]) -> dict[str, bool]:
    config = FakeConfig(modules)
    monkeypatch.setattr(bot_module, "config", config)
    bot = SimpleNamespace(modules_folder="modules")

    bot_module.OzernikiBot.discover_modules(bot)

    return config.MODULES


def test_new_modules_added(modules_dir, monkeypatch):
    assert discover(monkeypatch, {}) == {
        "modules.bridge.bridge_main": True,
        "modules.karma.karma": True,
    }


def test_removed_module_dropped_and_switches_kept(modules_dir, monkeypatch):
    result = discover(monkeypatch, {
        "modules.bridge.bridge": True,         # файл удалён (новый мост)
        "modules.bridge.bridge_main": False,   # выключен админом — так и остаётся
        "modules.karma.karma": True,
    })

    assert result == {"modules.bridge.bridge_main": False, "modules.karma.karma": True}
