"""
Страницы карточек кармы и карма за ивенты.

,rs — одна страница (решение Alium 2026-11-14: «Где пишу/сижу», «Ивенты» и «Другие обличья»
переехали в плашку *i — tests/test_stats.py). ,rc — по 10 связей на страницу.
Кнопки ◀ / ▶ — DynamicItem: чья карточка, какая страница и кто вызвал записаны в custom_id;
листать может только вызвавший (решение Alium 2026-11-14).
"""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from conftest import FakeChannel, add_ozernik, make_interaction, make_message
import modules.karma_sistem._db as karma_db

# ---------- СТРАНИЦЫ КАРТОЧЕК -----------

class TestCardPages:
    async def test_sansara_is_one_page(self, env):
        member = env.guild.add_member(1)

        file, kwargs = await env.cog.build_card_page("rs", member, 0)

        assert file.filename == "user1_sansara_rank.png"
        assert kwargs == {}  # одна страница — кнопок нет

    async def test_old_sansara_button_opens_card(self, env):
        """Кнопки старых сообщений ,rs (когда страниц было пять) открывают саму карточку."""
        member = env.guild.add_member(1)

        file, kwargs = await env.cog.build_card_page("rs", member, 3)

        assert (file.filename, kwargs) == ("user1_sansara_rank.png", {})

    async def test_cube_one_page_has_no_buttons(self, env):
        member = env.guild.add_member(1)

        _, kwargs = await env.cog.build_card_page("rc", member)

        assert kwargs == {}

    async def test_cube_pages_by_ten_binds(self, env):
        member = env.guild.add_member(1)
        me = env.bot.db_ensure_user(member).id
        for n in range(2, 17):  # 15 связей
            env.guild.add_member(n)
            env.db.add_bind_karma(me, add_ozernik(env, n), 100 - n)

        calls = []
        original = env.karma.get_full_binds

        async def spy(member_, bot, amount=10, offset=0):
            calls.append((amount, offset))
            return await original(member_, bot, amount, offset)

        env.karma.get_full_binds, saved = spy, original
        try:
            _, kwargs = await env.cog.build_card_page("rc", member, 1)
            # Связей стало меньше страниц (например, страница 5 из старой кнопки) — по кругу.
            await env.cog.build_card_page("rc", member, 4)
        finally:
            env.karma.get_full_binds = saved

        assert kwargs["view"].children[1].label == "Связи · 2/2"
        assert calls == [(10, 10), (10, 0)]

    async def test_shortcut_sansara_without_buttons(self, env):
        member = env.guild.add_member(1)
        message = make_message(member, FakeChannel(3000), env.guild, content=",rs")

        await env.cog.on_message(message)

        assert message.reply.call_args.kwargs["file"].filename == "user1_sansara_rank.png"
        assert "view" not in message.reply.call_args.kwargs

    async def test_slash_sansara_without_buttons(self, env):
        member = env.guild.add_member(1)
        interaction = make_interaction(member, env.guild)

        await env.cog.rank_sansara.callback(env.cog, interaction)

        assert "view" not in interaction.followup.send.call_args.kwargs

    async def test_turn_cube_page(self, env):
        owner = env.guild.add_member(1)
        me = env.bot.db_ensure_user(owner).id
        for n in range(2, 14):  # 12 связей — две страницы
            env.guild.add_member(n)
            env.db.add_bind_karma(me, add_ozernik(env, n), 100 - n)
        caller = env.guild.add_member(50)  # вызвал ,rc @1 — листает он
        interaction = make_interaction(caller, env.guild)
        interaction.edit_original_response = AsyncMock()

        await env.cog.turn_card_page(interaction, "rc", owner.id, 1, caller.id)

        interaction.response.defer.assert_awaited_once()
        kwargs = interaction.edit_original_response.call_args.kwargs
        assert kwargs["attachments"][0].filename == "user1_cube_rank.png"
        assert kwargs["view"].children[1].label == "Связи · 2/2"
        assert kwargs["view"].children[2].custom_id == "karma:card:rc:1:0:next:50"  # и дальше — только он

    async def test_shortcut_cube_buttons_for_author(self, env):
        owner = env.guild.add_member(1)
        me = env.bot.db_ensure_user(owner).id
        for n in range(2, 14):
            env.guild.add_member(n)
            env.db.add_bind_karma(me, add_ozernik(env, n), 100 - n)
        author = env.guild.add_member(50)
        message = make_message(author, FakeChannel(3000), env.guild, content=",rc <@1>")
        message.mentions = [owner]

        await env.cog.on_message(message)

        ids = [item.custom_id for item in message.reply.call_args.kwargs["view"].children]
        assert ids == ["karma:card:rc:1:1:prev:50", "karma:card_label:rc:1", "karma:card:rc:1:1:next:50"]

    async def test_old_sansara_button_removes_buttons(self, env):
        """
        Нажата старая ▶ под ,rs: карточка без кнопок, и view=None передаётся явно —
        иначе Discord оставил бы под сообщением прежние кнопки.
        """
        owner = env.guild.add_member(1)
        interaction = make_interaction(env.guild.add_member(2), env.guild)
        interaction.edit_original_response = AsyncMock()

        await env.cog.turn_card_page(interaction, "rs", owner.id, 2)

        kwargs = interaction.edit_original_response.call_args.kwargs
        assert kwargs["attachments"][0].filename == "user1_sansara_rank.png"
        assert "view" in kwargs and kwargs["view"] is None

    async def test_turn_page_of_member_who_left(self, env):
        """Человек ушёл с сервера — карточку всё равно можно открыть (берётся через fetch_user)."""
        gone = env.guild.add_member(1)
        env.bot.db_ensure_user(gone)
        env.guild.members.remove(gone)
        env.bot.fetch_user = AsyncMock(return_value=gone)
        interaction = make_interaction(env.guild.add_member(2), env.guild)
        interaction.edit_original_response = AsyncMock()

        await env.cog.turn_card_page(interaction, "rs", 1, 0)

        assert interaction.edit_original_response.call_args.kwargs["attachments"][0].filename == "user1_sansara_rank.png"


