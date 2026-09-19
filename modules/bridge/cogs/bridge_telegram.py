import asyncio, os, re
from telegram import Update, ReactionTypeEmoji
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, MessageReactionHandler, Application # noqa
from config_loader import config
from utilities import storage

first = True
recent = None

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE): # noqa
    global first
    print(update.effective_chat.id)
    if update.effective_chat.id != config.BR_TELEGRAM_CHANNEL_ID:
        return
    first = True

    message = update.message

    user = message.from_user
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(f"{base_dir}/temp", exist_ok=True)

    full_name = message.from_user.full_name
    telegram_user_id = message.from_user.id
    avatar_url = None
    files = []
    telegram_message_id = message.message_id

    mess_text = message.text or message.caption or ''
    mess_text = re.sub(r"@everyone", r"@evеryone", mess_text) # noqa
    mess_text = re.sub(r"@here", r"@hеre", mess_text) # noqa

    if message.reply_to_message:
        replied_name = message.reply_to_message.from_user.full_name
        replied_id = message.reply_to_message.id
    else:
        replied_name = ''
        replied_id = ''

    downloaded_ids = set()

    # Profile photo:
    try:
        photos = await context._bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count > 0:
            file_id = photos.photos[0][0].file_id
            file = await context._bot.get_file(file_id)
            avatar_url = file.file_path
    except Exception as e:
        print(f"Error getting {user.username} avatar: {e}")

    # Files:
    if message.photo:  # Photo
        best_photo = message.photo[-1]
        if best_photo.file_unique_id not in downloaded_ids:
            tg_file = await context._bot.get_file(best_photo.file_id)
            image_path = f"{base_dir}/temp/{tg_file.file_unique_id}.jpg"
            await tg_file.download_to_drive(image_path)
            files.append(image_path)
            downloaded_ids.add(best_photo.file_unique_id)

    if message.video: # Video
        tg_file = await context._bot.get_file(message.video.file_id)
        video_path = f"{base_dir}/temp/{tg_file.file_unique_id}.mp4"
        await tg_file.download_to_drive(video_path)
        files.append(video_path)

    if message.animation: # GIF (animation)
        if message.animation.file_unique_id not in downloaded_ids:
            tg_file = await context._bot.get_file(message.animation.file_id)
            original_ext = os.path.splitext(tg_file.file_path)[1] or ".mp4"
            file_path = f"{base_dir}/temp/{tg_file.file_unique_id}{original_ext}"
            await tg_file.download_to_drive(file_path)
            files.append(file_path)
            downloaded_ids.add(message.animation.file_unique_id)

    if message.sticker:
        if message.sticker.file_unique_id not in downloaded_ids:
            tg_file = await context._bot.get_file(message.sticker.file_id)
            ext = ".tgs" if message.sticker.is_animated else ".webp"
            file_path = f"{base_dir}/temp/{message.sticker.file_unique_id}{ext}"
            await tg_file.download_to_drive(file_path)
            files.append(file_path)
            downloaded_ids.add(message.sticker.file_unique_id)

    if message.document: # Document (any file)
        if message.document.file_unique_id not in downloaded_ids:
            tg_file = await context._bot.get_file(message.document.file_id)
            file_path = f"{base_dir}/temp/{tg_file.file_unique_id}_{message.document.file_name}"
            await tg_file.download_to_drive(file_path)
            files.append(file_path)
            downloaded_ids.add(message.document.file_unique_id)

    if message.voice: # Voice message
        tg_file = await context._bot.get_file(message.voice.file_id)
        voice_path = f"{base_dir}/temp/{tg_file.file_unique_id}.ogg"
        await tg_file.download_to_drive(voice_path)
        files.append(voice_path)

    if message.video_note: # Video circle # noqa
        tg_file = await context._bot.get_file(message.video_note.file_id)
        vnote_path = f"{base_dir}/temp/{tg_file.file_unique_id}.mp4" # noqa
        await tg_file.download_to_drive(vnote_path)
        files.append(vnote_path)

    storage.add_ttd({
        "telegram_user_id":    telegram_user_id,
        "full_name":           full_name,
        "avatar_url":          avatar_url,
        "mess_text":           mess_text,
        "files":               files,
        "telegram_message_id": telegram_message_id,
        "replied_name":        replied_name,
        "replied_id":          replied_id
    })


