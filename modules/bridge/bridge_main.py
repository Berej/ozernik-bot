from discord.ext import commands

class BridgeMain(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # запустить телеграм мост в фоне
        self.tg_task = bot.loop.create_task(self.start_telegram_bridge())

        # загрузить discord ког
        bot.loop.create_task(self.load_bridge_discord())

    async def start_telegram_bridge(self):  # noqa
        """Запуск Telegram моста."""
        try:
            await start_telegram_bot()
        except Exception as e:
            print(f"Ошибка Telegram моста: {e}")

    async def load_bridge_discord(self):
        """Загрузка кода моста как кога."""
        try:
            await self.bot.load_extension("modules.bridge.cogs.bridge_discord")
        except Exception as e:
            print(f"Ошибка загрузки bridge_discord: {e}")


async def setup(bot):
    # await bot.add_cog(BridgeMain(bot))
    pass
