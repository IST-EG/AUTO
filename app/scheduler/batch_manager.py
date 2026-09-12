"""
Batch Manager.

Manages persistent batch progression, item accounting, and crash recovery.
"""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.campaign_batch import CampaignBatch
from app.models.message import Message


class BatchManager:
    """
    Manages persistent campaign batches and guarantees safe restart recovery.
    """

    def __init__(self, db: Session):
        self.db = db

    def create_batch(self, campaign_id: int, total_items: int) -> CampaignBatch:
        """
        Allocates a new sequential batch for a campaign.
        """
        # Determine next batch sequence number
        max_batch = self.db.query(func.max(CampaignBatch.batch_number)).filter(
            CampaignBatch.campaign_id == campaign_id
        ).scalar() or 0

        now = datetime.now(timezone.utc)
        batch = CampaignBatch(
            campaign_id=campaign_id,
            batch_number=max_batch + 1,
            status="PENDING",
            total_items=total_items,
            processed_items=0,
            successful_items=0,
            failed_items=0,
            created_at=now,
            updated_at=now
        )

        self.db.add(batch)
        self.db.commit()
        self.db.refresh(batch)
        return batch

    def start_batch(self, batch_id: int) -> CampaignBatch:
        """
        Starts an allocated batch.
        """
        batch = self.db.query(CampaignBatch).filter(CampaignBatch.id == batch_id).first()
        if not batch:
            raise ValueError(f"Batch {batch_id} not found.")

        now = datetime.now(timezone.utc)
        batch.status = "IN_PROGRESS"
        if not batch.started_at:
            batch.started_at = now
        batch.updated_at = now

        self.db.commit()
        self.db.refresh(batch)
        return batch

    def record_item_result(self, batch_id: int, success: bool, skipped: bool = False) -> CampaignBatch:
        """
        Updates batch metrics as an item completes.
        Transitions to COMPLETED once all items are processed.
        """
        batch = self.db.query(CampaignBatch).filter(CampaignBatch.id == batch_id).first()
        if not batch:
            raise ValueError(f"Batch {batch_id} not found.")

        batch.processed_items += 1
        if success:
            batch.successful_items += 1
        elif not skipped:
            batch.failed_items += 1

        now = datetime.now(timezone.utc)
        if batch.total_items > 0 and batch.processed_items >= batch.total_items:
            batch.status = "COMPLETED"
            batch.completed_at = now

        batch.updated_at = now
        self.db.commit()
        self.db.refresh(batch)
        return batch

    def pause_batch(self, batch_id: int) -> CampaignBatch:
        """
        Pauses an active batch.
        """
        batch = self.db.query(CampaignBatch).filter(CampaignBatch.id == batch_id).first()
        if not batch:
            raise ValueError(f"Batch {batch_id} not found.")

        batch.status = "PAUSED"
        batch.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(batch)
        return batch

    def resume_batch(self, batch_id: int) -> CampaignBatch:
        """
        Resumes a paused batch.
        """
        batch = self.db.query(CampaignBatch).filter(CampaignBatch.id == batch_id).first()
        if not batch:
            raise ValueError(f"Batch {batch_id} not found.")

        batch.status = "IN_PROGRESS"
        batch.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(batch)
        return batch

    def recover_batch(self, batch_id: int) -> CampaignBatch:
        """
        Reconciles batch counters against persisted messages ground truth.
        Prevents batch progress from resetting to zero after a process crash.
        """
        batch = self.db.query(CampaignBatch).filter(CampaignBatch.id == batch_id).first()
        if not batch:
            raise ValueError(f"Batch {batch_id} not found.")

        # Query ground truth from messages table
        sent_count = self.db.query(func.count(Message.id)).filter(
            Message.batch_id == batch_id,
            Message.status == "SENT"
        ).scalar() or 0

        failed_count = self.db.query(func.count(Message.id)).filter(
            Message.batch_id == batch_id,
            Message.status == "FAILED"
        ).scalar() or 0

        skipped_count = self.db.query(func.count(Message.id)).filter(
            Message.batch_id == batch_id,
            Message.status == "SKIPPED"
        ).scalar() or 0

        batch.successful_items = sent_count
        batch.failed_items = failed_count
        batch.processed_items = sent_count + failed_count + skipped_count

        now = datetime.now(timezone.utc)
        if batch.total_items > 0 and batch.processed_items >= batch.total_items:
            batch.status = "COMPLETED"
            batch.completed_at = now
        elif batch.status != "PAUSED":
            batch.status = "IN_PROGRESS"

        batch.updated_at = now
        self.db.commit()
        self.db.refresh(batch)
        return batch

    def get_active_batch(self, campaign_id: int) -> Optional[CampaignBatch]:
        """
        Finds the current IN_PROGRESS or PAUSED batch for a campaign.
        """
        return self.db.query(CampaignBatch).filter(
            CampaignBatch.campaign_id == campaign_id,
            CampaignBatch.status.in_(["IN_PROGRESS", "PAUSED", "PENDING"])
        ).order_by(CampaignBatch.batch_number.asc()).first()