async def send_tel_message(bot_list):
    global recent
    global first
    index = 0
    while True:
        mess_info = storage.pop_dtt()
        if mess_info:
            user_id         = mess_info["user_id"]
            display_name    = mess_info["display_name"]
            user_name       = mess_info["user_name"]
            mess_id         = mess_info["mess_id"]
            mess_text       = mess_info["mess_text"]
            files           = mess_info["files"]
            replied_name    = mess_info["replied_name"]
            replied_mess_id = mess_info["replied_id"]
            replied_user_id = mess_info["replied_user_id"]
            replied_text    = mess_info["replied_text"]

            replied_tel_id = None
            quote = ''

            if replied_mess_id:
                links = storage.load_link()
                for link in links:
                    if replied_mess_id == link[0]:
                        if replied_user_id == "Webhook":
                            replied_tel_id = link[1]
                        else:
                            quote = f'<blockquote expandable><b><a href="https://t.me/c/{str(config.BR_TELEGRAM_CHANNEL_ID)[-10:]}/{link[1]}"><u>↑ {replied_name}</u></a></b> \n{replied_text}</blockquote>\n'
                        break
            full_title = quote

            if user_id != recent:
                index += 1
                recent = user_id
                first = True
            bot = bot_list[index % len(bot_list)]

            hat = f'<b><a href="https://discord.gg/S39yQzS4Ke">{display_name} — {user_name}</a></b>:\n'
            if first:
                fin_mess = quote + hat + mess_text
                full_title = quote + hat
                first = False
            else:
                fin_mess = quote + mess_text

            try:
                if not files:
                    message = await bot.send_message(chat_id=config.BR_TELEGRAM_CHANNEL_ID, text=fin_mess, parse_mode="HTML", reply_to_message_id=replied_tel_id, disable_web_page_preview=True)
                    storage.add_link([mess_id, message.message_id, full_title, True])
                else:
                    fin_mess = quote + hat + mess_text
                    full_title = quote + hat
                    for i, file in enumerate(files):
                        with open(file, "rb") as f:
                            if file.lower().endswith((".jpg", ".png")):
                                message = await bot.send_photo(chat_id=config.BR_TELEGRAM_CHANNEL_ID, photo=f,
                                                               caption=fin_mess, parse_mode="HTML", reply_to_message_id=replied_tel_id)
                            elif file.lower().endswith(".mp4"):
                                message = await bot.send_video(chat_id=config.BR_TELEGRAM_CHANNEL_ID, video=f,
                                                               caption=fin_mess, parse_mode="HTML", reply_to_message_id=replied_tel_id)
                            elif file.lower().endswith((".gif", ".webp", ".webp2")):
                                message = await bot.send_animation(chat_id=config.BR_TELEGRAM_CHANNEL_ID, animation=f,
                                                                   caption=fin_mess, parse_mode="HTML", reply_to_message_id=replied_tel_id)
                            elif file.lower().endswith((".mp3", ".wav")):
                                message = await bot.send_audio(chat_id=config.BR_TELEGRAM_CHANNEL_ID, voice=f,
                                                               caption=fin_mess, parse_mode="HTML", reply_to_message_id=replied_tel_id)
                            else:
                                message = await bot.send_document(chat_id=config.BR_TELEGRAM_CHANNEL_ID, document=f,
                                                                  caption=fin_mess, parse_mode="HTML", reply_to_message_id=replied_tel_id)
                            if i == 0: storage.add_link([mess_id, message.message_id, full_title, True])
                            else: storage.add_link([mess_id, message.message_id, full_title, False])
                        replied_tel_id = None
                        fin_mess = hat
                        os.remove(file)
            except Exception as e:
                print(f"Send error: {e}")
            await asyncio.sleep(0.2)
        else:
            await asyncio.sleep(1)

