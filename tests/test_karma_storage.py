"""
Хранение данных кармы: тексты повышений в базе, перенос из старого levels.json,
надёжная запись JSON-файлов (NewDataWorker, безопасная запись через временный файл).

Кейс 2026-09-29: тексты первых 10 уровней пропали после обновления бота —
levels.json писался фоновой задачей, которая молча умирала от первой ошибки записи.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import utilities
from conftest import make_interaction


# ---------- ТЕКСТЫ В БАЗЕ -----------

class TestLevelTextsInDatabase:
    def test_empty_by_default(self, env):
        assert env.db.get_level_texts() == {}
        assert env.db.get_level_text(5) == ""

    def test_set_and_update(self, env):
        env.db.set_level_texts({1: "первый", 10: "десятый"})
        env.db.set_level_texts({10: "новый десятый"})

        assert env.db.get_level_texts() == {1: "первый", 10: "новый десятый"}
        assert env.db.get_level_text(10) == "новый десятый"

    @pytest.mark.parametrize("texts", [{0: "x"}, {101: "x"}, {"5": "x"}, {5: 7}])
    def test_validation(self, env, texts):
        with pytest.raises(ValueError):
            env.db.set_level_texts(texts)

    def test_invalid_batch_writes_nothing(self, env):
        with pytest.raises(ValueError):
            env.db.set_level_texts({1: "ок", 200: "плохо"})

        assert env.db.get_level_texts() == {}

    def test_level_up_uses_database_text(self, env):
        karma_roles = env.data.karma_roles
        karma_roles["preta"]["role_id"] = 777
        env.data.karma_roles = karma_roles
        env.db.set_level_texts({3: "Третий! {role}"})

        assert env.karma.get_level_up_text(3) == "Третий! <@&777>"


# ---------- ПЕРЕНОС ИЗ levels.json -----------

class TestLegacyImport:
    def write_legacy(self, path, texts):
        data = {str(level): "" for level in range(1, 101)}
        data.update(texts)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_imports_texts_and_renames_file(self, env, tmp_path):
        path = tmp_path / "levels.json"
        self.write_legacy(path, {"1": "Первый", "10": "Десятый {role}"})

        imported = env.karma.import_legacy_level_texts(path)

        assert imported == 2
        assert env.db.get_level_texts() == {1: "Первый", 10: "Десятый {role}"}
        assert not path.exists()
        assert (tmp_path / "levels.json.imported").exists()  # резервная копия

    def test_does_not_overwrite_existing_texts(self, env, tmp_path):
        env.db.set_level_texts({1: "Уже отредактировано в базе"})
        path = tmp_path / "levels.json"
        self.write_legacy(path, {"1": "Старый текст из файла"})

        assert env.karma.import_legacy_level_texts(path) == 0
        assert env.db.get_level_text(1) == "Уже отредактировано в базе"

    def test_skips_junk_keys(self, env, tmp_path):
        path = tmp_path / "levels.json"
        path.write_text(json.dumps({"5": "ок", "abc": "мусор", "500": "мимо", "6": 42}), encoding="utf-8")

        env.karma.import_legacy_level_texts(path)

        assert env.db.get_level_texts() == {5: "ок"}

    def test_missing_file(self, env, tmp_path):
        assert env.karma.import_legacy_level_texts(tmp_path / "levels.json") == 0

    def test_broken_file_left_alone(self, env, tmp_path):
        path = tmp_path / "levels.json"
        path.write_text("{ сломано", encoding="utf-8")

        assert env.karma.import_legacy_level_texts(path) == 0
        assert path.exists()  # не переименован — можно разобраться руками

    def test_runs_on_cog_load(self, env, monkeypatch, tmp_path):
        from discord.ext import tasks
        from unittest.mock import MagicMock

        monkeypatch.setattr(tasks.Loop, "start", lambda self, *args, **kwargs: None)
        env.bot.add_view = MagicMock()
        self.write_legacy(tmp_path / "levels.json", {"2": "Второй"})

        env.cog.cog_load()

        assert env.db.get_level_text(2) == "Второй"


# ---------- ОКНО «ТЕКСТ ПОВЫШЕНИЙ» -----------

class TestLevelUpModal:
    def open_modal(self, env, value):
        author = env.guild.add_member(1)
        navigator = env.karma.Navigator(env.karma.SettingsLevelUpPage, author=author, bot=env.bot)
        navigator.message = SimpleNamespace(edit=AsyncMock())
        page = navigator.current_page
        modal = page.LevelUpTextModal((("1", ""),), page)
        modal.text_input._value = value
        return modal, make_interaction(author, env.guild)

    async def test_saves_to_database(self, env):
        modal, interaction = self.open_modal(env, "'1': 'Первый', '7': 'Седьмой'")

        await modal.on_submit(interaction)

        assert env.db.get_level_texts() == {1: "Первый", 7: "Седьмой"}
        interaction.followup.send.assert_awaited_once_with("Изменено.", ephemeral=True)

    @pytest.mark.parametrize(
        ("value", "reason"),
        [
            ("'0': 'нулевой'", "нужен номер от 1 до 100"),
            ("'abc': 'x'", "нужен номер от 1 до 100"),
            ("'5': 42", "текст должен быть в кавычках"),
        ],
    )
    async def test_rejects_bad_levels(self, env, value, reason):
        modal, interaction = self.open_modal(env, value)

        await modal.on_submit(interaction)

        message = interaction.followup.send.call_args.args[0]
        assert message.startswith("Не сохранено.")
        assert reason in message
        assert env.db.get_level_texts() == {}

    async def test_page_shows_texts_from_database(self, env):
        env.db.set_level_texts({2: "два"})
        author = env.guild.add_member(1)
        navigator = env.karma.Navigator(env.karma.SettingsLevelUpPage, author=author, bot=env.bot)
        interaction = make_interaction(author, env.guild)

        await navigator.current_page.buttons[0].callback(interaction)

        modal = interaction.response.send_modal.call_args.args[0]
        assert modal.text_input.default.startswith("'1': '',\n'2': 'два',\n'3': ''")


# ---------- ЗАПИСЬ JSON-ФАЙЛОВ -----------

class TestSafeJsonWrite:
    def test_write_is_atomic(self, tmp_path, monkeypatch):
        """Если запись упала посередине — на диске остаётся старый целый файл."""
        worker = utilities.DataWorker(tmp_path / "data.json", setup={"channel": 1})

        def broken_dump(data, f, **kwargs):
            f.write('{"channel": 2, "обре')  # половина файла…
            raise OSError("диск отвалился")

        monkeypatch.setattr(utilities.json, "dump", broken_dump)

        with pytest.raises(OSError):
            worker.channel = 2

        monkeypatch.undo()
        assert json.loads((tmp_path / "data.json").read_text(encoding="utf-8")) == {"channel": 1}


class TestNewDataWorker:
    async def test_writes_in_background(self, tmp_path):
        worker = utilities.NewDataWorker(tmp_path / "x.json", setup={"a": ""})

        worker["a"] = "текст"
        await asyncio.sleep(0.1)

        assert json.loads((tmp_path / "x.json").read_text(encoding="utf-8")) == {"a": "текст"}
        await worker.close()

    async def test_survives_write_error_and_retries(self, tmp_path, monkeypatch):
        """Раньше первая же ошибка убивала задачу-писатель, и правки молча терялись."""
        monkeypatch.setattr(utilities.NewDataWorker, "RETRY_DELAY", 0.05)
        worker = utilities.NewDataWorker(tmp_path / "x.json", setup={"a": ""})
        await asyncio.sleep(0.05)

        real_write = utilities.JsonWorker._write_json
        calls = {"n": 0}

        def flaky(self, data):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError("файл занят другим процессом")
            real_write(self, data)

        monkeypatch.setattr(utilities.JsonWorker, "_write_json", flaky)

        worker["a"] = "первый"
        await asyncio.sleep(0.3)

        assert not worker._commit_task.done()  # задача жива
        assert json.loads((tmp_path / "x.json").read_text(encoding="utf-8")) == {"a": "первый"}

        worker["a"] = "второй"
        await asyncio.sleep(0.1)
        assert json.loads((tmp_path / "x.json").read_text(encoding="utf-8")) == {"a": "второй"}
        await worker.close()

    async def test_close_flushes_last_change(self, tmp_path):
        """Правка за мгновение до выключения не теряется."""
        worker = utilities.NewDataWorker(tmp_path / "x.json", setup={"a": ""})
        await asyncio.sleep(0.05)

        worker["a"] = "в последний момент"
        await worker.close()  # без паузы — фоновая задача не успела бы записать

        assert json.loads((tmp_path / "x.json").read_text(encoding="utf-8")) == {"a": "в последний момент"}

    async def test_keeps_existing_values_over_setup(self, tmp_path):
        (tmp_path / "x.json").write_text(json.dumps({"a": "было"}), encoding="utf-8")

        worker = utilities.NewDataWorker(tmp_path / "x.json", setup={"a": "", "b": ""})

        assert dict(worker) == {"a": "было", "b": ""}
        await worker.close()
