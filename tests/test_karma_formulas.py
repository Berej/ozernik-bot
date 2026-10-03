"""Тесты формул кармы: уровни, статусы Сансары, кубы, форматирование."""

from itertools import count

import pytest

from conftest import add_ozernik

_discord_ids = count(10_000)


def karma_obj(env, karma: int, status=None):
    user_id = add_ozernik(env, next(_discord_ids))
    env.db.set_karma(user_id, karma)
    if status:
        env.db.execute("UPDATE karma SET status = ? WHERE user_id = ?", (status, user_id))
    return env.db.get_karma(user_id)


# ---------- УРОВНИ -----------

class TestLevels:
    @pytest.mark.parametrize(
        ("level", "karma"),
        [
            (0, 0),
            (-3, 0),
            (1, 100),
            (9, 900),
            (10, 1000),
            (11, 1200),
            (20, 3000),
            (21, 3400),
            (25, 5000),
            (30, 7000),
            (31, 7800),
        ],
    )
    def test_get_karma(self, env, level, karma):
        assert env.karma.get_karma(level) == karma

    @pytest.mark.parametrize(
        ("karma", "level"),
        [
            (None, None),
            (-5, 0),
            (0, 0),
            (99, 0),
            (100, 1),
            (999, 9),
            (1000, 10),
            (1199, 10),
            (1200, 11),
            (5000, 25),
        ],
    )
    def test_get_level(self, env, karma, level):
        assert env.karma.get_level(karma) == level

    def test_level_and_karma_are_inverse(self, env):
        for level in range(0, 45):
            required = env.karma.get_karma(level)
            assert env.karma.get_level(required) == level
            if level > 0:
                assert env.karma.get_level(required - 1) == level - 1


# ---------- СТАТУСЫ САНСАРЫ -----------

class TestStatus:
    @pytest.mark.parametrize(
        ("karma", "tag"),
        [
            (0, "naraka"),
            (99, "naraka"),
            (100, "preta"),
            (999, "preta"),
            (1000, "animal"),
            (4999, "animal"),
            (5000, "human"),
            (10**6, "human"),
        ],
    )
    def test_get_status_by_karma(self, env, karma, tag):
        assert env.karma.get_status(karma_obj(env, karma))["tag_name"] == tag

    @pytest.mark.parametrize("status", ["asur", "deva"])
    def test_special_status_overrides_karma(self, env, status):
        assert env.karma.get_status(karma_obj(env, 0, status))["tag_name"] == status

    @pytest.mark.parametrize(
        ("karma", "next_tag"),
        [
            (0, "preta"),
            (100, "animal"),
            (1000, "human"),
        ],
    )
    def test_get_next_status(self, env, karma, next_tag):
        assert env.karma.get_next_status(karma_obj(env, karma))["tag_name"] == next_tag

    def test_no_next_status_for_human_and_special(self, env):
        assert env.karma.get_next_status(karma_obj(env, 5000)) is None
        assert env.karma.get_next_status(karma_obj(env, 0, "asur")) is None
        assert env.karma.get_next_status(karma_obj(env, 0, "deva")) is None

    def test_role_id_by_level(self, env):
        karma_roles = env.data.karma_roles
        for n, role in enumerate(karma_roles.values(), start=1):
            role["role_id"] = n
        env.data.karma_roles = karma_roles

        assert env.karma.get_role_id_by_level(0) == 1   # Нарака
        assert env.karma.get_role_id_by_level(1) == 2   # Прета
        assert env.karma.get_role_id_by_level(10) == 3  # Зверь
        assert env.karma.get_role_id_by_level(24) == 3
        assert env.karma.get_role_id_by_level(25) == 4  # Человек

    def test_level_up_text_substitutes_role(self, env):
        karma_roles = env.data.karma_roles
        karma_roles["preta"]["role_id"] = 777
        env.data.karma_roles = karma_roles
        env.levels["3"] = "Уровень 3! Вы теперь {role}."

        assert env.karma.get_level_up_text(3) == "Уровень 3! Вы теперь <@&777>."

    def test_level_up_text_default_is_empty(self, env):
        assert env.karma.get_level_up_text(5) == ""


# ---------- КУБЫ -----------

def add_binds(env, me: int, karmas: list[int]) -> None:
    for n, bind_karma in enumerate(karmas):
        other = add_ozernik(env, 50_000 + n)
        env.db.add_bind_karma(me, other, bind_karma) if bind_karma else env.db._ensure_karmic_bind(me, other)


