import os
from pathlib import Path
from getpass import getpass
import json
import sys
from typing import Any, Dict
from copy import deepcopy
from dotenv import load_dotenv
from utilities import DataWorker

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "config.json"


def create_env_if_missing():
    if ENV_PATH.exists():
        return

    print("Initial setup. Enter tokens.")

    discord_token_main = input("Discord token: ")
    telegram_token_main = input("Telegram main token: ")
    telegram_token_repeater_1 = input("Telegram repeater 1 token: ")
    telegram_token_repeater_2  = input("Telegram repeater 2 token: ")

    ENV_PATH.write_text(
        f"DISCORD_TOKEN_MAIN={discord_token_main}\n"
        f"TELEGRAM_TOKEN_MAIN={telegram_token_main}\n"
        f"TELEGRAM_TOKEN_REPEATER_1={telegram_token_repeater_1}\n"
        f"TELEGRAM_TOKEN_REPEATER_2={telegram_token_repeater_2}\n",
        encoding="utf-8"
    )

    print(".env created.")


create_env_if_missing()
load_dotenv(ENV_PATH)

DISCORD_TOKEN_MAIN = os.getenv("DISCORD_TOKEN_MAIN")
TELEGRAM_TOKEN_MAIN = os.getenv("TELEGRAM_TOKEN_MAIN")
TELEGRAM_TOKEN_REPEATER_1 = os.getenv("TELEGRAM_TOKEN_REPEATER_1")
TELEGRAM_TOKEN_REPEATER_2 = os.getenv("TELEGRAM_TOKEN_REPEATER_2")

if not DISCORD_TOKEN_MAIN:
    raise RuntimeError("DISCORD_TOKEN_MAIN not found in .env")

if not TELEGRAM_TOKEN_MAIN:
    raise RuntimeError("TELEGRAM_TOKEN_MAIN not found in .env")

if not TELEGRAM_TOKEN_REPEATER_1:
    raise RuntimeError("TELEGRAM_TOKEN_REPEATER_1n ot found in .env")

if not TELEGRAM_TOKEN_REPEATER_2:
    raise RuntimeError("TELEGRAM_TOKEN_REPEATER_2 not found in .env")

CONFIG_TEMPLATE = {
    "GUILD_ID": 1324396791965417513,
    "OWNERS_IDS": [
        512079329619083291,
        436586208450052099
    ],
    "ADMIN_ROLES_IDS": [],
    "MODULES": {}
}

class ConfigWorker(DataWorker):
    def __init__(self, path: str, setup: dict | None = None):
        super().__init__(path, setup)

    def __setattr__(self, name: str, value: Any):
        if name in {"json_path", "_data"}:
            super().__setattr__(name, value)
            return

        self._data[name] = value
        self._commit_data()

    def module_add(self, module_path: str):
        if module_path in self.MODULES:
            return

        self.MODULES[module_path] = True
        self._data["MODULES"][module_path] = True
        self._commit_data()

    def module_on(self, module_path: str):
        self.MODULES[module_path] = True
        self._data["MODULES"][module_path] = True
        self._commit_data()

    def module_off(self, module_path: str):
        self.MODULES[module_path] = False
        self._data["MODULES"][module_path] = False
        self._commit_data()


config = ConfigWorker(CONFIG_PATH, CONFIG_TEMPLATE)