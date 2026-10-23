"""
Статистика по каналам и страницы карточек (,rs и ,rc).

Сансара: 1 — карточка, 2 — «Где пишу» (карма по текстовым каналам и категориям),
3 — «Где сижу» (минуты в голосовых). Кубы: по 10 связей на страницу.
Кнопки ◀ / ▶ — DynamicItem: чья карточка и какая страница записаны в custom_id.
"""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from PIL import Image

from conftest import FakeChannel, add_ozernik, make_interaction, make_message
import modules.karma_sistem._db as karma_db

AVATAR = Image.new("RGBA", (220, 220), (90, 140, 200, 255))


def channel(channel_id, name, category=None):
    """Канал сервера: имя и категория — для страниц статистики."""
    ch = FakeChannel(channel_id, name=name)
    ch.category = SimpleNamespace(name=category) if category else None
    return ch


def put_channels(env, *channels):
    by_id = {ch.id: ch for ch in channels}
    env.guild.get_channel = by_id.get


# ---------- БАЗА -----------

class TestChannelStatsDatabase:
    def test_add_karma_with_channel(self, env):
        me = add_ozernik(env, 1)

        env.db.add_karma(me, 1, True, channel_id=3000)
        env.db.add_karma(me, 1, True, channel_id=3000)
        env.db.add_karma(me, 1, True, channel_id=3001)

        assert env.db.get_channel_stats(me, "text") == [(3000, 2), (3001, 1)]
        assert env.db.get_channel_stats(me, "voice") == []

    def test_add_karma_without_channel_keeps_stats_empty(self, env):
        """Админские /karma_add идут без канала — в статистику не попадают."""
        me = add_ozernik(env, 1)

        env.db.add_karma(me, 50)

        assert env.db.get_channel_stats(me, "text") == []

    def test_voice_minutes_for_every_participant(self, env):
        a, b, c = (add_ozernik(env, i) for i in (1, 2, 3))

        env.db.add_bind_karma_many([(a, b), (a, c), (b, c)], karma=1, channel_id=6000)

        for user_id in (a, b, c):
            assert env.db.get_channel_stats(user_id, "voice") == [(6000, 1)]  # минута, а не по минуте на пару

    def test_link_twink_merges_stats(self, env):
        main, twink = add_ozernik(env, 1), add_ozernik(env, 11)
        env.db.add_karma(main, 1, channel_id=3000)
        env.db.add_karma(twink, 1, channel_id=3000)
        env.db.add_karma(twink, 1, channel_id=3001)

        env.db.link_twink(main, 11)

        assert env.db.get_channel_stats(main, "text") == [(3000, 2), (3001, 1)]


# ---------- НАЧИСЛЕНИЕ -----------

class TestChannelStatsCollection:
    async def test_message_counts_to_channel(self, env):
        member = env.guild.add_member(1)

        await env.cog.on_message(make_message(member, FakeChannel(3000), env.guild))

        me = env.db.get_user_by_discord_id(1).id
        assert env.db.get_channel_stats(me, "text") == [(3000, 1)]

    async def test_thread_counts_to_parent(self, env):
        member = env.guild.add_member(1)
        thread = FakeChannel(3500)
        thread.parent_id = 3000

        await env.cog.on_message(make_message(member, thread, env.guild))

        me = env.db.get_user_by_discord_id(1).id
        assert env.db.get_channel_stats(me, "text") == [(3000, 1)]

    async def test_blocked_channel_not_counted(self, env):
        env.data.blocked_channels_id = [3000]
        member = env.guild.add_member(1)

        await env.cog.on_message(make_message(member, FakeChannel(3000), env.guild))

        me = env.db.get_user_by_discord_id(1).id
        assert env.db.get_channel_stats(me, "text") == []

    async def test_voice_loop_counts_minutes(self, env):
        a, b = env.guild.add_member(1), env.guild.add_member(2)
        env.guild.voice_channels.append(FakeChannel(6000, members=[a, b]))

        await env.cog.voice_karma_check()

        me = env.db.get_user_by_discord_id(1).id
        assert env.db.get_channel_stats(me, "voice") == [(6000, 1)]

    async def test_alone_in_voice_not_counted(self, env):
        """Минуты — на тех же условиях, что и Связь: одному не начисляется."""
        a = env.guild.add_member(1)
        env.guild.voice_channels.append(FakeChannel(6000, members=[a]))

        await env.cog.voice_karma_check()

        ozernik = env.db.get_user_by_discord_id(1)
        assert ozernik is None or env.db.get_channel_stats(ozernik.id, "voice") == []


