"""
RSN Config Layer
Standalone JSON configuration for RSN module (modules/rsn/rsn_data.json)
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any


class RsnConfig:
    """
    Собственный слой конфигурации для модуля RSN.
    Файл: modules/rsn/rsn_data.json
    """

    DEFAULT_CONFIG = {
        "log_channel_id": 0,
        "admin_notify_channel_id": 0,
        "export_channel_id": 0,
        "admin_role_ids": [],
        "moderator_role_ids": [],
        "mute_role_id": 0,
        "full_mute_role_id": 0,
        "scale_max": 50,
        "scale_thresholds": {
            "isolator": [31, 50],
            "restricted": [11, 30],
            "critical": [1, 10],
            "ban_trigger": 0
        },
        "duration_multiplier": {
            "isolator": 1,
            "restricted": 2,
            "critical": 3
        },
        "weekly_point_gain": 1,
        "reset_points_on_return": 10,
        "last_weekly_gain_timestamp": 0
    }

    def __init__(self, config_path: str = "modules/rsn/rsn_data.json"):
        self.config_path = config_path
        self._data: Dict[str, Any] = {}
        self._load_or_create()

    def _load_or_create(self):
        """Загрузить конфиг или создать с дефолтами."""
        path = Path(self.config_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = self.DEFAULT_CONFIG.copy()
                self._save()
        else:
            self._data = self.DEFAULT_CONFIG.copy()
            self._save()

    def _save(self):
        """Сохранить конфиг в JSON."""
        path = Path(self.config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default: Any = None) -> Any:
        """Получить значение по ключу."""
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        """Установить значение и сохранить."""
        self._data[key] = value
        self._save()

    # Channel IDs
    def get_log_channel_id(self) -> int:
        return self.get("log_channel_id", 0)

    def set_log_channel_id(self, channel_id: int):
        self.set("log_channel_id", channel_id)

    def get_admin_notify_channel_id(self) -> int:
        return self.get("admin_notify_channel_id", 0)

    def set_admin_notify_channel_id(self, channel_id: int):
        self.set("admin_notify_channel_id", channel_id)

    def get_export_channel_id(self) -> int:
        return self.get("export_channel_id", 0)

    def set_export_channel_id(self, channel_id: int):
        self.set("export_channel_id", channel_id)

    # Role IDs
    def get_admin_role_ids(self) -> List[int]:
        return self.get("admin_role_ids", [])

    def set_admin_role_ids(self, role_ids: List[int]):
        self.set("admin_role_ids", role_ids)

    def get_moderator_role_ids(self) -> List[int]:
        return self.get("moderator_role_ids", [])

    def set_moderator_role_ids(self, role_ids: List[int]):
        self.set("moderator_role_ids", role_ids)

    def get_mute_role_id(self) -> int:
        return self.get("mute_role_id", 0)

    def set_mute_role_id(self, role_id: int):
        self.set("mute_role_id", role_id)

    def get_full_mute_role_id(self) -> int:
        return self.get("full_mute_role_id", 0)

    def set_full_mute_role_id(self, role_id: int):
        self.set("full_mute_role_id", role_id)

    # Scale settings
    def get_scale_max(self) -> int:
        return self.get("scale_max", 50)

    def set_scale_max(self, value: int):
        self.set("scale_max", value)

    def get_scale_thresholds(self) -> Dict[str, Any]:
        return self.get("scale_thresholds", {})

    def get_duration_multiplier(self) -> Dict[str, int]:
        return self.get("duration_multiplier", {})

    def get_multiplier_for_points(self, points: int) -> int:
        """Получить множитель длительности на основе текущих баллов."""
        thresholds = self.get_scale_thresholds()
        multiplier = self.get_duration_multiplier()

        # isolator: 31-50
        if points >= thresholds.get("isolator", [31, 50])[0]:
            return multiplier.get("isolator", 1)
        # restricted: 11-30
        elif points >= thresholds.get("restricted", [11, 30])[0]:
            return multiplier.get("restricted", 2)
        # critical: 1-10
        elif points > thresholds.get("ban_trigger", 0):
            return multiplier.get("critical", 3)
        # ban_trigger: <= 0
        else:
            return multiplier.get("critical", 3)

    # General settings
    def get_weekly_point_gain(self) -> int:
        return self.get("weekly_point_gain", 1)

    def set_weekly_point_gain(self, value: int):
        self.set("weekly_point_gain", value)

    def get_reset_points_on_return(self) -> int:
        return self.get("reset_points_on_return", 10)

    def set_reset_points_on_return(self, value: int):
        self.set("reset_points_on_return", value)

    def get_last_weekly_gain_timestamp(self) -> int:
        """Получить timestamp последнего еженедельного начисления."""
        return self.get("last_weekly_gain_timestamp", 0)

    def set_last_weekly_gain_timestamp(self, timestamp: int):
        """Установить timestamp последнего еженедельного начисления."""
        self.set("last_weekly_gain_timestamp", timestamp)

    def is_admin_or_mod(self, user_id: int) -> bool:
        """Проверить, админ ли или модератор."""
        admin_ids = self.get_admin_role_ids()
        mod_ids = self.get_moderator_role_ids()
        return user_id in admin_ids or user_id in mod_ids
