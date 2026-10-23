"""
Настройки старого моста (Discord ⇄ Telegram через JSON-очереди) — временно, пока пишется новый.

Раньше старый мост читал их из config_loader.py (config.json старого формата). 1 сентября 2026
config_loader удалили вместе с переходом на новый config.py, и старый мост перестал запускаться.
Здесь — тот же набор имён, что ждёт код старого моста, поверх нынешних config.json и .env.

Где задаются (заполняет владелец бота на сервере):
- GUILD                  — config.json → GUILD_ID (общий для бота);
- BR_DISCORD_CHANNEL_ID  — config.json → BR_DISCORD_CHANNEL_ID (канал Discord, который связан с Telegram);
- BR_TELEGRAM_CHANNEL_ID — config.json → BR_TELEGRAM_CHANNEL_ID (ID чата Telegram, обычно отрицательный);
- TELEGRAM_TOKEN_1/2     — .env → TELEGRAM_TOKEN_1 / TELEGRAM_TOKEN_2 (или те же ключи в config.json,
                           как было в старом формате). Бот 1 пересылает сообщения, бот 2 — реакции.

Если на сервере сохранился config.json старого формата, ключи BR_* и TELEGRAM_TOKEN_1/2 в нём
уже есть: новый config.py не удаляет незнакомые ключи.
"""

import os

from config import config as _config


def _get(name: str, default=None):
    # config — ConfigWorker над config.json; незнакомых ключей у него просто нет (None).
    value = getattr(_config, name, None)
    return default if value is None else value


class OldBridgeConfig:
    """Те же имена, что у старого config_loader.config — код старого моста не меняется."""

    @property
    def GUILD(self) -> int:
        return int(_get('GUILD_ID', 0) or 0)

    @property
    def BR_DISCORD_CHANNEL_ID(self) -> int:
        return int(_get('BR_DISCORD_CHANNEL_ID', 0) or 0)

    @property
    def BR_TELEGRAM_CHANNEL_ID(self) -> int:
        return int(_get('BR_TELEGRAM_CHANNEL_ID', 0) or 0)

    @property
    def TELEGRAM_TOKEN_1(self) -> str:
        return os.getenv('TELEGRAM_TOKEN_1') or _get('TELEGRAM_TOKEN_1', '') or ''

    @property
    def TELEGRAM_TOKEN_2(self) -> str:
        return os.getenv('TELEGRAM_TOKEN_2') or _get('TELEGRAM_TOKEN_2', '') or ''

    def missing(self) -> list[str]:
        """Какие настройки не заданы — без них старый мост не запускается."""
        names = ('GUILD', 'BR_DISCORD_CHANNEL_ID', 'BR_TELEGRAM_CHANNEL_ID', 'TELEGRAM_TOKEN_1', 'TELEGRAM_TOKEN_2')
        return [name for name in names if not getattr(self, name)]


config = OldBridgeConfig()
