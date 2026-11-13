"""
Личные войсы (решения Alium 2026-11-08): бот помнит в общей базе, чей войс, и сохранённые
настройки; войс удаляется, когда в нём не осталось людей; минуты «кто в чьём войсе» копятся;
плашка *i (модуль stats) показывает все войсы владельца одной строкой «Войс Имя».
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import modules.voice_manager.voicemanager as vm
from conftest import GUILD_ID, FakeMember, FakeRole, make_interaction
from modules.rsn._rsn_levels import ISOLATION_OVERWRITE, MUTE_OVERWRITE


class FakeVoiceChannel:
    def __init__(self, channel_id, name, guild, *, category=None, overwrites=None, user_limit=0):
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.category = category
        self.user_limit = user_limit
        self.members = []
        self._overwrites = dict(overwrites or {})
        self.send = AsyncMock()

        async def delete(reason=None):
            guild.channels.pop(self.id, None)

        async def edit(name=None, user_limit=None, reason=None):
            if name is not None:
                self.name = name
            if user_limit is not None:
                self.user_limit = user_limit

        async def set_permissions(target, *, overwrite=None, reason=None):
            if overwrite is None or overwrite.is_empty():
                self._overwrites.pop(target, None)
            else:
                self._overwrites[target] = overwrite

        self.delete = AsyncMock(side_effect=delete)
        self.edit = AsyncMock(side_effect=edit)
        self.set_permissions = AsyncMock(side_effect=set_permissions)

    @property
    def overwrites(self):
        return {target: discord.PermissionOverwrite.from_pair(*ow.pair()) for target, ow in self._overwrites.items()}

    def overwrites_for(self, target):
        ow = self._overwrites.get(target)
        return discord.PermissionOverwrite.from_pair(*ow.pair()) if ow else discord.PermissionOverwrite()

    def __eq__(self, other):
        return isinstance(other, FakeVoiceChannel) and other.id == self.id

    def __hash__(self):
        return hash(self.id)


def join(member, channel):
    """Участник оказался в войсе (как это видит кэш discord.py)."""
    old = member.voice.channel
    if old is not None and member in old.members:
        old.members.remove(member)
    member.voice.channel = channel
    if channel is not None:
        channel.members.append(member)
    return old


@pytest.fixture
def voice_env(env):
    guild = env.guild
    guild.channels = {}
    guild.get_channel = guild.channels.get
    guild.default_role = FakeRole(GUILD_ID, "@everyone", guild=guild)
    guild.me = FakeMember(999, guild, bot=True)
    created = []
    next_id = iter(range(70_000, 80_000))

    category = SimpleNamespace(id=vm.CATEGORY_ID, name="ЛИЧНЫЕ ВОЙСЫ")
    guild.channels[vm.CATEGORY_ID] = category
    entry = FakeVoiceChannel(vm.VOICE_ID, "➕ Создать войс", guild, category=category)
    guild.channels[entry.id] = entry

    async def create_voice_channel(name, *, category=None, overwrites=None, user_limit=0, reason=None):
        channel = FakeVoiceChannel(next(next_id), name, guild, category=category, overwrites=overwrites, user_limit=user_limit)
        guild.channels[channel.id] = channel
        created.append(channel)
        return channel

    guild.create_voice_channel = AsyncMock(side_effect=create_voice_channel)
    env.bot.get_cog = lambda name: None

    def add_member(member_id, **kwargs):
        member = guild.add_member(member_id, **kwargs)
        member.voice = SimpleNamespace(channel=None, self_deaf=False, deaf=False, self_mute=False)

        async def move_to(channel, reason=None):
            join(member, channel)
            await env_ns.cog.on_voice_state_update(member, SimpleNamespace(channel=entry), SimpleNamespace(channel=channel))

        member.move_to = AsyncMock(side_effect=move_to)
        return member

    env_ns = SimpleNamespace(
        db=env.db, guild=guild, bot=env.bot, entry=entry, category=category, created=created,
        add_member=add_member, karma_env=env,
    )
    env_ns.cog = vm.VoiceManager(env.bot)
    return env_ns


async def enter(voice_env, member, channel):
    """Перейти в канал и отдать событие боту."""
    before = join(member, channel)
    await voice_env.cog.on_voice_state_update(member, SimpleNamespace(channel=before), SimpleNamespace(channel=channel))


def person(voice_env, discord_id):
    return voice_env.db.get_user_by_discord_id(discord_id).id


def age(voice_env, seconds=vm.FRESH_SECONDS + 1):
    """Войсы «созданы» seconds секунд назад — цикл перестаёт считать их свежими."""
    with voice_env.db.transaction():
        voice_env.db.execute("UPDATE personal_voice_channels SET created_at = created_at - ?", (seconds,))


# ---------- СОЗДАНИЕ ----------

class TestCreate:
    async def test_entry_creates_voice_and_remembers_owner(self, voice_env):
        me = voice_env.add_member(1)

        await enter(voice_env, me, voice_env.entry)

        channel = voice_env.created[0]
        assert channel.name == "User 1's Room" and channel.category is voice_env.category
        assert me in channel.members
        assert channel.overwrites_for(me).manage_channels is True
        assert channel.overwrites_for(voice_env.guild.default_role).connect is True
        row = voice_env.db.get_live_personal_voice(person(voice_env, 1))
        assert row["channel_id"] == channel.id and row["category_id"] == vm.CATEGORY_ID

    async def test_second_entry_moves_to_existing(self, voice_env):
        me = voice_env.add_member(1)
        friend = voice_env.add_member(2)
        await enter(voice_env, me, voice_env.entry)
        await enter(voice_env, friend, voice_env.created[0])

        await enter(voice_env, me, voice_env.entry)  # вышел во вход, войс не пуст — друг там

        assert len(voice_env.created) == 1
        assert me in voice_env.created[0].members

    async def test_saved_settings_applied(self, voice_env):
        me = voice_env.add_member(1)
        friend = voice_env.add_member(2)
        owner = voice_env.db.add_user(discord_id=1)
        voice_env.db.save_personal_voice_settings(owner, "Чайная", 5, True, [2, 12345])

        await enter(voice_env, me, voice_env.entry)

        channel = voice_env.created[0]
        assert (channel.name, channel.user_limit) == ("Чайная", 5)
        assert channel.overwrites_for(voice_env.guild.default_role).connect is False
        assert channel.overwrites_for(friend).connect is True  # допущен; 12345 нет на сервере — пропущен

    async def test_twink_opens_voice_of_main(self, voice_env):
        main = voice_env.add_member(1)
        twink = voice_env.add_member(11)
        owner = voice_env.db.add_user(discord_id=1)
        voice_env.db.link_twink(owner, 11)

        await enter(voice_env, twink, voice_env.entry)

        channel = voice_env.created[0]
        assert voice_env.db.get_personal_voice(channel.id)["user_id"] == owner
        assert channel.overwrites_for(twink).manage_channels is True
        assert channel.overwrites_for(main).connect is True  # другой аккаунт — зайти может и в закрытый

    async def test_rsn_roles_get_rsn_overwrites(self, voice_env):
        old_mute = FakeRole(vm.FORBIDDEN_ROLE_IDS[1], "Мут", guild=voice_env.guild)
        isolation = FakeRole(778, "Изоляция", guild=voice_env.guild)
        voice_env.guild.roles.update({old_mute.id: old_mute, isolation.id: isolation})
        rsn = SimpleNamespace(_role=lambda key: {"mute_role_id": old_mute, "isolation_role_id": isolation}[key])
        voice_env.bot.get_cog = lambda name: rsn if name == "RsnCog" else None

        await enter(voice_env, voice_env.add_member(1), voice_env.entry)

        channel = voice_env.created[0]
        # «Мут» РСН 2.0 видит каналы, но не заходит — запрет РСН перекрыл старый «не видит»
        assert channel.overwrites_for(old_mute) == MUTE_OVERWRITE
        assert channel.overwrites_for(isolation) == ISOLATION_OVERWRITE

    async def test_members_get_connect_only_not_view(self, voice_env):
        """
        Личное разрешение сильнее запрета роли: с view_channel «Изоляция» допущенного увидела бы войс.
        Исключение — владелец (кейс 2026-11-14): см. test_owner_with_forbidden_role_sees_own_voice.
        """
        me = voice_env.add_member(1)
        voice_env.add_member(2)
        owner = voice_env.db.add_user(discord_id=1)
        voice_env.db.save_personal_voice_settings(owner, None, 0, True, [2])
        voice_env.db.link_twink(owner, 11)
        voice_env.add_member(11)

        await enter(voice_env, me, voice_env.entry)

        channel = voice_env.created[0]
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, FakeMember) and target.id not in (voice_env.guild.me.id, me.id):
                assert overwrite.view_channel is None, target
        assert channel.overwrites_for(me).view_channel is True

    async def test_owner_with_forbidden_role_sees_own_voice(self, voice_env):
        """
        Кейс 2026-11-14 (Бердж): у владельца роль из FORBIDDEN_ROLE_IDS (запрет видеть) — без личного
        view_channel он не видел бы собственный войс, Discord выкидывал бы его, и войс сразу «пустел».
        Личное разрешение сильнее запрета роли — владелец видит свой войс.
        """
        forbidden = FakeRole(vm.FORBIDDEN_ROLE_IDS[0], "Полный мут", guild=voice_env.guild)
        voice_env.guild.roles[forbidden.id] = forbidden
        me = voice_env.add_member(1, roles=[forbidden])

        await enter(voice_env, me, voice_env.entry)

        channel = voice_env.created[0]
        assert channel.overwrites_for(forbidden).view_channel is False
        assert channel.overwrites_for(me).view_channel is True and channel.overwrites_for(me).connect is True

    async def test_punished_member_kicked_from_personal_voice(self, voice_env):
        mute_role = FakeRole(777, "Мут", guild=voice_env.guild)
        rsn = SimpleNamespace(_role=lambda key: mute_role if key == "mute_role_id" else None)
        voice_env.bot.get_cog = lambda name: rsn if name == "RsnCog" else None
        me = voice_env.add_member(1)
        await enter(voice_env, me, voice_env.entry)
        muted = voice_env.add_member(2, roles=[mute_role])

        await enter(voice_env, muted, voice_env.created[0])

        muted.move_to.assert_awaited_once()
        assert muted.move_to.call_args.args[0] is None
        assert muted not in voice_env.created[0].members

    async def test_bot_in_entry_ignored(self, voice_env):
        await enter(voice_env, voice_env.add_member(50, bot=True), voice_env.entry)

        assert voice_env.created == []


# ---------- УДАЛЕНИЕ ----------

class TestCleanup:
    async def test_last_human_leaves_voice_deleted(self, voice_env):
        me = voice_env.add_member(1)
        music = voice_env.add_member(60, bot=True)
        await enter(voice_env, me, voice_env.entry)
        channel = voice_env.created[0]
        join(music, channel)

        await enter(voice_env, me, None)

        channel.delete.assert_awaited_once()  # музыкальный бот войс не держит
        # Причина в журнале аудита — по ней видно, какая проверка удалила войс
        assert channel.delete.call_args.kwargs["reason"] == "Личный войс опустел: вышел последний"
        assert voice_env.db.get_personal_voice(channel.id)["deleted_at"] is not None
        assert voice_env.db.get_live_personal_voice(person(voice_env, 1)) is None

    async def test_not_personal_channel_untouched(self, voice_env):
        me = voice_env.add_member(1)
        common = FakeVoiceChannel(5555, "Общий", voice_env.guild)
        voice_env.guild.channels[common.id] = common
        await enter(voice_env, me, common)

        await enter(voice_env, me, None)

        common.delete.assert_not_awaited()

    async def test_after_restart_tick_deletes_empty_and_forgets_missing(self, voice_env):
        me = voice_env.add_member(1)
        await enter(voice_env, me, voice_env.entry)
        channel = voice_env.created[0]
        join(me, None)  # вышел, пока бот лежал: события не было
        owner = person(voice_env, 1)
        voice_env.db.add_personal_voice(424242, owner, vm.CATEGORY_ID)  # удалён руками, пока бот лежал
        age(voice_env)

        restarted = vm.VoiceManager(voice_env.bot)  # память пуста — всё из базы
        await restarted.voice_tick()

        channel.delete.assert_awaited_once()
        assert voice_env.db.get_live_personal_voices() == []

    async def test_fresh_voice_survives_lagging_cache(self, voice_env):
        """
        Кейс 2026-11-14 (Бердж): move_to вернулся, а событие «владелец в войсе» ещё не пришло —
        по кэшу войс пуст. Цикл в этот момент не должен удалять свежий войс.
        """
        me = voice_env.add_member(1)
        me.move_to = AsyncMock()  # перенос принят Discord, но кэш (channel.members) ещё не знает
        join(me, voice_env.entry)
        await voice_env.cog.on_voice_state_update(me, SimpleNamespace(channel=None), SimpleNamespace(channel=voice_env.entry))
        channel = voice_env.created[0]
        assert channel.members == []

        await voice_env.cog.voice_tick()

        channel.delete.assert_not_awaited()
        assert voice_env.db.get_personal_voice(channel.id)["deleted_at"] is None

        # Прошло больше FRESH_SECONDS, а людей так и нет — это уже правда пустой войс.
        age(voice_env)
        await voice_env.cog.voice_tick()

        channel.delete.assert_awaited_once()
        assert channel.delete.call_args.kwargs["reason"] == "Личный войс опустел (проверка раз в минуту)"

    async def test_fresh_voice_not_in_cache_not_marked_deleted(self, voice_env):
        """Канал только что создан, событие «канал создан» ещё не обработано — не считать его удалённым."""
        owner = voice_env.db.add_user(discord_id=1)
        voice_env.db.add_personal_voice(55555, owner, vm.CATEGORY_ID)  # в кэше сервера его пока нет

        await voice_env.cog.voice_tick()

        assert voice_env.db.get_personal_voice(55555)["deleted_at"] is None

    async def test_waits_for_channel_in_cache_before_move(self, voice_env):
        """Перенос — только когда канал в кэше: иначе discord.py решит, что человек вышел из голоса."""
        order = []
        create = voice_env.guild.create_voice_channel.side_effect

        async def create_not_cached(*args, **kwargs):
            channel = await create(*args, **kwargs)
            del voice_env.guild.channels[channel.id]  # как в discord.py: в кэш — только с событием
            return channel

        async def wait_for(event, check, timeout):
            channel = voice_env.created[-1]
            assert event == "guild_channel_create" and check(channel) and timeout == vm.CACHE_WAIT_SECONDS
            voice_env.guild.channels[channel.id] = channel  # пришло событие «канал создан»
            order.append("в кэше")
            return channel

        voice_env.guild.create_voice_channel.side_effect = create_not_cached
        voice_env.bot.wait_for = AsyncMock(side_effect=wait_for)
        me = voice_env.add_member(1)
        move = me.move_to.side_effect

        async def logged_move(channel, reason=None):
            order.append("перенос")
            await move(channel, reason)

        me.move_to.side_effect = logged_move

        await enter(voice_env, me, voice_env.entry)

        assert order == ["в кэше", "перенос"]
        assert me in voice_env.created[0].members

    async def test_failed_move_deletes_new_voice(self, voice_env):
        me = voice_env.add_member(1)
        me.move_to = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=400), "Target user is not connected to voice."))
        join(me, voice_env.entry)

        await voice_env.cog.on_voice_state_update(me, SimpleNamespace(channel=None), SimpleNamespace(channel=voice_env.entry))

        channel = voice_env.created[0]
        channel.delete.assert_awaited_once()
        assert channel.delete.call_args.kwargs["reason"] == "Личный войс: не удалось перенести владельца"
        assert voice_env.db.get_personal_voice(channel.id)["deleted_at"] is not None

    async def test_deleted_by_hand_marked(self, voice_env):
        me = voice_env.add_member(1)
        await enter(voice_env, me, voice_env.entry)
        channel = voice_env.created[0]

        await voice_env.cog.on_guild_channel_delete(channel)

        assert voice_env.db.get_personal_voice(channel.id)["deleted_at"] is not None

    async def test_tick_error_does_not_stop_loop(self, voice_env, monkeypatch, capsys):
        monkeypatch.setattr(voice_env.cog, "_tick", AsyncMock(side_effect=RuntimeError("сбой")))

        await voice_env.cog.voice_tick()  # не бросает

        assert "сбой" in capsys.readouterr().out


# ---------- МИНУТЫ ----------

class TestMinutes:
    async def test_minutes_per_owner_and_visitor(self, voice_env):
        me = voice_env.add_member(1)
        friend = voice_env.add_member(2)
        music = voice_env.add_member(60, bot=True)
        await enter(voice_env, me, voice_env.entry)
        channel = voice_env.created[0]
        join(friend, channel)
        join(music, channel)

        await voice_env.cog.voice_tick()
        await voice_env.cog.voice_tick()

        owner = person(voice_env, 1)
        assert dict(voice_env.db.get_personal_voice_visitors(owner)) == {owner: 2, person(voice_env, 2): 2}
        assert voice_env.db.get_personal_voice_hosts(person(voice_env, 2)) == [(owner, 2)]

    async def test_twink_minutes_go_to_main(self, voice_env):
        host = voice_env.add_member(1)
        main_id = voice_env.db.add_user(discord_id=2)
        voice_env.db.link_twink(main_id, 22)
        twink = voice_env.add_member(22)
        await enter(voice_env, host, voice_env.entry)
        join(twink, voice_env.created[0])

        await voice_env.cog.voice_tick()

        assert voice_env.db.get_personal_voice_hosts(main_id) == [(person(voice_env, 1), 1)]


# ---------- КОМАНДЫ ----------

async def run(voice_env, command, user, *args):
    interaction = make_interaction(user, voice_env.guild)
    await command.callback(voice_env.cog, interaction, *args)
    return interaction


def reply(interaction) -> str:
    call = interaction.followup.send.call_args or interaction.response.send_message.call_args
    return call.args[0]


class TestCommands:
    async def open(self, voice_env, member_id=1):
        member = voice_env.add_member(member_id)
        await enter(voice_env, member, voice_env.entry)
        return member, voice_env.created[-1]

    async def test_no_voice(self, voice_env):
        interaction = await run(voice_env, voice_env.cog.voice_limit, voice_env.add_member(1), 3)

        assert reply(interaction) == vm.NO_VOICE_TEXT

    async def test_rename_and_discord_rename_limit(self, voice_env):
        me, channel = await self.open(voice_env)

        await run(voice_env, voice_env.cog.voice_name, me, "Чайная")
        await run(voice_env, voice_env.cog.voice_name, me, "Кухня")
        third = await run(voice_env, voice_env.cog.voice_name, me, "Подвал")

        assert channel.name == "Кухня"
        assert "2 раза за 10 минут" in reply(third)
        assert channel.edit.await_count == 2

    async def test_rename_waits_too_long(self, voice_env, monkeypatch):
        """Discord сам не пускает (переименовали в клиенте) — discord.py ждёт молча; мы не ждём дольше EDIT_TIMEOUT."""
        me, channel = await self.open(voice_env)
        monkeypatch.setattr(vm, "EDIT_TIMEOUT", 0.01)

        async def slow_edit(**kwargs):
            import asyncio
            await asyncio.sleep(1)

        channel.edit.side_effect = slow_edit

        interaction = await run(voice_env, voice_env.cog.voice_name, me, "Чайная")

        assert "Попробуйте позже" in reply(interaction)

    async def test_limit(self, voice_env):
        me, channel = await self.open(voice_env)

        await run(voice_env, voice_env.cog.voice_limit, me, 4)

        assert channel.user_limit == 4

    async def test_lock_lets_in_those_inside(self, voice_env):
        me, channel = await self.open(voice_env)
        friend = voice_env.add_member(2)
        join(friend, channel)

        await run(voice_env, voice_env.cog.voice_lock, me)

        assert channel.overwrites_for(voice_env.guild.default_role).connect is False
        assert channel.overwrites_for(voice_env.guild.default_role).view_channel is True
        assert channel.overwrites_for(friend).connect is True

        await run(voice_env, voice_env.cog.voice_unlock, me)

        assert channel.overwrites_for(voice_env.guild.default_role).connect is True

    async def test_save_and_reset(self, voice_env):
        me, channel = await self.open(voice_env)
        friend = voice_env.add_member(2)
        join(friend, channel)
        await run(voice_env, voice_env.cog.voice_lock, me)
        await channel.edit(name="Чайная", user_limit=3)  # поменял в самом Discord

        interaction = await run(voice_env, voice_env.cog.voice_save, me)

        settings = voice_env.db.get_personal_voice_settings(person(voice_env, 1))
        assert (settings["name"], settings["user_limit"], settings["locked"], settings["allowed"]) == ("Чайная", 3, True, [2])
        assert "Чайная" in reply(interaction)

        await run(voice_env, voice_env.cog.voice_reset, me)

        assert voice_env.db.get_personal_voice_settings(person(voice_env, 1)) is None

    async def test_default_name_saved_as_default(self, voice_env):
        me, _ = await self.open(voice_env)

        await run(voice_env, voice_env.cog.voice_save, me)

        assert voice_env.db.get_personal_voice_settings(person(voice_env, 1))["name"] is None

    async def test_next_voice_after_save(self, voice_env):
        me, channel = await self.open(voice_env)
        await run(voice_env, voice_env.cog.voice_limit, me, 2)
        await run(voice_env, voice_env.cog.voice_save, me)
        await enter(voice_env, me, None)

        await enter(voice_env, me, voice_env.entry)

        assert len(voice_env.created) == 2 and voice_env.created[1].user_limit == 2

    async def test_transfer(self, voice_env):
        me, channel = await self.open(voice_env)
        friend = voice_env.add_member(2)
        join(friend, channel)

        await run(voice_env, voice_env.cog.voice_transfer, me, friend)

        assert voice_env.db.get_personal_voice(channel.id)["user_id"] == person(voice_env, 2)
        assert channel.overwrites_for(friend).manage_channels is True
        assert channel.overwrites_for(me).manage_channels is None
        assert channel.overwrites_for(me).connect is True
        assert "управляет <@2>" in channel.send.call_args.args[0]

    async def test_transfer_needs_member_inside(self, voice_env):
        me, _ = await self.open(voice_env)
        stranger = voice_env.add_member(2)

        interaction = await run(voice_env, voice_env.cog.voice_transfer, me, stranger)

        assert "не в вашем войсе" in reply(interaction)

    async def test_claim_when_owner_left(self, voice_env):
        me, channel = await self.open(voice_env)
        friend = voice_env.add_member(2)
        join(friend, channel)

        refused = await run(voice_env, voice_env.cog.voice_claim, friend)
        assert "ещё здесь" in reply(refused)

        join(me, None)
        await run(voice_env, voice_env.cog.voice_claim, friend)

        assert voice_env.db.get_personal_voice(channel.id)["user_id"] == person(voice_env, 2)
        assert channel.overwrites_for(friend).manage_channels is True


# ---------- БАЗА: ТВИНКИ ----------

class TestTwinkMerge:
    def test_link_twink_moves_voice_data(self, voice_env):
        db = voice_env.db
        main = db.add_user(discord_id=1)
        twink = db.add_user(discord_id=11)
        friend = db.add_user(discord_id=2)
        db.save_personal_voice_settings(twink, "Твинкова", 0, False, [])
        db.add_personal_voice(5001, twink, None)
        db.add_personal_voice_minutes_many([(twink, friend, 5), (friend, twink, 3), (friend, main, 4), (main, twink, 7)])

        db.link_twink(main, 11)

        assert db.get_personal_voice_settings(main)["name"] == "Твинкова"  # своих не было
        assert db.get_personal_voice(5001)["user_id"] == main
        assert dict(db.get_personal_voice_visitors(main)) == {friend: 5, main: 7}
        assert dict(db.get_personal_voice_hosts(main)) == {friend: 3 + 4, main: 7}
