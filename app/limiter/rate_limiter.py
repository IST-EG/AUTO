"""
Rate Limiter.

Provides operational pacing, workload management, and compliance rate-limiting.
Strictly decoupled from messaging providers.
Does NOT attempt to imitate human activity or bypass platform security systems.
"""

import random
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.message import Message
from app.models.campaign import Campaign
from app.models.app_setting import AppSetting


class RateLimiter:
    """
    Independent rate limiter enforcing operational intervals, batch pauses, and daily caps.
    """

    def __init__(self, db: Session):
        self.db = db

    def calculate_interval_delay(self, campaign: Optional[Campaign] = None) -> float:
        """
        Calculates operational pacing delay between sends for workload distribution.
        """
        min_delay = 10.0
        max_delay = 30.0

        if campaign:
            min_delay = float(campaign.min_delay_seconds)
            max_delay = float(campaign.max_delay_seconds)

        if min_delay > max_delay:
            min_delay, max_delay = max_delay, min_delay

        return random.uniform(min_delay, max_delay)

    def check_daily_quota(self, campaign: Campaign, at_time: Optional[datetime] = None) -> Tuple[bool, int, int]:
        """
        Checks whether daily message quota has been reached for a campaign.
        Returns: (is_quota_available: bool, sent_today: int, limit: int)
        """
        now = at_time or datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

        sent_today = self.db.query(func.count(Message.id)).filter(
            Message.campaign_id == campaign.id,
            Message.status == "SENT",
            Message.sent_at >= start_of_day
        ).scalar() or 0

        limit = campaign.daily_limit
        is_available = sent_today < limit
        return is_available, sent_today, limit

    def check_global_daily_quota(self, at_time: Optional[datetime] = None) -> Tuple[bool, int, Optional[int]]:
        """
        Checks whether system-wide global daily quota has been reached.
        Returns: (is_quota_available: bool, sent_today: int, global_limit: Optional[int])
        """
        setting = self.db.query(AppSetting).filter(AppSetting.key == "global_daily_limit").first()
        if not setting or not setting.value.strip().isdigit():
            return True, 0, None

        global_limit = int(setting.value.strip())
        now = at_time or datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

        sent_today = self.db.query(func.count(Message.id)).filter(
            Message.status == "SENT",
            Message.sent_at >= start_of_day
        ).scalar() or 0

        is_available = sent_today < global_limit
        return is_available, sent_today, global_limit

    def should_pause_for_batch(self, messages_sent_in_batch: int, campaign: Campaign) -> bool:
        """
        Determines if worker should pause following the completion of a batch chunk.
        """
        if campaign.batch_size <= 0:
            return False
        return messages_sent_in_batch >= campaign.batch_size

    def get_batch_pause_seconds(self, campaign: Campaign) -> float:
        """
        Returns configured batch pause delay in seconds.
        """
        return max(0.0, float(campaign.batch_pause_seconds))
