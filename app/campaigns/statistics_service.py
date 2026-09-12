"""
Campaign Statistics Service.
"""
from typing import Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.campaign_contact import CampaignContact

class CampaignStatisticsService:
    """Provides efficient statistics aggregations for a campaign."""
    
    def __init__(self, db: Session):
        self.db = db

    def get_statistics(self, campaign_id: int) -> Dict[str, Any]:
        """
        Calculates campaign statistics efficiently using DB aggregation.
        """
        # Query: SELECT status, count(id) FROM campaign_contacts WHERE campaign_id = X GROUP BY status
        query_results = self.db.query(
            CampaignContact.status, 
            func.count(CampaignContact.id)
        ).filter(
            CampaignContact.campaign_id == campaign_id
        ).group_by(CampaignContact.status).all()

        stats = {
            "total_contacts": 0,
            "eligible": 0,
            "excluded": 0,
            "pending": 0,
            "queued": 0,
            "sent": 0,
            "failed": 0,
            "skipped": 0,
            "replies": 0,  # Not implemented yet
            "completion_percentage": 0.0
        }
        
        for status, count in query_results:
            status_lower = status.lower()
            if status_lower in stats:
                stats[status_lower] = count
            stats["total_contacts"] += count
            
        # Calculate completion percentage
        # We consider a contact "processed" if it is SENT, FAILED, SKIPPED, or EXCLUDED.
        # Pending, Eligible, Queued are not processed.
        processed = stats["sent"] + stats["failed"] + stats["skipped"] + stats["excluded"]
        if stats["total_contacts"] > 0:
            stats["completion_percentage"] = round((processed / stats["total_contacts"]) * 100.0, 2)
            
        return stats
