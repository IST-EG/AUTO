"""
Unit tests for authoritative OS process lock and stale process recovery.
"""

import os
import json
import tempfile
import pytest
from app.runner.process_lock import ProcessLock, is_pid_alive
from app.models.app_setting import AppSetting


def test_is_pid_alive_current_process():
    assert is_pid_alive(os.getpid()) is True


def test_is_pid_alive_invalid_pid():
    assert is_pid_alive(-1) is False
    assert is_pid_alive(9999999) is False


def test_process_lock_acquisition_and_release(db_session, tmp_path):
    lock_file = str(tmp_path / "test_runner.lock")
    lock = ProcessLock(db=db_session, lock_file_path=lock_file)

    # Acquire lock
    assert lock.acquire(worker_id="test_w1", campaign_id=1) is True
    assert lock.is_held is True
    assert os.path.exists(lock_file)

    # Check lock file content
    data = lock.read_lock_file()
    assert data is not None
    assert data["pid"] == os.getpid()
    assert data["worker_id"] == "test_w1"
    assert data["campaign_id"] == 1

    # Check DB heartbeat
    setting = db_session.query(AppSetting).filter(AppSetting.key == ProcessLock.DB_SETTING_KEY).first()
    assert setting is not None
    db_data = json.loads(setting.value)
    assert db_data["worker_id"] == "test_w1"

    # Release lock
    lock.release()
    assert lock.is_held is False
    assert not os.path.exists(lock_file)

    # Check DB setting removed
    setting_after = db_session.query(AppSetting).filter(AppSetting.key == ProcessLock.DB_SETTING_KEY).first()
    assert setting_after is None


def test_process_lock_duplicate_acquisition_blocked(db_session, tmp_path):
    lock_file = str(tmp_path / "test_runner.lock")
    lock1 = ProcessLock(db=db_session, lock_file_path=lock_file)
    lock2 = ProcessLock(db=db_session, lock_file_path=lock_file)

    # Lock 1 acquires
    assert lock1.acquire(worker_id="worker_1", campaign_id=1) is True

    # Lock 2 attempts to acquire same file -> blocked
    assert lock2.acquire(worker_id="worker_2", campaign_id=2) is False
    assert lock2.is_held is False

    # Lock 1 releases -> Lock 2 can now acquire
    lock1.release()
    assert lock2.acquire(worker_id="worker_2", campaign_id=2) is True
    lock2.release()


def test_process_lock_stale_pid_recovery(db_session, tmp_path):
    lock_file = str(tmp_path / "test_runner.lock")

    # Manually write lockfile simulating a crashed process with dead PID (9999999)
    stale_data = {
        "pid": 9999999,
        "worker_id": "crashed_worker",
        "campaign_id": 9,
        "started_at": "2026-09-11T00:00:00Z",
    }
    with open(lock_file, "w") as f:
        json.dump(stale_data, f)

    # ProcessLock should detect dead PID, clear stale lock, and acquire cleanly
    lock = ProcessLock(db=db_session, lock_file_path=lock_file)
    assert lock.acquire(worker_id="fresh_worker", campaign_id=1) is True
    assert lock.is_held is True

    fresh_data = lock.read_lock_file()
    assert fresh_data is not None
    assert fresh_data["pid"] == os.getpid()
    assert fresh_data["worker_id"] == "fresh_worker"

    lock.release()


def test_process_lock_update_heartbeat(db_session, tmp_path):
    lock_file = str(tmp_path / "test_runner.lock")
    lock = ProcessLock(db=db_session, lock_file_path=lock_file)
    assert lock.acquire(worker_id="hb_worker", campaign_id=1) is True

    initial_info = lock.read_lock_file()
    initial_hb = initial_info.get("last_heartbeat")

    lock.update_heartbeat()
    updated_info = lock.read_lock_file()
    assert "last_heartbeat" in updated_info

    lock.release()


def test_get_active_runner_info(db_session, tmp_path):
    lock_file = str(tmp_path / "test_active.lock")
    lock = ProcessLock(db=db_session, lock_file_path=lock_file)

    # When no runner
    assert lock.get_active_runner_info() is None

    # When lock acquired by current process (alive)
    lock.acquire(worker_id="active_w", campaign_id=5)
    info = lock.get_active_runner_info()
    assert info is not None
    assert info["worker_id"] == "active_w"
    assert info["pid"] == os.getpid()

    lock.release()
    assert lock.get_active_runner_info() is None


def test_get_active_runner_info_db_fallback(db_session, tmp_path):
    lock_file = str(tmp_path / "non_existent.lock")
    lock = ProcessLock(db=db_session, lock_file_path=lock_file)

    # Insert DB heartbeat with current PID (alive)
    setting = AppSetting(
        key=ProcessLock.DB_SETTING_KEY,
        value=json.dumps({"pid": os.getpid(), "worker_id": "db_w", "campaign_id": 3}),
        description="test"
    )
    db_session.add(setting)
    db_session.commit()

    info = lock.get_active_runner_info()
    assert info is not None
    assert info["worker_id"] == "db_w"

    db_session.delete(setting)
    db_session.commit()


def test_process_lock_unlocked_operations(db_session, tmp_path):
    lock_file = str(tmp_path / "unlocked.lock")
    lock = ProcessLock(db=db_session, lock_file_path=lock_file)

    # These should be safe no-ops
    lock.update_heartbeat()
    lock.release()
    assert lock.read_lock_file() is None