# ---------- СТРАНИЦЫ «ГДЕ ПИШУ» / «ГДЕ СИЖУ» -----------

class TestStatsPage:
    def test_channels_and_categories(self, env):
        me = add_ozernik(env, 1)
        put_channels(
            env,
            channel(3000, "чат", "ОБЩЕНИЕ"),
            channel(3001, "мемы", "ОБЩЕНИЕ"),
            channel(3002, "арт", "ТВОРЧЕСТВО"),
            channel(3003, "без-папки"),
        )
        for channel_id, amount in ((3000, 10), (3001, 5), (3002, 7), (3003, 1)):
            env.db.add_karma(me, amount, channel_id=channel_id)

        page = env.karma.get_channel_stats_page(me, "text", env.guild)

        assert page["channels"] == [("чат", 10), ("арт", 7), ("мемы", 5), ("без-папки", 1)]
        assert page["categories"] == [("ОБЩЕНИЕ", 15), ("ТВОРЧЕСТВО", 7), ("Без категории", 1)]
        assert page["total"] == 23

    def test_top_five_and_deleted_channels_skipped(self, env):
        me = add_ozernik(env, 1)
        channels = [channel(3000 + n, f"канал{n}", "К") for n in range(7)]
        put_channels(env, *channels)
        for n in range(7):
            env.db.add_karma(me, 10 - n, channel_id=3000 + n)
        env.db.add_karma(me, 100, channel_id=9999)  # канал удалён

        page = env.karma.get_channel_stats_page(me, "text", env.guild)

        assert [name for name, _ in page["channels"]] == [f"канал{n}" for n in range(5)]
        assert page["categories"] == [("К", sum(range(4, 11)))]

    def test_drop_missing_glyphs(self, env):
        font = env.karma.ImageFont.truetype(env.karma.assets.fonts["Roboto-Bold"], size=24)

        assert env.karma.drop_missing_glyphs("💬・общий-чат", font) == "общий-чат"
        assert env.karma.drop_missing_glyphs("🔊 Озеро 🔊", font) == "Озеро"
        assert env.karma.drop_missing_glyphs("🎮", font) == ""

    @pytest.mark.parametrize(("minutes", "text"), [(45, "45 мин"), (60, "1 ч"), (200, "3 ч 20 мин"), (6005, "100 ч")])
    def test_format_minutes_short(self, env, minutes, text):
        assert env.karma.format_minutes_short(minutes) == text

    @pytest.mark.parametrize("kind", ["text", "voice"])
    def test_render_without_database(self, env, monkeypatch, kind):
        """Рисуется в отдельном потоке — к базе не обращается."""
        page = {"kind": kind, "channels": [("💬・чат", 120), ("x" * 80, 3)], "categories": [("К", 123)], "total": 123}
        monkeypatch.setattr(env.karma._state, "db", None)

        buffer = env.karma.create_stats_card(AVATAR, "Очень длинное имя пользователя " * 3, "#C9A0FF", page)

        assert Image.open(BytesIO(buffer.getvalue())).size == (900, 480)

    def test_render_empty(self, env):
        page = {"kind": "voice", "channels": [], "categories": [], "total": 0}
        buffer = env.karma.create_stats_card(AVATAR, "User", "#C9A0FF", page)
        assert Image.open(BytesIO(buffer.getvalue())).size == (900, 480)


# ---------- СТРАНИЦЫ КАРТОЧЕК -----------

def custom_ids(view):
    return [item.custom_id for item in view.children]


