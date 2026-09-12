import pytest
from app.models.campaign import Campaign
from app.models.message import Message
from app.scheduler.batch_manager import BatchManager


def test_batch_lifecycle_and_accounting(db_session):
    camp = Campaign(name="Batch Camp", message_template="Hello")
    db_session.add(camp)
    db_session.commit()

    bm = BatchManager(db_session)
    batch = bm.create_batch(campaign_id=camp.id, total_items=2)
    assert batch.batch_number == 1
    assert batch.status == "PENDING"
    assert batch.processed_items == 0

    batch = bm.start_batch(batch.id)
    assert batch.status == "IN_PROGRESS"
    assert batch.started_at is not None

    # Process first item (success)
    batch = bm.record_item_result(batch.id, success=True)
    assert batch.processed_items == 1
    assert batch.successful_items == 1
    assert batch.status == "IN_PROGRESS"

    # Process second item (failed)
    batch = bm.record_item_result(batch.id, success=False)
    assert batch.processed_items == 2
    assert batch.failed_items == 1
    assert batch.status == "COMPLETED"
    assert batch.completed_at is not None


def test_batch_crash_recovery(db_session):
    camp = Campaign(name="Recovery Camp", message_template="Hello")
    db_session.add(camp)
    db_session.commit()

    bm = BatchManager(db_session)
    batch = bm.create_batch(campaign_id=camp.id, total_items=3)
    bm.start_batch(batch.id)

    # Simulate messages committed to DB before crash
    m1 = Message(campaign_id=camp.id, contact_id=1, batch_id=batch.id, rendered_content="1", idempotency_key="rec_1", status="SENT")
    m2 = Message(campaign_id=camp.id, contact_id=2, batch_id=batch.id, rendered_content="2", idempotency_key="rec_2", status="FAILED")
    db_session.add_all([m1, m2])
    db_session.commit()

    # Simulate corrupted or un-updated in-memory batch state
    batch.processed_items = 0
    batch.successful_items = 0
    batch.failed_items = 0
    db_session.commit()

    # Run recovery
    recovered = bm.recover_batch(batch.id)
    assert recovered.processed_items == 2
    assert recovered.successful_items == 1
    assert recovered.failed_items == 1
    assert recovered.status == "IN_PROGRESS"
