"""Проверки, что карточки Pillow рисуются без ошибок в разных состояниях пользователя."""

import pytest
from PIL import Image

from conftest import PNG_BYTES, add_ozernik, make_png_bytes


def avatar(size=(220, 220)) -> Image.Image:
    return Image.new("RGBA", size, (20, 120, 200, 255))


def assert_png(buffer, size=None):
    buffer.seek(0)
    with Image.open(buffer) as image:
        assert image.format == "PNG"
        if size:
            assert image.size == size


@pytest.mark.parametrize(
    ("karma", "status"),
    [
        (0, None),
        (150, None),
        (1234, None),
        (6000, None),
        (10**6, None),
        (0, "asur"),
        (0, "deva"),
    ],
)
def test_rank_card(env, karma, status):
    user_id = add_ozernik(env, 1)
    env.db.set_karma(user_id, karma)
    env.db.add_karma(user_id, 3, weekly=True)
    if status:
        env.db.execute("UPDATE karma SET status = ? WHERE user_id = ?", (status, user_id))

    buffer = env.karma.create_rank_card(env.db.get_user(user_id), avatar(), "Очень длинное имя пользователя")

    assert_png(buffer)


@pytest.mark.parametrize("bind_karmas", [[], [1], [400] * 10, [6000] * 12])
def test_rank_card_with_cubes(env, bind_karmas):
    me = add_ozernik(env, 1)
    for n, bind_karma in enumerate(bind_karmas):
        env.db.add_bind_karma(me, add_ozernik(env, 100 + n), bind_karma)

    assert_png(env.karma.create_rank_card(env.db.get_user(me), avatar(), "User"))


@pytest.mark.parametrize("bind_karma", [1, 360, 1440, 5760])
def test_bind_up_postcard(env, bind_karma):
    first = add_ozernik(env, 1)
    second = add_ozernik(env, 2)
    bind = env.db.add_bind_karma(first, second, bind_karma)

    assert_png(env.karma.create_bind_up_postcard(bind, avatar(), avatar()))


@pytest.mark.parametrize("binds_count", [0, 1, 10, 15])
async def test_cube_card(env, binds_count):
    member = env.guild.add_member(1)
    me = env.bot.db_ensure_user(member).id
    for n in range(binds_count):
        friend = env.guild.add_member(100 + n)
        env.db.add_bind_karma(me, env.bot.db_ensure_user(friend).id, 360 * (n + 1))

    full_binds = await env.karma.get_full_binds(member, env.bot)

    assert len(full_binds) == min(binds_count, 10)
    assert_png(env.karma.create_cube_cart(env.db.get_user(me), avatar(), member.display_name, full_binds))


async def test_full_binds_fetches_users_not_on_server(env):
    member = env.guild.add_member(1)
    gone = env.guild.add_member(2)
    env.db.add_bind_karma(env.bot.db_ensure_user(member).id, env.bot.db_ensure_user(gone).id, 5)
    env.guild.members.remove(gone)
    env.bot.fetch_user.side_effect = lambda user_id: gone

    full_binds = await env.karma.get_full_binds(member, env.bot)

    env.bot.fetch_user.assert_awaited_once_with(2)
    assert full_binds[0]["name"] == gone.display_name


async def test_discord_avatar_resized(env):
    member = env.guild.add_member(1)
    member.display_avatar.read.return_value = make_png_bytes((512, 300))

    image = await env.karma.get_discord_avatar(member)

    assert image.size == (220, 220)
    assert image.mode == "RGBA"


def test_load_icon(env, tmp_path):
    path = tmp_path / "icon.png"
    path.write_bytes(PNG_BYTES)

    assert env.karma.load_icon(path, (32, 32)).size == (32, 32)


@pytest.mark.parametrize("card", ["rank", "cube"])
def test_cards_render_without_database(env, monkeypatch, card):
    """Карточки рисуются в отдельном потоке — всё из базы должно прийти через stats."""
    me = add_ozernik(env, 1)
    env.db.set_karma(me, 1500)
    env.db.add_bind_karma(me, add_ozernik(env, 2), 400)
    ozernik = env.db.get_user(me)
    stats = env.karma.get_card_stats(me)

    monkeypatch.setattr(env.karma._state, "db", None)  # любое обращение к базе упадёт

    if card == "rank":
        buffer = env.karma.create_rank_card(ozernik, avatar(), "User", stats)
    else:
        buffer = env.karma.create_cube_cart(ozernik, avatar(), "User", [], stats)

    assert_png(buffer)