async def do_request(bot_list):
    global recent
    while True:
        try:
            request = storage.pop_req('telegram')
            if not request:
                await asyncio.sleep(1)
                continue
            if request['request'] == 'delete':
                for message_id in request['message_id']:
                    for bot in bot_list:
                        try:
                            await bot.delete_message(chat_id=config.BR_TELEGRAM_CHANNEL_ID, message_id=message_id)
                            break
                        except Exception: # noqa
                            continue
                recent = 0
            elif request['request'] == 'edit':
                edited_text = request['title'] + request['new_text']
                for bot in bot_list:
                    try:
                        await bot.edit_message_text(chat_id=config.BR_TELEGRAM_CHANNEL_ID, message_id=request['message_id'],
                                                    text=edited_text, parse_mode="HTML", disable_web_page_preview=True)
                        break
                    except Exception: # noqa
                        try:
                            await bot.edit_message_caption(chat_id=config.BR_TELEGRAM_CHANNEL_ID, message_id=request['message_id'],
                                                           caption=edited_text, parse_mode="HTML")
                            break
                        except Exception: # noqa
                            continue
            elif request['request'] == 'reaction':
                try:
                    for bot in bot_list:
                        try:
                            await bot.set_message_reaction(
                                chat_id=config.BR_TELEGRAM_CHANNEL_ID,
                                message_id=request['message_id'],
                                reaction=[ReactionTypeEmoji(emoji=request['emoji'])],
                                is_big=False
                            )
                            break
                        except Exception: # noqa
                            continue
                except Exception as e:
                    print(f'{request['request']} error: {e}')
            else:
                print(f'Unknown request: {request['request']}')
        except Exception as e:
            print(f"REQUEST ERROR IN bridge_discord.py: {e}")

async def on_message_edit(update: Update, context: ContextTypes.DEFAULT_TYPE): # noqa
    edited = update.edited_message
    if not edited:
        return

    tel_message_id = edited.message_id
    new_text = edited.text or edited.caption

    message_id = None
    reply_hat = None
    links = storage.load_link()
    for link in links:
        if link[1] == tel_message_id and link[3]:
            message_id = link[0]
            reply_hat = link[2]
            break

    storage.add_req({'way':       'discord',
                     'request':   'edit',
                     'message_id': message_id,
                     'new_text':   new_text,
                     'reply_hat':  reply_hat})

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE): # noqa
    reaction = update.message_reaction
    user = reaction.user

    if not reaction.new_reaction:
        return

    if user.id == context._bot.id:
        return

    if update.effective_chat.id != config.BR_TELEGRAM_CHANNEL_ID:
        return

    emoji = None
    for react in reaction.new_reaction:
        if react.emoji:
            emoji = react.emoji
        else:
            return

    if emoji == '🤩':
        emoji = '⭐'
    message_id = None
    links = storage.load_link()
    for link in links:
        if link[1] == reaction.message_id:
            message_id = link[0]

    if not message_id:
        return

    storage.add_req({
        'way':       'discord',
        'request':   'reaction',
        'message_id': message_id,
        'emoji':      emoji
    })

async def start_telegram_bot():
    app1 = ApplicationBuilder().token(config.TELEGRAM_TOKEN_1).connect_timeout(30).read_timeout(30).build()
    app2 = ApplicationBuilder().token(config.TELEGRAM_TOKEN_2).connect_timeout(30).read_timeout(30).build()

    app1.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE, on_message))
    app1.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, on_message_edit))
    app2.add_handler(MessageReactionHandler(handle_reaction))

    try:
        await app1.initialize()
        await app1.start()
        await app1.updater.start_polling()
    except Exception as e:
        print(f'Error launching Telegram bot 2: {e}')
        return

    try:
        await app2.initialize()
        await app2.start()
        await app2.updater.start_polling(allowed_updates=["message", "edited_message", "message_reaction", "handle_reply"])
    except Exception as e:
        print(f'Error launching Telegram bot 1: {e}')
        return

    asyncio.create_task(send_tel_message([app1.bot, app2.bot]))
    asyncio.create_task(do_request([app1.bot, app2.bot]))

