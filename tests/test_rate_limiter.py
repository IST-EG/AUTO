import pytest
from datetime import datetime, timezone, timedelta

from app.models.campaign import Campaign
from app.models.message import Message
from app.models.app_setting import AppSetting
from app.limiter.rate_limiter import RateLimiter


def test_rate_limiter_interval_delay():
    camp = Campaign(min_delay_seconds=5, max_delay_seconds=15)
    limiter = RateLimiter(None)

    for _ in range(50):
        delay = limiter.calculate_interval_delay(camp)
        assert 5.0 <= delay <= 15.0


def test_rate_limiter_daily_quota(db_session):
    camp = Campaign(name="Quota Camp", message_template="Hi", daily_limit=2)
    db_session.add(camp)
    db_session.commit()

    limiter = RateLimiter(db_session)
    available, sent, limit = limiter.check_daily_quota(camp)
    assert available is True
    assert sent == 0
    assert limit == 2

    # Insert 2 sent messages today
    now = datetime.now(timezone.utc)
    m1 = Message(campaign_id=camp.id, contact_id=1, rendered_content="c", idempotency_key="q1", status="SENT", sent_at=now)
    m2 = Message(campaign_id=camp.id, contact_id=2, rendered_content="c", idempotency_key="q2", status="SENT", sent_at=now)
    db_session.add_all([m1, m2])
    db_session.commit()

    available, sent, limit = limiter.check_daily_quota(camp)
    assert available is False
    assert sent == 2


def test_rate_limiter_global_daily_quota(db_session):
    db_session.add(AppSetting(key="global_daily_limit", value="1"))
    db_session.commit()

    limiter = RateLimiter(db_session)

    # 0 sent
    available, sent, limit = limiter.check_global_daily_quota()
    assert available is True
    assert limit == 1

    # Insert 1 sent message
    db_session.add(Message(campaign_id=1, contact_id=1, rendered_content="c", idempotency_key="g1", status="SENT", sent_at=datetime.now(timezone.utc)))
    db_session.commit()

    available, sent, limit = limiter.check_global_daily_quota()
    assert available is False
    assert sent == 1


def test_rate_limiter_batch_pause():
    camp = Campaign(batch_size=10, batch_pause_seconds=60)
    limiter = RateLimiter(None)

    assert not limiter.should_pause_for_batch(9, camp)
    assert limiter.should_pause_for_batch(10, camp)
    assert limiter.should_pause_for_batch(15, camp)
    assert limiter.get_batch_pause_seconds(camp) == 60.0
