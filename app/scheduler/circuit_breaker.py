"""
Circuit Breaker.

Monitors failure patterns and trips campaigns to PAUSED when consecutive error threshold is met.
Prevents systemic failures from continuing unchecked.
"""

from typing import Dict
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.audit_log import AuditLog


class CircuitBreaker:
    """
    Campaign-level circuit breaker tracking consecutive dispatch failures.
    """

    def __init__(self, db: Session):
        self.db = db
        # In-memory consecutive error counter per campaign_id
        self._consecutive_errors: Dict[int, int] = {}

    def get_consecutive_errors(self, campaign_id: int) -> int:
        """Returns the current consecutive error count for a campaign."""
        return self._consecutive_errors.get(campaign_id, 0)

    def record_success(self, campaign_id: int) -> None:
        """Resets the consecutive error count upon successful send."""
        self._consecutive_errors[campaign_id] = 0

    def record_failure(self, campaign: Campaign, error_message: str) -> bool:
        """
        Records a failure for the campaign.
        Trips circuit breaker and pauses campaign if threshold is exceeded.
        Returns: True if tripped, False otherwise.
        """
        campaign_id = campaign.id
        current_errors = self._consecutive_errors.get(campaign_id, 0) + 1
        self._consecutive_errors[campaign_id] = current_errors

        threshold = max(1, campaign.error_threshold)
        if current_errors >= threshold:
            # Trip the circuit breaker: pause campaign
            campaign.status = "PAUSED"

            # Create audit log
            log = AuditLog(
                event_type="CIRCUIT_BREAKER_TRIPPED",
                campaign_id=campaign_id,
                status="PAUSED",
                error_message=f"Consecutive errors ({current_errors}) met or exceeded threshold ({threshold}): {error_message}"
            )
            self.db.add(log)
            self.db.commit()
            return True

        return False

    def reset(self, campaign_id: int) -> None:
        """Explicitly resets circuit breaker counter for a campaign."""
        self._consecutive_errors[campaign_id] = 0
