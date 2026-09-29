"""Тесты сокращений команд (,rs ,rc ,lb ,lbw ,lbc и старого ,r)."""

import discord
import pytest

from conftest import FakeChannel, make_message


@pytest.fixture
def channel():
    return FakeChannel(3000, name="general")


def karma_of(env, member) -> int:
    ozernik = env.db.get_user_by_discord_id(member.id)
    return env.db.get_karma(ozernik.id).karma if ozernik else 0


def fill_boards(env, *members):
    """Карма, недельная карма и связь, чтобы все три топа были непустыми."""
    ids = [env.bot.db_ensure_user(member).id for member in members]
    for n, user_id in enumerate(ids, 1):
        env.db.add_karma(user_id, 100 * n, weekly=True)
    env.db.add_bind_karma(ids[0], ids[1], 30)


@pytest.mark.parametrize(
    ("shortcut", "filename"),
    [
        (",rs", "user1_sansara_rank.png"),
        (",r", "user1_sansara_rank.png"),
        (",rc", "user1_cube_rank.png"),
    ],
)
async def test_card_shortcuts(env, channel, shortcut, filename):
    member = env.guild.add_member(1)
    message = make_message(member, channel, env.guild, content=shortcut)

    await env.cog.on_message(message)

    file = message.reply.call_args.kwargs["file"]
    assert isinstance(file, discord.File)
    assert file.filename == filename
    assert message.reply.call_args.kwargs["allowed_mentions"].replied_user is False
    assert karma_of(env, member) == 0  # сокращение карму не даёт


@pytest.mark.parametrize(
    ("shortcut", "title"),
    [
        (",lb", "Таблица лидеров"),
        (",lbw", "Таблица лидеров"),
        (",lbc", "Пары"),
    ],
)
async def test_leaderboard_shortcuts(env, channel, shortcut, title):
    first, second = env.guild.add_member(1), env.guild.add_member(2)
    fill_boards(env, first, second)
    message = make_message(first, channel, env.guild, content=shortcut)

    await env.cog.on_message(message)

    embed = message.reply.call_args.kwargs["embed"]
    assert embed.title == title
    assert "<@2>" in embed.description
    assert karma_of(env, first) == 100


async def test_leaderboard_shortcut_matches_slash_command(env, channel):
    from conftest import make_interaction

    first, second = env.guild.add_member(1), env.guild.add_member(2)
    fill_boards(env, first, second)
    message = make_message(first, channel, env.guild, content=",lbw")
    interaction = make_interaction(first, env.guild)

    await env.cog.on_message(message)
    await env.cog.leaderboard_weekly.callback(env.cog, interaction)

    shortcut_embed = message.reply.call_args.kwargs["embed"]
    slash_embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert shortcut_embed.description == slash_embed.description


@pytest.mark.parametrize(
    ("shortcut", "text"),
    [
        (",lb", "Нет таблицы лидеров."),
        (",lbw", "Нет недельной таблицы лидеров."),
        (",lbc", "Нет таблиц лидеров Связи."),
    ],
)
async def test_empty_leaderboards(env, channel, shortcut, text):
    member = env.guild.add_member(1)
    message = make_message(member, channel, env.guild, content=shortcut)

    await env.cog.on_message(message)

    assert message.reply.call_args.args[0] == text


async def test_card_for_mentioned_member(env, channel):
    member = env.guild.add_member(1)
    other = env.guild.add_member(2)
    message = make_message(member, channel, env.guild, content=",rs <@2>")
    message.mentions = [other]

    await env.cog.on_message(message)

    assert message.reply.call_args.kwargs["file"].filename == "user2_sansara_rank.png"


async def test_mention_of_non_member_falls_back_to_author(env, channel):
    member = env.guild.add_member(1)
    message = make_message(member, channel, env.guild, content=",rc <@999>")
    message.mentions = [type("User", (), {"id": 999})()]

    await env.cog.on_message(message)

    assert message.reply.call_args.kwargs["file"].filename == "user1_cube_rank.png"


@pytest.mark.parametrize("content", [",LB", ",Lb  спасибо", "  ,lb"])
async def test_case_and_extra_words(env, channel, content):
    member = env.guild.add_member(1)
    env.db.add_karma(env.bot.db_ensure_user(member).id, 5)
    message = make_message(member, channel, env.guild, content=content)

    await env.cog.on_message(message)

    assert message.reply.call_args.kwargs["embed"].title == "Таблица лидеров"


@pytest.mark.parametrize("content", [",rofl", ",ребята", ",lbx", "lb", ", lb", "привет ,lb", ""])
async def test_not_shortcuts_give_karma(env, channel, content):
    member = env.guild.add_member(1)
    message = make_message(member, channel, env.guild, content=content)

    await env.cog.on_message(message)

    message.reply.assert_not_awaited()
    assert karma_of(env, member) == 1


def test_slash_descriptions_mention_shortcuts(env):
    descriptions = {command.name: command.description for command in env.cog.get_app_commands()}

    assert descriptions["rank_sansara"].endswith("Сокращение: ,rs")
    assert descriptions["rank_cube"].endswith("Сокращение: ,rc")
    assert descriptions["leaderboard"].endswith("Сокращение: ,lb")
    assert descriptions["leaderboard_weekly"].endswith("Сокращение: ,lbw")
    assert descriptions["leaderboard_cubes"].endswith("Сокращение: ,lbc")
