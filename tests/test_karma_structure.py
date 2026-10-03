"""
Устройство модуля кармы по файлам.

bot.py грузит как расширение каждый .py в папке модуля, кроме начинающихся с «_»,
и требует у расширения функцию setup. Поэтому вспомогательные файлы кармы обязаны
начинаться с «_», а расширение в папке — ровно одно.
"""

import os
from pathlib import Path

import pytest

KARMA_DIR = Path(__file__).resolve().parents[1] / "modules" / "karma_sistem"


def discovered_extensions(folder: Path) -> list[str]:
    """Та же логика, что в OzernikiBot.discover_modules."""
    return sorted(
        name[:-3]
        for name in os.listdir(folder)
        if name.endswith(".py") and not name.startswith("_")
    )


def test_only_one_extension_in_karma_folder():
    assert discovered_extensions(KARMA_DIR) == ["karma_sistem"]


def test_extension_has_setup(env):
    assert callable(env.karma.setup)


@pytest.mark.parametrize("helper", ["_db", "_state", "_formulas", "_cards", "_roles", "_settings", "_boards"])
def test_helper_files_exist(helper):
    assert (KARMA_DIR / f"{helper}.py").exists()


def test_main_file_reexports_parts(env):
    """karma_sistem отдаёт части модуля под своим именем — на этом держатся тесты и старые импорты."""
    for name in (
        "get_level", "get_cube_status", "create_rank_card", "Roles",
        "SettingsGeneralPage", "BindLeaderboardView", "KarmaDatabase", "import_legacy_level_texts",
    ):
        assert hasattr(env.karma, name), name
