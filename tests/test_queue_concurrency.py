import os
import tempfile
import threading
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.message import Message
from app.queue.service import PersistentQueueService
from app.queue.state_machine import QueueState


def test_concurrent_worker_claiming():
    """
    Simulates multiple worker threads concurrently competing to claim queue items.
    Uses file-based SQLite with WAL mode to simulate true multi-connection worker processes.
    Verifies that two workers never claim the same message.
    """
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "concurrency_test.db")
    engine = create_engine(
        f"sqlite:///{db_path}?timeout=30",
        connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    # Seed campaign, contacts, and 15 queued messages
    setup_session = Session()
    camp = Campaign(name="Concurrency Campaign", message_template="Hello")
    setup_session.add(camp)
    setup_session.commit()

    for i in range(15):
        contact = Contact(name=f"Contact_{i}", phone_e164=f"+141555500{i:02d}", country_code="US")
        setup_session.add(contact)
        setup_session.commit()

        msg = Message(
            campaign_id=camp.id,
            contact_id=contact.id,
            rendered_content=f"Hello {i}",
            idempotency_key=f"conc_key_{i}",
            status=QueueState.QUEUED
        )
        setup_session.add(msg)
    camp_id = camp.id
    setup_session.close()

    claimed_ids = []
    lock = threading.Lock()

    def worker_thread(worker_name: str):
        thread_session = Session()
        q_svc = PersistentQueueService(thread_session)
        for _ in range(5):
            msg = q_svc.claim_next_message(worker_id=worker_name, campaign_id=camp_id)
            if msg:
                with lock:
                    claimed_ids.append(msg.id)
        thread_session.close()

    # Launch 5 concurrent workers
    threads = []
    for w in range(5):
        t = threading.Thread(target=worker_thread, args=(f"worker_{w}",))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Verify: Every claimed message ID is strictly unique (no two workers claimed the same item)
    assert len(claimed_ids) > 0
    assert len(claimed_ids) == len(set(claimed_ids)), f"Duplicate claims detected: {claimed_ids}"

    engine.dispose()
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)
