import pytest
from datetime import datetime, timezone, timedelta

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.message import Message
from app.models.app_setting import AppSetting
from app.limiter.frequency_service import FrequencyLimitService


@pytest.fixture
def frequency_setup(db_session):
    camp1 = Campaign(name="Camp 1", message_template="Hi")
    camp2 = Campaign(name="Camp 2", message_template="Hi")
    contact = Contact(name="Alex", phone_e164="+14155552672", country_code="US")
    db_session.add_all([camp1, camp2, contact])
    db_session.commit()
    return camp1, camp2, contact


def test_frequency_eligible_initially(db_session, frequency_setup):
    camp1, _, contact = frequency_setup
    svc = FrequencyLimitService(db_session)

    res = svc.check_frequency(contact, camp1)
    assert res.eligible
    assert res.reason == "ELIGIBLE"
    assert res.next_allowed_at is None


def test_frequency_daily_limit_reached(db_session, frequency_setup):
    camp1, _, contact = frequency_setup
    svc = FrequencyLimitService(db_session)

    # Insert a SENT message sent 2 hours ago
    sent_msg = Message(
        campaign_id=camp1.id,
        contact_id=contact.id,
        rendered_content="Hello",
        idempotency_key="freq_test_1",
        status="SENT",
        sent_at=datetime.now(timezone.utc) - timedelta(hours=2)
    )
    db_session.add(sent_msg)
    db_session.commit()

    res = svc.check_frequency(contact, camp1)
    assert not res.eligible
    assert res.reason == "DAILY_LIMIT_REACHED"
    assert res.next_allowed_at is not None


def test_frequency_campaign_limit_reached(db_session, frequency_setup):
    camp1, _, contact = frequency_setup
    svc = FrequencyLimitService(db_session)

    # Set daily limit higher (2) to isolate campaign limit
    db_session.add(AppSetting(key="freq_max_messages_per_day", value="5"))
    db_session.add(AppSetting(key="freq_max_messages_per_campaign", value="1"))
    db_session.commit()

    # Message sent 3 days ago for camp1
    sent_msg = Message(
        campaign_id=camp1.id,
        contact_id=contact.id,
        rendered_content="Hello",
        idempotency_key="freq_test_2",
        status="SENT",
        sent_at=datetime.now(timezone.utc) - timedelta(days=3)
    )
    db_session.add(sent_msg)
    db_session.commit()

    res = svc.check_frequency(contact, camp1)
    assert not res.eligible
    assert res.reason == "CAMPAIGN_LIMIT_REACHED"


def test_frequency_cross_campaign_cooldown(db_session, frequency_setup):
    camp1, camp2, contact = frequency_setup
    svc = FrequencyLimitService(db_session)

    # Allow 5 per day, but enforce 24h cooldown between different campaigns
    db_session.add(AppSetting(key="freq_max_messages_per_day", value="5"))
    db_session.add(AppSetting(key="freq_cooldown_hours", value="24"))
    db_session.commit()

    # Contact received message from camp1 6 hours ago
    last_sent = datetime.now(timezone.utc) - timedelta(hours=6)
    sent_msg = Message(
        campaign_id=camp1.id,
        contact_id=contact.id,
        rendered_content="Hello from camp1",
        idempotency_key="freq_test_3",
        status="SENT",
        sent_at=last_sent
    )
    db_session.add(sent_msg)
    db_session.commit()

    # Now camp2 wants to message the contact
    res = svc.check_frequency(contact, camp2)
    assert not res.eligible
    assert res.reason == "CAMPAIGN_COOLDOWN_ACTIVE"
    assert res.next_allowed_at is not None
    assert res.next_allowed_at == last_sent + timedelta(hours=24)