def pressed_by(env, user_id, cog_present=True):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        client=SimpleNamespace(get_cog=lambda name: env.cog if cog_present and name == "KarmaSistem" else None),
        response=SimpleNamespace(send_message=AsyncMock()),
    )


class TestCardPageButton:
    async def test_from_custom_id(self, env):
        button = env.karma.CardPageButton("rc", 42, 3, "next", 50)
        match = button.__discord_ui_compiled_template__.fullmatch(button.custom_id)

        restored = await env.karma.CardPageButton.from_custom_id(None, button.item, match)

        assert button.custom_id == "karma:card:rc:42:3:next:50"
        assert (restored.card, restored.user_id, restored.page, restored.invoker_id) == ("rc", 42, 3, 50)
        assert restored.item.label == "▶"

    async def test_old_button_without_invoker_still_recognized(self, env):
        """Кнопки сообщений, отправленных до 2026-11-14 (без вызвавшего), не должны стать мёртвыми."""
        match = env.karma.CardPageButton.__discord_ui_compiled_template__.fullmatch("karma:card:rc:42:1:prev")

        restored = await env.karma.CardPageButton.from_custom_id(None, None, match)

        assert (restored.user_id, restored.invoker_id) == (42, None)

    async def test_callback_calls_cog(self, env):
        button = env.karma.CardPageButton("rc", 42, 1, "prev", 50)
        env.cog.turn_card_page = AsyncMock()
        interaction = pressed_by(env, 50)

        await button.callback(interaction)

        env.cog.turn_card_page.assert_awaited_once_with(interaction, "rc", 42, 1, 50)

    @pytest.mark.parametrize(("invoker", "presser", "allowed"), [
        (50, 50, True),     # вызвавший
        (50, 42, False),    # владелец карточки, но вызвал другой
        (50, 7, False),     # посторонний
        (None, 42, True),   # старая кнопка — листает владелец
        (None, 7, False),
    ])
    async def test_only_invoker_turns(self, env, invoker, presser, allowed):
        button = env.karma.CardPageButton("rc", 42, 1, "next", invoker)
        env.cog.turn_card_page = AsyncMock()
        interaction = pressed_by(env, presser)

        await button.callback(interaction)

        assert env.cog.turn_card_page.await_count == (1 if allowed else 0)
        if not allowed:
            assert interaction.response.send_message.call_args.args[0] == "Листать может только тот, кто вызвал карточку."
            assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    async def test_karma_disabled(self, env):
        button = env.karma.CardPageButton("rs", 42, 1, "prev", 50)
        interaction = pressed_by(env, 50, cog_present=False)

        await button.callback(interaction)

        assert interaction.response.send_message.call_args.args[0] == "Карма сейчас выключена."

    def test_registered_on_cog_load(self, env, monkeypatch):
        monkeypatch.setattr(env.karma.tasks.Loop, "start", lambda self, *args, **kwargs: None)
        env.bot.add_view = lambda view: None

        env.cog.cog_load()

        env.bot.add_dynamic_items.assert_called_once_with(env.karma.CardPageButton)


