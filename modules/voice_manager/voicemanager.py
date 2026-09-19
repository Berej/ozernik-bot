import asyncio
import discord
from discord.ext import commands

from config import config

VOICE_ID = 1108123669747732481
CATEGORY_ID = 1131880136053100616

# Roles that should be forbidden from joining temporary voice channels
FORBIDDEN_ROLE_IDS = [1234872836691067063, 1168293205624766524]

class VoiceManager(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # mapping: owner_user_id -> channel_id
        self.temp_channels: dict[int, int] = {}

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        prev = before.channel
        new = after.channel

        # Joined a channel from nowhere
        if prev is None and new is not None:
            # If joined VM entry channel — create (or reuse) personal channel
            if new.id == VOICE_ID:
                await self._handle_join_vm(member)
            return

        # Left a channel (went offline / disconnected)
        if prev is not None and new is None:
            await self._maybe_cleanup_channel(prev)
            return

        # Moved between channels (prev != new)
        if prev is not None and new is not None and prev != new:
            # First cleanup the previous channel (if it was a temp)
            await self._maybe_cleanup_channel(prev)

            # If destination is the VM entry channel — create or reuse personal channel
            if new.id == VOICE_ID:
                await self._handle_join_vm(member)

            return

    async def _handle_join_vm(self, member: discord.Member):
        """Create or reuse a personal channel for member and move them into it."""
        guild = member.guild

        # If the user already has a tracked channel that still exists, reuse it
        existing_id = self.temp_channels.get(member.id)
        if existing_id:
            existing_channel = guild.get_channel(existing_id)
            if existing_channel and isinstance(existing_channel, discord.VoiceChannel):
                try:
                    await member.move_to(existing_channel)
                    return
                except Exception as e:
                    # Couldn't move (permissions etc). Fall through to create a new one.
                    print(f"Failed to move {member} to existing channel {existing_channel.id}: {e}")

        # create in category if provided and valid
        category = None
        if CATEGORY_ID:
            category = guild.get_channel(CATEGORY_ID)
            if category is None:
                print(f"Warning: category id {CATEGORY_ID} not found in guild {guild.id}; creating channel at root.")

        # Overwrites:
        # - @everyone: hide / cannot connect by default
        # - owner: can view, connect, and has manage_channels (no explicit move/mute/deafen)
        # - bot: has access and manage_channels
        # - forbidden roles: explicitly denied view and connect
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {}

        # Default: allow everyone to view and connect to voice channels
        overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=True, connect=True)

        # Deny connect/view for configured forbidden roles (if they exist in guild)
        for rid in FORBIDDEN_ROLE_IDS:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=False, connect=False)
            else:
                # If role not found, log a warning but continue
                print(f"Warning: forbidden role id {rid} not found in guild {guild.id}.")

        # Allow owner and bot
        overwrites[member] = discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)

        name = f"{member.display_name}'s Room"

        try:
            temp_channel = await guild.create_voice_channel(
                name=name,
                category=category,
                overwrites=overwrites
            )
        except Exception as e:
            print(f"Failed to create voice channel for {member} in guild {guild.id}: {e}")
            return

        # Save mapping and move member
        self.temp_channels[member.id] = temp_channel.id
        try:
            await member.move_to(temp_channel)
        except Exception as e:
            print(f"Failed to move member {member} into newly created channel {temp_channel.id}: {e}")
            # If move failed, consider deleting the empty channel immediately
            try:
                if len(temp_channel.members) == 0:
                    await temp_channel.delete()
                    self._remove_mapping_by_channel(temp_channel.id)
            except Exception as e2:
                print(f"Failed cleaning up failed-created channel {temp_channel.id}: {e2}")

    async def _maybe_cleanup_channel(self, channel: discord.abc.GuildChannel | None):
        """If channel is one we created and it's empty -> delete it."""
        if channel is None:
            return

        # ensure it's a VoiceChannel
        if not isinstance(channel, discord.VoiceChannel):
            return

        # is it one of ours?
        if channel.id not in self.temp_channels.values():
            return

        # if empty -> delete and clean mapping
        try:
            if len(channel.members) == 0:
                await channel.delete()
                self._remove_mapping_by_channel(channel.id)
        except Exception as e:
            print(f"Failed to cleanup channel {channel.id}: {e}")

    def _remove_mapping_by_channel(self, channel_id: int):
        """Remove mapping entry by channel id."""
        user_id = next((uid for uid, cid in self.temp_channels.items() if cid == channel_id), None)
        if user_id:
            del self.temp_channels[user_id]

async def setup(bot):
    await bot.add_cog(VoiceManager(bot))
