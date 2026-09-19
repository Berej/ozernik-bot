# main.py

import asyncio
import os
import traceback
from datetime import timedelta, timezone

import discord
from colorama import Fore, Style, init
from discord.ext import commands

from config import config, DISCORD_TOKEN_MAIN

from utilities import Database, DataTypes

init()


GUILD_ID = config.GUILD_ID
MODULES_FOLDER = "modules"

MOSCOW_TZ = timezone(timedelta(hours=3))


class OzernikiBot(commands.Bot):
    def __init__(self) -> None:
        self.ready = False
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=".",
            intents=intents,
        )

        self.moscow_tz = MOSCOW_TZ
        self.modules_folder = MODULES_FOLDER

        self.db = Database()

    def discover_modules(self) -> None:
        """
        Находит Python-модули в папке modules
        и добавляет их в конфигурацию.
        """
        for folder in sorted(os.listdir(self.modules_folder)):
            folder_path = os.path.join(self.modules_folder, folder)

            if not os.path.isdir(folder_path):
                continue

            if folder.startswith("_"):
                continue

            for filename in sorted(os.listdir(folder_path)):
                if not filename.endswith(".py"):
                    continue

                if filename.startswith("_"):
                    continue

                module_path = (
                    f"{self.modules_folder}."
                    f"{folder}."
                    f"{filename[:-3]}"
                )

                config.module_add(module_path)

    async def load_configured_extensions(self) -> None:
        """
        Загружает включённые расширения из config.MODULES.
        """
        for index, (module_path, loaded) in enumerate(
            config.MODULES.items(),
            start=1,
        ):
            module_name = module_path.rsplit(".", maxsplit=1)[-1]

            if not loaded:
                print(
                    Fore.YELLOW
                    + f"{index}. [{module_name}] Cog is disabled."
                    + Style.RESET_ALL
                )
                continue

            try:
                await self.load_extension(module_path)

            except Exception:
                tb = traceback.format_exc()

                print(
                    Fore.RED
                    + f'{index}. [{module_name}] Error loading Cog:\n{tb}'
                    + Style.RESET_ALL
                )

            else:
                print(
                    Fore.GREEN
                    + f"{index}. [{module_name}] Cog loaded successfully!"
                    + Style.RESET_ALL
                )

    async def setup_hook(self) -> None:
        """
        Выполняется один раз при запуске бота,
        до события on_ready.
        """
        self.discover_modules()
        await self.load_configured_extensions()

        guild = discord.Object(id=GUILD_ID)

        # Копирует глобально объявленные команды
        # в конкретный сервер для быстрой синхронизации.
        self.tree.copy_global_to(guild=guild)

        await self.tree.sync(guild=guild)

        print(f"Slash-команды синхронизированы для сервера {GUILD_ID}.")

    async def close(self) -> None:
        """
        Закрывает ресурсы перед остановкой бота.
        """
        # Например:
        # self.database.close_()

        await super().close()

    def db_ensure_user(self, user: discord.User) -> DataTypes.Ozernik | None:
        ozernik = self.db.get_user_by_discord_id(user.id)
        if ozernik is None:
            ozernik_id = self.db.add_user(discord_id=user.id)
            ozernik = self.db.get_user(ozernik_id)
        return ozernik

    @property
    def unix_time(self) -> int:
        return time.time()

    @property
    def guild(self):
        return self.get_guild(GUILD_ID)

bot = OzernikiBot()


@bot.event
async def on_ready() -> None:
    """
    Может выполняться повторно после переподключения.
    """
    print(f"Login: {bot.user}")
    print(f"Timezone setup: {bot.moscow_tz}")
    print("Ready!")
    bot.ready = True


async def main() -> None:
    async with bot:
        await bot.start(DISCORD_TOKEN_MAIN)


if __name__ == "__main__":
    asyncio.run(main())