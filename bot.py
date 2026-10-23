# main.py

import asyncio
import importlib
import pkgutil
import sys
import os
import traceback
from datetime import timedelta, timezone
from typing import Any

import discord
from colorama import Fore, Style, init
from discord.ext import commands
from discord.ext.commands import Context, errors
from discord.ext.commands._types import BotT

from config import config, DISCORD_TOKEN_MAIN

from utilities import Database, DataTypes

init()


GUILD_ID = config.GUILD_ID
MODULES_FOLDER = "modules"

MOSCOW_TZ = timezone(timedelta(hours=3))


class OzernikiBot(commands.Bot):

    # ---------- ИНИЦИАЛИЗАЦИЯ -----------

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
        self.found_modules: set[str] = set()

        self.db = Database()

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

    def discover_modules(self) -> None:
        """
        Находит модули-расширения в пакете modules и добавляет новые в конфигурацию.

        Ищет через систему импорта Python (pkgutil), а не по пути к папке: Python ищет пакет
        там же, откуда потом его грузит load_extension, — найденное и загружаемое совпадают.
        Кейс 2026-10-24: поиск шёл по относительному пути "modules" от папки запуска; под
        systemd это была другая папка (старая копия бота) — карма, РСН и Зал славы «не находились».

        Модуль = файл <папка>/<имя>.py в папке-пакете (с __init__.py). Папки и файлы на «_»,
        вложенные пакеты (bridge/cogs) — не модули.

        Записи о не найденных модулях из config.json НЕ удаляются — load_configured_extensions
        пропускает их с предупреждением: ошибка поиска не должна стирать настройки.
        """
        self.found_modules = set()
        package = importlib.import_module(self.modules_folder)

        for folder in sorted(pkgutil.iter_modules(package.__path__), key=lambda info: info.name):
            if not folder.ispkg or folder.name.startswith("_"):
                continue

            subpackage = importlib.import_module(f"{self.modules_folder}.{folder.name}")

            for module in sorted(pkgutil.iter_modules(subpackage.__path__), key=lambda info: info.name):
                if module.ispkg or module.name.startswith("_"):
                    continue

                module_path = f"{self.modules_folder}.{folder.name}.{module.name}"
                self.found_modules.add(module_path)
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

            if module_path not in self.found_modules:
                print(
                    Fore.YELLOW
                    + f"{index}. [{module_name}] Модуль не найден — пропущен "
                      f"({module_path}; если его удалили, запись можно убрать из config.json)."
                    + Style.RESET_ALL
                )
                continue

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

    # ---------- ЛОГИРОВАНИЕ -----------

    @staticmethod
    def print_error(source, error):
        tb = ''.join(
            traceback.format_exception(
                type(error),
                error,
                error.__traceback__
            )
        )

        print(f"Error in {source}:\n{tb}")

    async def on_error(self, event, *args, **kwargs) -> None:
        error = sys.exc_info()[1]

        self.print_error(event, error)

    # --------- ВСПОМОГАТЕЛЬНО ----------

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