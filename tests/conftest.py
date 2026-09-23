import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import *
from app.utils.settings import settings


@pytest.fixture(scope="session", autouse=True)
def configure_test_worker_instance_id():
    """Sets a valid test WORKER_INSTANCE_ID so integration tests run in a configured worker environment."""
    old_id = getattr(settings, "WORKER_INSTANCE_ID", "")
    settings.WORKER_INSTANCE_ID = "test-worker-01"
    yield
    settings.WORKER_INSTANCE_ID = old_id


@pytest.fixture(scope="session")
def engine():
    return create_engine("sqlite:///:memory:")

@pytest.fixture(scope="session")
def tables(engine):
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)

@pytest.fixture
def db_session(engine, tables):
    """Returns a session with a clean rollback after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()

    yield session

    session.close()
    transaction.rollback()
    connection.close()