class TestCardPages:
    async def test_sansara_pages(self, env):
        member = env.guild.add_member(1)
        put_channels(env)

        names = []
        for page in range(4):
            file, view = await env.cog.build_card_page("rs", member, page)
            names.append(file.filename)

        assert names == ["user1_sansara_rank.png", "user1_sansara_text.png", "user1_sansara_voice.png", "user1_sansara_events.png"]

    async def test_buttons_wrap_around(self, env):
        member = env.guild.add_member(1)

        _, kwargs = await env.cog.build_card_page("rs", member, 0)
        view = kwargs["view"]

        assert custom_ids(view) == ["karma:card:rs:1:3:prev", "karma:card_label:rs:1", "karma:card:rs:1:1:next"]
        assert view.children[1].label == "Карточка · 1/4"
        assert view.children[1].disabled
        assert view.timeout is None

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

    async def test_shortcut_sends_buttons(self, env):
        member = env.guild.add_member(1)
        message = make_message(member, FakeChannel(3000), env.guild, content=",rs")

        await env.cog.on_message(message)

        assert custom_ids(message.reply.call_args.kwargs["view"])[2] == "karma:card:rs:1:1:next"

    async def test_slash_sends_buttons(self, env):
        member = env.guild.add_member(1)
        interaction = make_interaction(member, env.guild)

        await env.cog.rank_sansara.callback(env.cog, interaction)

        assert "view" in interaction.followup.send.call_args.kwargs

    async def test_turn_page(self, env):
        owner = env.guild.add_member(1)
        stranger = env.guild.add_member(2)  # листать может любой
        interaction = make_interaction(stranger, env.guild)
        interaction.edit_original_response = AsyncMock()

        await env.cog.turn_card_page(interaction, "rs", owner.id, 2)

        interaction.response.defer.assert_awaited_once()
        kwargs = interaction.edit_original_response.call_args.kwargs
        assert kwargs["attachments"][0].filename == "user1_sansara_voice.png"
        assert kwargs["view"].children[1].label == "Где сижу · 3/4"

    async def test_turn_page_of_member_who_left(self, env):
        """Человек ушёл с сервера — карточку всё равно можно листать (берётся через fetch_user)."""
        gone = env.guild.add_member(1)
        env.bot.db_ensure_user(gone)
        env.guild.members.remove(gone)
        env.bot.fetch_user = AsyncMock(return_value=gone)
        interaction = make_interaction(env.guild.add_member(2), env.guild)
        interaction.edit_original_response = AsyncMock()

        await env.cog.turn_card_page(interaction, "rs", 1, 1)

        assert interaction.edit_original_response.call_args.kwargs["attachments"][0].filename == "user1_sansara_text.png"