# ---------- КАРМА ЗА ИВЕНТЫ -----------

def admin_interaction(env):
    admin = env.guild.add_member(900)
    admin.guild_permissions.administrator = True
    return make_interaction(admin, env.guild)


class TestEventKarma:
    def test_db_award(self, env):
        me = add_ozernik(env, 1)

        old, new, award_id = env.db.add_event_karma(me, 100, "Квиз", 900)

        assert (old.karma, new.karma, new.weekly_karma) == (0, 100, 0)
        assert env.db.get_event_karma(me) == (100, 1)
        award = env.db.get_event_awards(me)[0]
        assert (award["id"], award["amount"], award["reason"], award["admin_discord_id"]) == (award_id, 100, "Квиз", 900)

    def test_awards_newest_first(self, env, monkeypatch):
        me = add_ozernik(env, 1)
        for n, reason in enumerate(("первый", "второй", "третий")):
            monkeypatch.setattr(karma_db, "now_ms", lambda n=n: 1_000 * (n + 1))
            env.db.add_event_karma(me, 10, reason)

        assert [a["reason"] for a in env.db.get_event_awards(me, 2)] == ["третий", "второй"]

    async def test_karma_add_with_reason(self, env):
        target = env.guild.add_member(2)
        target.send = AsyncMock()
        env.bot.get_user = env.guild.get_member
        interaction = admin_interaction(env)

        await env.cog.karma_add.callback(env.cog, interaction, target, 150, "Победа в квизе")

        me = env.db.get_user_by_discord_id(2).id
        assert env.db.get_event_karma(me) == (150, 1)
        assert "Причина: Победа в квизе (награда #1)" in interaction.followup.send.call_args.args[0]
        assert target.send.call_args.args[0] == "Вам начислено **150** кармы: Победа в квизе"

    async def test_karma_add_bot_gets_no_dm(self, env):
        bot_member = env.guild.add_member(50, bot=True)
        bot_member.send = AsyncMock()
        env.bot.get_user = env.guild.get_member

        await env.cog.karma_add.callback(env.cog, admin_interaction(env), bot_member, 10)

        bot_member.send.assert_not_awaited()

    @pytest.mark.parametrize("command", ["karma_remove", "karma_set"])
    async def test_remove_and_set_keep_event_karma(self, env, command):
        target = env.guild.add_member(2)
        await env.cog.karma_add.callback(env.cog, admin_interaction(env), target, 150)

        await getattr(env.cog, command).callback(env.cog, admin_interaction(env), target, 0 if command == "karma_set" else 100)

        me = env.db.get_user_by_discord_id(2).id
        assert env.db.get_event_karma(me) == (150, 1)

    async def test_event_delete(self, env):
        target = env.guild.add_member(2)
        await env.cog.karma_add.callback(env.cog, admin_interaction(env), target, 150, "Ошибка")
        interaction = admin_interaction(env)

        await env.cog.karma_event_delete.callback(env.cog, interaction, 1)

        me = env.db.get_user_by_discord_id(2).id
        assert env.db.get_event_karma(me) == (0, 0)
        assert env.db.get_karma(me).karma == 150  # карма не меняется
        assert interaction.response.send_message.call_args.args[0].startswith("**Награда #1 удалена** у <@2>")

    async def test_event_delete_missing(self, env):
        interaction = admin_interaction(env)

        await env.cog.karma_event_delete.callback(env.cog, interaction, 7)

        assert interaction.response.send_message.call_args.args[0] == "Награды #7 нет."

    async def test_event_delete_only_admin(self, env):
        interaction = make_interaction(env.guild.add_member(3), env.guild)

        await env.cog.karma_event_delete.callback(env.cog, interaction, 1)

        assert interaction.response.send_message.call_args.args[0] == "Только для Администраторов."

    def test_link_twink_moves_awards(self, env):
        main, twink = add_ozernik(env, 1), add_ozernik(env, 11)
        env.db.add_event_karma(twink, 30, "Конкурс")

        env.db.link_twink(main, 11)

        assert env.db.get_event_karma(main) == (30, 1)

    def test_event_awards_summary_for_stats(self, env, monkeypatch):
        """«Ивенты» показывает плашка *i — данные отдаёт модуль кармы (награды — его данные)."""
        me = add_ozernik(env, 1)
        for n in range(7):
            monkeypatch.setattr(karma_db, "now_ms", lambda n=n: 1_000 * (n + 1))
            env.db.add_event_karma(me, 10, f"Ивент {n}")

        summary = env.cog.event_awards_summary(me)

        assert (summary["total"], summary["count"]) == (70, 7)
        assert [a["reason"] for a in summary["awards"]] == [f"Ивент {n}" for n in (6, 5, 4, 3, 2)]
        assert summary["awards"][0]["created_at"] == 7  # секунды, не мс