class TestCubes:
    @pytest.mark.parametrize(
        ("bind_karma", "tag"),
        [
            (0, "black_cube"),
            (359, "black_cube"),
            (360, "white_cube"),
            (1439, "white_cube"),
            (1440, "blue_cube"),
            (5759, "blue_cube"),
            (5760, "gold_cube"),
        ],
    )
    def test_get_cube(self, env, bind_karma, tag):
        assert env.karma.get_cube(bind_karma)["tag_name"] == tag

    def test_cube_status_without_binds(self, env):
        me = add_ozernik(env, 1)

        assert env.karma.get_cube_status(me) is None

    def test_one_bind_gives_black_cube(self, env):
        me = add_ozernik(env, 1)
        add_binds(env, me, [1])

        assert env.karma.get_cube_status(me)["tag_name"] == "black_cube"

    def test_zero_karma_bind_also_counts_as_black(self, env):
        me = add_ozernik(env, 1)
        add_binds(env, me, [0])

        assert env.karma.get_cube_status(me)["tag_name"] == "black_cube"

    def test_white_cube_needs_required_cubes_binds(self, env):
        me = add_ozernik(env, 1)
        add_binds(env, me, [360] * 9)

        assert env.karma.get_cube_status(me)["tag_name"] == "black_cube"

        tenth = add_ozernik(env, 2)
        env.db.add_bind_karma(me, tenth, 360)

        assert env.karma.get_cube_status(me)["tag_name"] == "white_cube"

    def test_higher_binds_count_towards_lower_cubes(self, env):
        me = add_ozernik(env, 1)
        add_binds(env, me, [5760] * 5 + [1440] * 5)

        assert env.karma.get_cube_status(me)["tag_name"] == "blue_cube"

    def test_gold_cube(self, env):
        me = add_ozernik(env, 1)
        add_binds(env, me, [5760] * 10)

        assert env.karma.get_cube_status(me)["tag_name"] == "gold_cube"

    def test_required_cubes_setting(self, env):
        env.data.required_cubes = 2
        me = add_ozernik(env, 1)
        add_binds(env, me, [360, 360])

        assert env.karma.get_cube_status(me)["tag_name"] == "white_cube"

    def test_get_cubes_counts(self, env):
        me = add_ozernik(env, 1)
        add_binds(env, me, [5760, 1440, 360, 10])

        counts = {cube["tag_name"]: cube["count"] for cube in env.karma.get_cubes(me)}

        assert counts == {"black_cube": 4, "white_cube": 3, "blue_cube": 2, "gold_cube": 1}

    def test_get_cubes_does_not_mutate_settings(self, env):
        me = add_ozernik(env, 1)
        add_binds(env, me, [10])

        env.karma.get_cubes(me)

        assert "count" not in env.data.cube_roles["black_cube"]

    def test_get_next_cube(self, env):
        me = add_ozernik(env, 1)
        assert env.karma.get_next_cube(me)["tag_name"] == "black_cube"

        add_binds(env, me, [1])
        assert env.karma.get_next_cube(me)["tag_name"] == "white_cube"

    def test_next_cube_after_gold_is_gold(self, env):
        me = add_ozernik(env, 1)
        add_binds(env, me, [5760] * 10)

        assert env.karma.get_next_cube(me)["tag_name"] == "gold_cube"

    def test_get_next_cube_from_cube(self, env):
        assert env.karma.get_next_cube_from_cube("black_cube")["tag_name"] == "white_cube"
        assert env.karma.get_next_cube_from_cube("blue_cube")["tag_name"] == "gold_cube"
        assert env.karma.get_next_cube_from_cube("gold_cube") is None

        with pytest.raises(ValueError):
            env.karma.get_next_cube_from_cube("pink_cube")


# ---------- ФОРМАТИРОВАНИЕ -----------

class TestFormatting:
    @pytest.mark.parametrize(
        ("minutes", "text"),
        [
            (0, ""),
            (1, "1 минута"),
            (2, "2 минуты"),
            (5, "5 минут"),
            (11, "11 минут"),
            (21, "21 минута"),
            (60, "1 час"),
            (61, "1 час, 1 минута"),
            (360, "6 часов"),
            (1440, "1 день"),
            (5760, "4 дня"),
            (1440 * 5 + 65, "5 дней, 1 час, 5 минут"),
        ],
    )
    def test_format_duration_minutes(self, env, minutes, text):
        assert env.karma.format_duration_minutes(minutes) == text

    def test_format_duration_minutes_backtick(self, env):
        assert env.karma.format_duration_minutes(61, True) == "`1` час, `1` минута"

    @pytest.mark.parametrize(
        ("seconds", "text"),
        [
            (0, ""),
            (1, "1 секунда"),
            (3, "3 секунды"),
            (12, "12 секунд"),
            (3661, "1 час, 1 минута, 1 секунда"),
            (86400, "1 день"),
        ],
    )
    def test_format_duration_seconds(self, env, seconds, text):
        assert env.karma.format_duration_seconds(seconds) == text

    def test_format_duration_seconds_backtick(self, env):
        assert env.karma.format_duration_seconds(2, True) == "`2` секунды"

    def test_collapse_and_expand_roundtrip(self, env):
        source = {"1": "Текст с 'кавычками'", "2": "", "nested": {"a": 1}}

        text = env.karma.collapse_dict(source)

        assert env.karma.expand_dict(text) == source

    def test_collapse_format(self, env):
        assert env.karma.collapse_dict({"1": "a", "2": "b"}) == "'1': 'a',\n'2': 'b'"

    def test_expand_invalid_raises_syntax_error(self, env):
        with pytest.raises(SyntaxError):
            env.karma.expand_dict("'1': 'не закрыто")


@pytest.mark.parametrize(
    ("level", "line"),
    [
        (1, "Вы достигли 1 уровня."),
        (40, "Вы достигли 40 уровня."),
        (41, "Вы добились 41 уровня."),
        (80, "Вы добились 80 уровня."),
        (81, "Вы доползли до 81 уровня."),
        (100, "Вы доползли до 100 уровня."),
    ],
)
def test_level_up_line(env, level, line):
    """Глагол по уровню, как в старой Сансаре: достигли → добились → доползли до."""
    assert env.karma.get_level_up_line(level) == line


def test_level_up_description_all_bold_multiline(env):
    """Весь текст поздравления жирным — каждая строка отдельно, пустые строки не трогаем."""
    env.db.set_level_texts({5: "Строка один\n\nСтрока два"})

    text = env.karma.build_level_up_description(5, previous_karma=450)

    assert text == "**Строка один**\n\n**Строка два**\n**Вы достигли 5 уровня.**"
