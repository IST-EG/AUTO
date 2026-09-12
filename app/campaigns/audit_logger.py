"""
Audit Logger for campaign events.
"""
from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog

class CampaignAuditLogger:
    """Helper service to log campaign business events."""
    
    def __init__(self, db: Session):
        self.db = db
        
    def log_event(self, event_type: str, campaign_id: int, status: str = None, 
                  error_message: str = None, result: str = None, contact_id: int = None):
        """Creates and persists an audit log entry."""
        log = AuditLog(
            event_type=event_type,
            campaign_id=campaign_id,
            status=status,
            error_message=error_message,
            result=result,
            contact_id=contact_id
        )
        self.db.add(log)
        self.db.commit()
        return log
