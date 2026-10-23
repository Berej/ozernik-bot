"""
Старый мост Discord ⇄ Telegram — включён временно (решение Alium 2026-10-23), пока Габ дописывает
новый (bridge.py удалён 2026-10-24, код — в истории git). Запускает Telegram-часть и грузит Discord-часть;
при выгрузке модуля всё это останавливает.
"""

import asyncio

from discord.ext import commands

from modules.bridge.cogs._old_config import config
from modules.bridge.cogs.bridge_telegram import start_telegram_bot

DISCORD_PART = "modules.bridge.cogs.bridge_discord"


class BridgeMain(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.telegram = None  # TelegramBridge после запуска

        # запустить телеграм мост в фоне
        self.tg_task = bot.loop.create_task(self.start_telegram_bridge())

        # загрузить discord ког
        bot.loop.create_task(self.load_bridge_discord())

    async def cog_unload(self):
        """
        Выгрузка через /modules_control: остановить Telegram-ботов и Discord-часть.
        Без этого боты продолжали работать, а повторная загрузка запускала вторую пару —
        одно сообщение из Telegram приходило в Discord дважды (кейс 2026-10-24).
        """
        if not self.tg_task.done():
            self.tg_task.cancel()
            try:
                await self.tg_task
            except asyncio.CancelledError:
                pass

        if self.telegram is not None:
            await self.telegram.stop()
            self.telegram = None

        if DISCORD_PART in self.bot.extensions:
            await self.bot.unload_extension(DISCORD_PART)

    async def start_telegram_bridge(self):  # noqa
        """Запуск Telegram моста."""
        try:
            self.telegram = await start_telegram_bot()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Ошибка Telegram моста: {e}")

    async def load_bridge_discord(self):
        """Загрузка кода моста как кога."""
        try:
            await self.bot.load_extension(DISCORD_PART)
        except Exception as e:
            print(f"Ошибка загрузки bridge_discord: {e}")


async def setup(bot):
    missing = config.missing()
    if missing:
        # Без настроек мост молча не работал бы — говорим прямо, чего не хватает.
        print(
            f"[Старый мост] Не запущен: не заданы {', '.join(missing)}. "
            f"См. modules/bridge/cogs/_old_config.py."
        )
        return

    await bot.add_cog(BridgeMain(bot))