# ---------- «ДРУГИЕ ОБЛИЧЬЯ» -----------

class TestCardOfTwink:
    """
    Карточка, открытая с твинка (или по твинку): ник и аватар — твинка,
    статистика — общая, человека (решение Alium 2026-10-23).
    """

    @pytest.mark.parametrize(("card", "page", "creator"), [
        ("rs", 0, "create_rank_card"),
        ("rc", 0, "create_cube_cart"),
    ])
    async def test_twink_name_and_avatar_on_every_page(self, env, monkeypatch, card, page, creator):
        main = env.guild.add_member(1)
        twink = env.guild.add_member(11)
        twink_avatar = Image.new("RGBA", (220, 220), (10, 200, 30, 255))
        buffer = BytesIO()
        twink_avatar.save(buffer, format="PNG")
        twink.display_avatar.read = AsyncMock(return_value=buffer.getvalue())

        main_id = env.bot.db_ensure_user(main).id
        env.db.link_twink(main_id, 11)
        env.db.set_karma(main_id, 1500)

        captured = {}

        def fake(*args):
            captured["args"] = args
            return BytesIO(b"png")

        monkeypatch.setattr(env.karma, creator, fake)

        file, _ = await env.cog.build_card_page(card, twink, page)

        ozernik, avatar, username = captured["args"][:3]
        assert ozernik.id == main_id  # статистика — человека
        assert username == "User 11"
        assert avatar.getpixel((110, 110))[:3] == (10, 200, 30)  # аватар твинка
        assert file.filename.startswith("user11_")