class TestCardPageButton:
    async def test_from_custom_id(self, env):
        button = env.karma.CardPageButton("rc", 42, 3, "next")
        match = button.__discord_ui_compiled_template__.fullmatch(button.custom_id)

        restored = await env.karma.CardPageButton.from_custom_id(None, button.item, match)

        assert (restored.card, restored.user_id, restored.page) == ("rc", 42, 3)
        assert restored.item.label == "▶"

    async def test_callback_calls_cog(self, env):
        button = env.karma.CardPageButton("rs", 42, 1, "prev")
        env.cog.turn_card_page = AsyncMock()
        interaction = SimpleNamespace(client=SimpleNamespace(get_cog=lambda name: env.cog if name == "KarmaSistem" else None))

        await button.callback(interaction)

        env.cog.turn_card_page.assert_awaited_once_with(interaction, "rs", 42, 1)

    async def test_karma_disabled(self, env):
        button = env.karma.CardPageButton("rs", 42, 1, "prev")
        interaction = SimpleNamespace(
            client=SimpleNamespace(get_cog=lambda name: None),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

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

    def test_events_page(self, env):
        me = add_ozernik(env, 1)
        for n in range(7):
            env.db.add_event_karma(me, 10, f"Ивент {n}")

        page = env.karma.get_events_page(me)

        assert (page["total"], page["count"]) == (70, 7)
        assert len(page["awards"]) == env.karma.EVENT_AWARDS_ON_PAGE

    @pytest.mark.parametrize("awards", [[], [("🎉 Квиз", 150, "23.10.2026", 1), ("x" * 120, 5, "01.01.2026", 2)]])
    def test_events_card_renders_without_database(self, env, monkeypatch, awards):
        page = {"total": sum(a[1] for a in awards), "count": len(awards), "awards": awards}
        monkeypatch.setattr(env.karma._state, "db", None)

        buffer = env.karma.create_events_card(AVATAR, "User", "#C9A0FF", page)

        assert Image.open(BytesIO(buffer.getvalue())).size == (900, 480)


# ---------- «ДРУГИЕ ОБЛИЧЬЯ» -----------

class TestGuisesPage:
    def link(self, env, main_member, *twink_ids):
        main_id = env.bot.db_ensure_user(main_member).id
        for twink_id in twink_ids:
            env.db.link_twink(main_id, twink_id)
        return main_id

    async def test_no_twinks_no_page(self, env):
        member = env.guild.add_member(1)

        _, kwargs = await env.cog.build_card_page("rs", member, 0)

        assert kwargs["view"].children[1].label == "Карточка · 1/4"

    async def test_page_with_twinks(self, env):
        main = env.guild.add_member(1)
        env.guild.add_member(11)
        self.link(env, main, 11)

        file, kwargs = await env.cog.build_card_page("rs", main, 4)

        assert file.filename == "user1_sansara_guises.png"
        assert kwargs["view"].children[1].label == "Другие обличья · 5/5"

    async def test_twink_card_shows_same_pages(self, env):
        """Карточка, открытая по твинку, — та же карточка человека: обличья тоже есть."""
        main = env.guild.add_member(1)
        twink = env.guild.add_member(11)
        self.link(env, main, 11)

        _, kwargs = await env.cog.build_card_page("rs", twink, 0)

        assert kwargs["view"].children[1].label == "Карточка · 1/5"

    async def test_accounts_passed_to_picture(self, env, monkeypatch):
        main = env.guild.add_member(1)
        env.guild.add_member(11)
        gone = env.guild.add_member(12)
        self.link(env, main, 11, 12)
        env.guild.members.remove(gone)  # ушёл с сервера — берётся через fetch_user
        env.bot.fetch_user = AsyncMock(return_value=gone)
        captured = {}

        def fake_create(avatar, username, color, accounts):
            captured["accounts"] = accounts
            return BytesIO(b"png")

        monkeypatch.setattr(env.karma, "create_guises_card", fake_create)

        await env.cog.build_card_page("rs", main, 4)

        accounts = captured["accounts"]
        assert [(a["name"], a["main"]) for a in accounts] == [("User 1", True), ("User 11", False), ("User 12", False)]
        assert accounts[1]["since"] and not accounts[0]["since"]
        assert all(a["avatar"] is not None for a in accounts)

    async def test_unknown_account(self, env, monkeypatch):
        main = env.guild.add_member(1)
        self.link(env, main, 55)  # такого аккаунта Discord не отдаёт
        env.bot.fetch_user = AsyncMock(side_effect=discord.NotFound(AsyncMock(status=404), "Unknown User"))
        captured = {}
        monkeypatch.setattr(env.karma, "create_guises_card",
                            lambda avatar, username, color, accounts: captured.update(accounts=accounts) or BytesIO(b"png"))

        await env.cog.build_card_page("rs", main, 4)

        assert captured["accounts"][1] == {"name": "ID 55", "avatar": None, "main": False, "since": captured["accounts"][1]["since"]}

    @pytest.mark.parametrize("count", [2, 8, 11])
    def test_render_without_database(self, env, monkeypatch, count):
        accounts = [{"name": f"Аккаунт {n} с длинным-длинным именем", "avatar": AVATAR if n % 3 else None,
                     "main": n == 0, "since": "23.10.2026" if n else ""} for n in range(count)]
        monkeypatch.setattr(env.karma._state, "db", None)

        buffer = env.karma.create_guises_card(AVATAR, "User", "#C9A0FF", accounts)

        assert Image.open(BytesIO(buffer.getvalue())).size == (900, 480)


class TestCardOfTwink:
    """
    Карточка, открытая с твинка (или по твинку): ник и аватар — твинка,
    статистика — общая, человека (решение Alium 2026-10-23).
    """

    @pytest.mark.parametrize(("card", "page", "creator"), [
        ("rs", 0, "create_rank_card"),
        ("rs", 1, "create_stats_card"),
        ("rs", 2, "create_stats_card"),
        ("rs", 3, "create_events_card"),
        ("rs", 4, "create_guises_card"),
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

        args = captured["args"]
        if creator in ("create_rank_card", "create_cube_cart"):
            ozernik, avatar, username = args[0], args[1], args[2]
            assert ozernik.id == main_id  # статистика — человека
        else:
            avatar, username = args[0], args[1]
        assert username == "User 11"
        assert avatar.getpixel((110, 110))[:3] == (10, 200, 30)  # аватар твинка
        assert file.filename.startswith("user11_")
