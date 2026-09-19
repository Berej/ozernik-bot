import os, re, html, discord, asyncio, random
from discord.ext import commands, tasks

from config_loader import config
from utilities import storage



class BridgeDiscord(commands.Cog):
    """Discord is part of the Telegram ⇄ Discord bridge."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.send_dis_message.start()
        self.do_request.start()

    # -------------------------
    #        ON MESSAGE
    # -------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user:
            return
        if message.webhook_id is not None:
            return
        if message.channel.id != config.BR_DISCORD_CHANNEL_ID:
            return

        base_dir = os.path.dirname(os.path.abspath(__file__))
        temp_dir = os.path.join(base_dir, "temp")
        os.makedirs(temp_dir, exist_ok=True)

        user_id = message.author.id
        display_name = re.sub(r'[\x00-\x1F\x7F<>[\]𓆩𓆪]', '', message.author.display_name)
        user_name = message.author.name

        files = []
        mess_id = message.id
        content = message.content

        content = re.sub(r"<a?:([a-zA-Z0-9_]+):\d+>", r":\1:", content)

        for user in message.mentions:
            content = content.replace(f"<@{user.id}>", f"@{user.name}")
            content = content.replace(f"<@!{user.id}>", f"@{user.name}")

        for channel in message.channel_mentions: # noqa
            content = content.replace(f"<#{channel.id}>", f"#{channel.name}")



        mess_text = html.escape(content)

        replied_name = replied_text = replied_id = replied_user_id = ""

        if message.reference and message.reference.message_id:
            channel = message.channel
            replied_message = await channel.fetch_message(message.reference.message_id)

            replied_name = replied_message.author.name
            replied_text = replied_message.content
            replied_id = replied_message.id
            replied_user_id = replied_message.author.id
            for user in replied_message.mentions:
                replied_text = replied_text.replace(f"<@{user.id}>", f"@\u200B{user.name}")
                replied_text = replied_text.replace(f"<@!{user.id}>", f"@\u200B{user.name}")

            for channel in replied_message.channel_mentions: # noqa
                replied_text = replied_text.replace(f"<#{channel.id}>", f"#{channel.name}")

            replied_text = html.escape(replied_text)

            if not replied_text:
                for attachment in replied_message.attachments:
                    if attachment != replied_message.attachments[0]:
                        replied_text += '\n'
                    replied_text += f'<i>{attachment.filename}</i>'

            if replied_message.webhook_id is not None:
                replied_user_id = "Webhook"

        if message.attachments:
            index_ = 0
            for attachment in message.attachments:
                name, ext = os.path.splitext(attachment.filename)
                unique_name = f"{name}_{index_}{ext}"
                file_path = os.path.join(temp_dir, unique_name)

                await attachment.save(file_path) # noqa
                files.append(file_path)
                index_ += 1

        if message.stickers:
            sticker = message.stickers[0]
            file_path = os.path.join(temp_dir, f"{sticker.name}{sticker.format}")
            await sticker.save(file_path)
            files.append(file_path)

        storage.add_dtt({
            "user_id":         user_id,
            "user_name":       user_name,
            "display_name":    display_name[:64],
            "mess_id":         mess_id,
            "mess_text":       mess_text,
            "files":           files,
            "replied_id":      replied_id,
            "replied_user_id": replied_user_id,
            "replied_name":    replied_name,
            "replied_text":    replied_text
        })

    # -------------------------
    #    ON MESSAGE EDIT
    # -------------------------
    @commands.Cog.listener()
    async def on_message_edit(self, before, after): # noqa
        if after.author == self.bot.user:
            return
        if after.channel.id != config.BR_DISCORD_CHANNEL_ID:
            return

        new_text = after.content
        for user in after.mentions:
            new_text = new_text.replace(f"<@{user.id}>", f"@{user.name}")
            new_text = new_text.replace(f"<@!{user.id}>", f"@{user.name}")

        for channel in after.channel_mentions:
            new_text = new_text.replace(f"<#{channel.id}>", f"#{channel.name}")
        new_text = html.escape(new_text)

        title = ""
        message_id = None
        links = storage.load_link()

        for link in links:
            if link[0] == after.id and link[3]:
                title = link[2]
                message_id = link[1]
                break

        storage.add_req({
            "way":        "telegram",
            "request":    "edit",
            "message_id": message_id,
            "new_text":   new_text,
            "title":      title
        })

    # -------------------------
    #    ON MESSAGE DELETE
    # -------------------------
    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.author == self.bot.user:
            return
        if message.channel.id != config.BR_DISCORD_CHANNEL_ID:
            return

        message_id = []
        links = storage.load_link()

        for link in links:
            if link[0] == message.id:
                message_id.append(link[1])

        storage.add_req({
            "way":        "telegram",
            "request":    "delete",
            "message_id": message_id
        })

    # -------------------------
    #    ON REACTION
    # -------------------------
    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user == self.bot.user:
            return
        if reaction.message.channel.id != config.BR_DISCORD_CHANNEL_ID:
            return

        emoji = reaction.emoji
        if not isinstance(emoji, str):
            return

        message_id = None
        links = storage.load_link()

        for link in links:
            if link[0] == reaction.message.id:
                message_id = link[1]

        if not message_id:
            return
        if emoji == '⭐':
            emoji = '🤩'
        storage.add_req({
            "way":        "telegram",
            "request":    "reaction",
            "message_id": message_id,
            "emoji":      emoji
        })

    # -------------------------
    #    TASK: SEND DISCORD
    # -------------------------
    @tasks.loop(seconds=0.5)
    async def send_dis_message(self):
        mess_info = storage.pop_ttd()
        if not mess_info:
            return

        channel = self.bot.get_channel(config.BR_DISCORD_CHANNEL_ID)
        if not channel:
            return

        telegram_user_id = mess_info["telegram_user_id"]
        full_name = mess_info["full_name"]
        mess_text = mess_info["mess_text"]
        avatar_url = mess_info["avatar_url"]
        files = mess_info["files"]
        telegram_message_id = mess_info["telegram_message_id"]
        replied_name = mess_info["replied_name"]
        replied_id = mess_info["replied_id"]
        try:
            webhooks = await channel.webhooks()
            webhook = next((wh for wh in webhooks if wh.name == "RelayWebhook"), None)
            if webhook is None:
                webhook = await channel.create_webhook(name="RelayWebhook")

            discord_files = []
            if files:
                for path in files:
                    if os.path.exists(path):
                        discord_files.append(discord.File(path))

            reply = ''
            links = storage.load_link()
            for link in links:
                if link[1] == replied_id:
                    replied_message = await channel.fetch_message(link[0])
                    replied_content = replied_message.content.replace('\n', ' ').replace('<', '<\\')
                    if len(replied_message.content) < 30:
                        reply_text = f'[{replied_content}](https://discord.com/channels/{config.GUILD}/{config.BR_DISCORD_CHANNEL_ID}/{link[0]})'
                    else:
                        reply_text = f'[{replied_content[:30]}...](https://discord.com/channels/{config.GUILD}/{config.BR_DISCORD_CHANNEL_ID}/{link[0]})'
                    if not replied_content:
                        reply_text = f'https://discord.com/channels/{config.GUILD}/{config.BR_DISCORD_CHANNEL_ID}/{link[0]}'
                    if replied_name == 'Lily':
                        reply_ping = f'<@{replied_message.author.id}>'
                    else:
                        reply_ping = f'**{replied_name}**'

                    reply = f'-# ┏  {reply_ping}╺╸**{reply_text}**\n'
                    break

            final_text = reply + mess_text

            if not avatar_url:
                avatar_url = random_avatar(telegram_user_id)

            message = await webhook.send(
                content=final_text,
                username=full_name,
                avatar_url=avatar_url,
                files=discord_files,
                wait=True
            )

            storage.add_link([message.id, telegram_message_id, reply, True])

            for path in files:
                if os.path.exists(path):
                    os.remove(path)

        except Exception as e:
            print(f"Ошибка отправки в Discord: {e}")

    # -------------------------
    #    TASK: REQUESTS
    # -------------------------
    @tasks.loop(seconds=1)
    async def do_request(self):
        request = storage.pop_req("discord")
        if not request:
            return

        channel = self.bot.get_channel(config.BR_DISCORD_CHANNEL_ID)
        if not channel:
            return

        if request["request"] == "edit":
            try:
                webhooks = await channel.webhooks()
                webhook = next((wh for wh in webhooks if wh.name == "RelayWebhook"), None)
                if webhook is None:
                    webhook = await channel.create_webhook(name="RelayWebhook")
                await asyncio.sleep(0.5)
                content = request['reply_hat'] + request['new_text']
                await webhook.edit_message(
                    message_id=request['message_id'],
                    content=content
                )
            except Exception as e:
                print(f"Ошибка при редактировании сообщения {request['message_id']}: {e}")
        elif request["request"] == "reaction":
            try:
                message = await channel.fetch_message(request["message_id"])
                emoji = request["emoji"]
                # Add a variation selector if it is missing, to avoid a second reaction block.
                if not emoji.endswith("\ufe0f"):
                    emoji += "\ufe0f"
                try:
                    await message.add_reaction(emoji)
                except Exception: # noqa
                    await message.add_reaction(request["emoji"])
            except Exception as e:
                print(f"Ошибка реакции: {e}")


async def setup(bot):
    # await bot.add_cog(BridgeDiscord(bot))
    pass

class DefaultAvatar:
    _avatars = {
        '1': "https://cdn.discordapp.com/attachments/1108123699309195365/1445181563368378449/Red.png",
        '2': "https://cdn.discordapp.com/attachments/1108123699309195365/1445181563733151835/White.png",
        '3': "https://cdn.discordapp.com/attachments/1108123699309195365/1445181564085600512/Black.png",
        '4': "https://cdn.discordapp.com/attachments/1108123699309195365/1445181564542783690/Blue.png",
        '5': "https://cdn.discordapp.com/attachments/1108123699309195365/1445181564941111296/Brown.png",
        '6': "https://cdn.discordapp.com/attachments/1108123699309195365/1445181565406543984/Cyan.png",
        '7': "https://cdn.discordapp.com/attachments/1108123699309195365/1445181565796745277/Gray.png",
        '8': "https://cdn.discordapp.com/attachments/1108123699309195365/1445181566233088060/Orange.png",
    }

    def __getattr__(self, name: str):
        if name in self._avatars:
            return self._avatars[name]
        raise AttributeError(f"Цвет '{name}' не существует")

avatar = DefaultAvatar()

def random_avatar(user_id: int):
    r = random.Random(user_id)
    avatar_id = r.randint(1, 8)
    return getattr(avatar, str(avatar_id))
