"""
Centralized AppSetting Service.

Provides efficient, batched key-value configuration retrieval.
Replaces N individual SQL queries on app_settings with single multi-key queries:
    SELECT key, value FROM app_settings WHERE key IN (:k1, :k2, ...)

Preserves:
- Strict semantic freshness (direct DB reads per call, no long-lived stale cache).
- Exact missing-key semantics (returns default or None).
- Explicit isolation: Emergency Stop is NOT routed through generic caching.
"""

import json
from typing import Dict, Any, Optional, Sequence, List
from sqlalchemy.orm import Session
from app.models.app_setting import AppSetting
from app.utils.logger import get_logger

logger = get_logger("app_setting_service")


class AppSettingService:
    """Centralized, batched access path for application settings."""

    @staticmethod
    def get_many(db: Session, keys: Sequence[str]) -> Dict[str, Optional[str]]:
        """
        Fetches multiple AppSetting values in a single SELECT query.

        Args:
            db: Active SQLAlchemy Session
            keys: Sequence of setting key strings

        Returns:
            Dict mapping every requested key to its string value, or None if not found in DB.
        """
        if not keys:
            return {}

        unique_keys = list(set(keys))
        try:
            rows = (
                db.query(AppSetting.key, AppSetting.value)
                .filter(AppSetting.key.in_(unique_keys))
                .all()
            )
            found = {row[0]: row[1] for row in rows}
            return {k: found.get(k) for k in keys}
        except Exception as e:
            logger.warning(f"Error batch-loading app_settings for keys {unique_keys}: {e}")
            return {k: None for k in keys}

    @staticmethod
    def get(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Fetches a single setting with fallback default.
        """
        try:
            row = db.query(AppSetting.value).filter(AppSetting.key == key).first()
            if row and row[0] is not None:
                return row[0]
            return default
        except Exception as e:
            logger.warning(f"Error loading app_setting {key}: {e}")
            return default

    @staticmethod
    def get_int(db: Session, key: str, default: int) -> int:
        """Fetches integer setting value from AppSetting or returns default."""
        val = AppSettingService.get(db, key)
        if val is not None and val.strip().isdigit():
            return int(val.strip())
        return default

    @staticmethod
    def get_json(db: Session, key: str, default: Any = None) -> Any:
        """Fetches and JSON-deserializes setting value or returns default."""
        val = AppSettingService.get(db, key)
        if val is not None:
            try:
                return json.loads(val)
            except Exception as e:
                logger.warning(f"Failed to parse JSON setting for key '{key}': {e}")
                return default
        return default
