"""
Администрация бота — общий список для всех модулей (решение Alium 2026-11-15): config.json
ADMIN_ROLES_IDS и MODERATOR_ROLES_IDS, задаются /staff_roles. Модераторам (без галочки
«Администратор») — РСН (муты и дела), твинки и /settings_karma.
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import utilities
import modules.rsn.rsn as rsn
from modules.panel import panel
from conftest import FakeRole, make_interaction

ADMIN_ROLE = 7300
MOD_ROLE = 7400


@pytest.fixture
def staff(env, monkeypatch):
    bot_config = sys.modules["config"].config
    monkeypatch.setattr(bot_config, "ADMIN_ROLES_IDS", [ADMIN_ROLE])
    monkeypatch.setattr(bot_config, "MODERATOR_ROLES_IDS", [MOD_ROLE])
    admin_role = FakeRole(ADMIN_ROLE, "Администратор", guild=env.guild)
    mod_role = FakeRole(MOD_ROLE, "Модератор", guild=env.guild)
    env.guild.roles.update({ADMIN_ROLE: admin_role, MOD_ROLE: mod_role})
    admin = env.guild.add_member(10, roles=[admin_role])
    moderator = env.guild.add_member(20, roles=[mod_role])
    flag_admin = env.guild.add_member(30)
    flag_admin.guild_permissions.administrator = True
    user = env.guild.add_member(40)
    return SimpleNamespace(env=env, config=bot_config, admin=admin, moderator=moderator,
                           flag_admin=flag_admin, user=user, admin_role=admin_role, mod_role=mod_role)


class TestLevels:
    def test_levels(self, staff):
        assert [utilities.is_admin(m) for m in (staff.admin, staff.flag_admin, staff.moderator, staff.user)] == \
            [True, True, False, False]
        assert [utilities.is_moderator(m) for m in (staff.admin, staff.flag_admin, staff.moderator, staff.user)] == \
            [True, True, True, False]
        assert utilities.staff_role_ids() == [ADMIN_ROLE, MOD_ROLE]

    async def test_require_staff_texts(self, staff):
        interaction = make_interaction(staff.moderator, staff.env.guild)

        assert await utilities.require_staff(interaction, "moderator") is True
        assert await utilities.require_staff(interaction, "admin") is False
        interaction.response.send_message.assert_awaited_once_with("Только для администрации.", ephemeral=True)


class TestKarma:
    async def test_moderator_opens_settings(self, staff):
        interaction = make_interaction(staff.moderator, staff.env.guild)

        await staff.env.cog.settings_karma.callback(staff.env.cog, interaction)

        assert "view" in interaction.response.send_message.call_args.kwargs

    async def test_moderator_links_twink(self, staff):
        env = staff.env
        main = env.guild.add_member(1)
        main.send = AsyncMock()
        twink = env.guild.add_member(11)
        twink.send = AsyncMock()
        env.bot.get_user = env.guild.get_member
        interaction = make_interaction(staff.moderator, env.guild)

        await env.cog.twink_link.callback(env.cog, interaction, main, twink)

        assert env.db.is_twink(11)

    async def test_moderator_cannot_add_karma(self, staff):
        interaction = make_interaction(staff.moderator, staff.env.guild)

        await staff.env.cog.karma_add.callback(staff.env.cog, interaction, staff.user, 10)

        interaction.response.send_message.assert_awaited_once_with("Только для Администраторов.", ephemeral=True)

    async def test_admin_role_without_flag_adds_karma(self, staff):
        """Роль из ADMIN_ROLES_IDS — администрация и без галочки «Администратор»."""
        staff.env.bot.get_user = staff.env.guild.get_member
        staff.user.send = AsyncMock()
        interaction = make_interaction(staff.admin, staff.env.guild)

        await staff.env.cog.karma_add.callback(staff.env.cog, interaction, staff.user, 10)

        assert staff.env.db.get_karma(staff.env.db.get_user_by_discord_id(40).id).karma == 10


class TestSeedFromRsn:
    def test_seeded_once(self):
        bot_config = SimpleNamespace(ADMIN_ROLES_IDS=[], MODERATOR_ROLES_IDS=[])
        rsn_data = {"admin_role_ids": [725], "moderator_role_ids": [779]}

        rsn.seed_staff_roles(rsn_data, bot_config)

        assert (bot_config.ADMIN_ROLES_IDS, bot_config.MODERATOR_ROLES_IDS) == ([725], [779])
        assert bot_config.STAFF_ROLES_SEEDED is True

    def test_cleared_list_not_reseeded(self):
        """Администрация очистила модерацию через /staff_roles — перезапуск не должен вернуть старые роли."""
        bot_config = SimpleNamespace(ADMIN_ROLES_IDS=[725], MODERATOR_ROLES_IDS=[], STAFF_ROLES_SEEDED=True)

        rsn.seed_staff_roles({"admin_role_ids": [725], "moderator_role_ids": [779]}, bot_config)

        assert bot_config.MODERATOR_ROLES_IDS == []

    def test_existing_config_kept(self):
        bot_config = SimpleNamespace(ADMIN_ROLES_IDS=[1], MODERATOR_ROLES_IDS=[2])

        rsn.seed_staff_roles({"admin_role_ids": [725], "moderator_role_ids": [779]}, bot_config)

        assert (bot_config.ADMIN_ROLES_IDS, bot_config.MODERATOR_ROLES_IDS) == ([1], [2])


class TestRsn:
    async def test_moderator_passes_rsn_check_without_role_in_rsn_data(self, staff, monkeypatch):
        monkeypatch.setitem(rsn.RSN_DATA, "moderator_role_ids", [])
        cog = rsn.RsnCog.__new__(rsn.RsnCog)

        assert await cog._check_permissions(make_interaction(staff.moderator, staff.env.guild)) is True
        refused = make_interaction(staff.user, staff.env.guild)
        assert await cog._check_permissions(refused) is False


class TestStaffRolesCommand:
    async def test_only_admin(self, staff):
        cog = panel.Panel(staff.env.bot)
        interaction = make_interaction(staff.moderator, staff.env.guild)

        await cog.staff_roles.callback(cog, interaction)

        interaction.response.send_message.assert_awaited_once_with("Только для администрации.", ephemeral=True)

    async def test_untouched_list_kept_on_save(self, staff):
        """
        Select.values — только выбранное в этом меню; предвыбранное туда не попадает. Поменяли одну
        модерацию — администрация не должна стереться.
        """
        cog = panel.Panel(staff.env.bot)
        opened = make_interaction(staff.admin, staff.env.guild)
        await cog.staff_roles.callback(cog, opened)
        view = opened.response.send_message.call_args.kwargs["view"]
        admin_select, mod_select = [item for item in view.children if isinstance(item, discord.ui.RoleSelect)]
        assert [r.id for r in admin_select.default_values] == [ADMIN_ROLE]

        new_mod = FakeRole(7500, "Хелпер", guild=staff.env.guild)
        staff.env.guild.roles[new_mod.id] = new_mod
        mod_select._values = [new_mod]  # как будто выбрали в меню
        await mod_select.callback(SimpleNamespace(response=SimpleNamespace(defer=AsyncMock())))
        press = SimpleNamespace(user=staff.admin, guild=staff.env.guild,
                                response=SimpleNamespace(edit_message=AsyncMock()))
        await view.save.callback(press)

        assert staff.config.ADMIN_ROLES_IDS == [ADMIN_ROLE]
        assert staff.config.MODERATOR_ROLES_IDS == [7500]
        assert "Сохранено" in press.response.edit_message.call_args.kwargs["content"]


class TestLegacyPanelRoles:
    def test_staff_roles_shows_panel_roles_to_identify(self, staff):
        """На выкатке /staff_roles показывает старый список панели: название (упоминание) и число участников."""
        known = FakeRole(panel.ALLOWED_ROLES[1], "Кто-то", guild=staff.env.guild)
        known.members = [staff.user, staff.moderator]
        staff.env.guild.roles[known.id] = known

        text = panel.staff_roles_text(staff.env.guild)

        assert f"<@&{known.id}> (`{known.id}`) — участников: 2" in text
        assert f"`{panel.ALLOWED_ROLES[4]}` — такой роли на этом сервере нет" in text


# ---------- Сторож: права — только через общие проверки (решение Alium 2026-11-15) ----------

import re
from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parents[1] / "modules"

# Известные исключения — с причиной. Новые сюда не добавлять: в новом коде — utilities.require_staff,
# is_admin, is_moderator, staff_role_ids.
LEGACY_PERMISSION_CHECKS = {
    "cold_old_man.py",   # ивент «Пьяный алхимик», отключён; при оживлении — перевести на require_staff
}
LEGACY_ROLE_LISTS = {
    ("panel.py", "ALLOWED_ROLES"),   # TODO следующего патча: свести с общим списком (см. /staff_roles)
}


def test_no_direct_administrator_checks():
    """
    Кто «администрация» — решает utilities.is_admin (право «Администратор» ИЛИ роль из ADMIN_ROLES_IDS).
    Прямая проверка guild_permissions.administrator не пускает тех, у кого админская роль без галочки,
    и модераторов — ровно та стена, о которую они бились до 2026-11-15. Права самого бота
    (guild.me…) проверять напрямую можно.
    """
    offenders = []
    for path in MODULES_DIR.rglob("*.py"):
        if path.name in LEGACY_PERMISSION_CHECKS:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in re.finditer(r"([\w.]+)\.guild_permissions\.administrator", line):
                owner = match.group(1)
                if owner != "me" and not owner.endswith(".me"):
                    offenders.append(f"{path.relative_to(MODULES_DIR)}:{number}")
    assert offenders == [], "Права — через utilities.require_staff / is_admin / is_moderator: " + ", ".join(offenders)


def test_no_hardcoded_staff_role_lists():
    """Роли администрации и модерации — один список в config.json (/staff_roles), а не свой в каждом модуле."""
    pattern = re.compile(r"^\s*(\w*(?:ADMIN|MOD|STAFF|ALLOWED)\w*_ROLES?(?:_IDS?)?)\s*=", re.MULTILINE)
    offenders = [
        f"{path.relative_to(MODULES_DIR)}: {match.group(1)}"
        for path in MODULES_DIR.rglob("*.py")
        for match in pattern.finditer(path.read_text(encoding="utf-8"))
        if (path.name, match.group(1)) not in LEGACY_ROLE_LISTS
    ]
    assert offenders == [], "Списки ролей — только config.ADMIN_ROLES_IDS / MODERATOR_ROLES_IDS: " + ", ".join(offenders)
