"""
FrequencyLimitService.

Enforces cross-campaign and temporal messaging limits on contacts.
Prevents over-messaging contacts while keeping rules configurable via AppSetting and campaign properties.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.message import Message
from app.models.app_setting import AppSetting
from app.models.campaign import Campaign
from app.models.contact import Contact


@dataclass(frozen=True)
class FrequencyResult:
    eligible: bool
    reason: str
    contact_id: int
    campaign_id: int
    next_allowed_at: Optional[datetime] = None


class FrequencyLimitService:
    """
    Evaluates frequency and cooldown constraints for contacts.
    """

    DEFAULT_MAX_PER_DAY = 1
    DEFAULT_MAX_PER_CAMPAIGN = 1
    DEFAULT_MAX_ACROSS_CAMPAIGNS_30D = 5
    DEFAULT_COOLDOWN_HOURS = 24

    def __init__(self, db: Session):
        self.db = db
        self._cached_thresholds = None

    def _get_setting_int(self, key: str, default_value: int) -> int:
        """Retrieves integer setting value from AppSetting or returns default."""
        from app.services.app_setting_service import AppSettingService
        return AppSettingService.get_int(self.db, key, default_value)

    def _load_thresholds(self):
        from app.services.app_setting_service import AppSettingService
        keys = [
            "freq_max_messages_per_day",
            "freq_max_messages_per_campaign",
            "freq_max_messages_30d",
            "freq_cooldown_hours",
        ]
        s_map = AppSettingService.get_many(self.db, keys)

        def _parse(k, def_val):
            val = s_map.get(k)
            if val is not None and val.strip().isdigit():
                return int(val.strip())
            return def_val

        self._cached_thresholds = {
            "max_per_day": _parse("freq_max_messages_per_day", self.DEFAULT_MAX_PER_DAY),
            "max_per_campaign": _parse("freq_max_messages_per_campaign", self.DEFAULT_MAX_PER_CAMPAIGN),
            "max_across_30d": _parse("freq_max_messages_30d", self.DEFAULT_MAX_ACROSS_CAMPAIGNS_30D),
            "cooldown_hours": _parse("freq_cooldown_hours", self.DEFAULT_COOLDOWN_HOURS),
        }
        return self._cached_thresholds

    def check_frequency(self, contact: Contact, campaign: Campaign, at_time: Optional[datetime] = None) -> FrequencyResult:
        """
        Determines whether a contact is within allowed messaging frequency limits.
        """
        now = at_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # 1. Configurable thresholds (loaded in single batched query per service instance)
        if self._cached_thresholds is None:
            self._load_thresholds()
        max_per_day = self._cached_thresholds["max_per_day"]
        max_per_campaign = self._cached_thresholds["max_per_campaign"]
        max_across_30d = self._cached_thresholds["max_across_30d"]
        cooldown_hours = self._cached_thresholds["cooldown_hours"]

        # 2. Check messages sent today (last 24h rolling)
        day_start = now - timedelta(days=1)
        sent_last_24h = self.db.query(func.count(Message.id)).filter(
            Message.contact_id == contact.id,
            Message.status == "SENT",
            Message.sent_at >= day_start
        ).scalar() or 0

        if sent_last_24h >= max_per_day:
            # Find earliest sent message in this window to calculate next allowed time
            earliest_in_window = self.db.query(Message.sent_at).filter(
                Message.contact_id == contact.id,
                Message.status == "SENT",
                Message.sent_at >= day_start
            ).order_by(Message.sent_at.asc()).first()

            if earliest_in_window and earliest_in_window[0]:
                earliest_ts = earliest_in_window[0]
                if earliest_ts.tzinfo is None:
                    earliest_ts = earliest_ts.replace(tzinfo=timezone.utc)
                next_allowed = earliest_ts + timedelta(days=1)
            else:
                next_allowed = now + timedelta(days=1)

            return FrequencyResult(
                eligible=False,
                reason="DAILY_LIMIT_REACHED",
                contact_id=contact.id,
                campaign_id=campaign.id,
                next_allowed_at=next_allowed
            )

        # 3. Check messages sent for this specific campaign
        sent_this_campaign = self.db.query(func.count(Message.id)).filter(
            Message.contact_id == contact.id,
            Message.campaign_id == campaign.id,
            Message.status == "SENT"
        ).scalar() or 0

        if sent_this_campaign >= max_per_campaign:
            return FrequencyResult(
                eligible=False,
                reason="CAMPAIGN_LIMIT_REACHED",
                contact_id=contact.id,
                campaign_id=campaign.id,
                next_allowed_at=None
            )

        # 4. Check messages sent in the last 30 days across all campaigns
        month_start = now - timedelta(days=30)
        sent_last_30d = self.db.query(func.count(Message.id)).filter(
            Message.contact_id == contact.id,
            Message.status == "SENT",
            Message.sent_at >= month_start
        ).scalar() or 0

        if sent_last_30d >= max_across_30d:
            earliest_30d = self.db.query(Message.sent_at).filter(
                Message.contact_id == contact.id,
                Message.status == "SENT",
                Message.sent_at >= month_start
            ).order_by(Message.sent_at.asc()).first()

            if earliest_30d and earliest_30d[0]:
                earliest_ts = earliest_30d[0]
                if earliest_ts.tzinfo is None:
                    earliest_ts = earliest_ts.replace(tzinfo=timezone.utc)
                next_allowed = earliest_ts + timedelta(days=30)
            else:
                next_allowed = now + timedelta(days=30)

            return FrequencyResult(
                eligible=False,
                reason="CROSS_CAMPAIGN_30D_LIMIT_REACHED",
                contact_id=contact.id,
                campaign_id=campaign.id,
                next_allowed_at=next_allowed
            )

        # 5. Check cooldown between campaigns
        # If contacted by a different campaign recently, enforce cooldown
        last_sent = self.db.query(Message.sent_at, Message.campaign_id).filter(
            Message.contact_id == contact.id,
            Message.status == "SENT"
        ).order_by(Message.sent_at.desc()).first()

        if last_sent:
            last_sent_at, last_camp_id = last_sent
            if last_camp_id != campaign.id and last_sent_at:
                if last_sent_at.tzinfo is None:
                    last_sent_at = last_sent_at.replace(tzinfo=timezone.utc)
                cooldown_delta = timedelta(hours=cooldown_hours)
                if now < last_sent_at + cooldown_delta:
                    next_allowed = last_sent_at + cooldown_delta
                    return FrequencyResult(
                        eligible=False,
                        reason="CAMPAIGN_COOLDOWN_ACTIVE",
                        contact_id=contact.id,
                        campaign_id=campaign.id,
                        next_allowed_at=next_allowed
                    )

        return FrequencyResult(
            eligible=True,
            reason="ELIGIBLE",
            contact_id=contact.id,
            campaign_id=campaign.id,
            next_allowed_at=None
        )
