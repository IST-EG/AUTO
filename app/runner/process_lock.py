"""
Authoritative OS Process Lock and Runner Singularity Manager.

Guarantees that only ONE production runner process can operate against the
WhatsApp automation system on the host machine.
Authoritative lock is enforced via OS-level non-blocking file locking.
A database heartbeat in AppSetting ('system:active_runner') provides cross-CLI
visibility and stale-runner detection.
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.utils.settings import settings

logger = logging.getLogger(__name__)

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def is_pid_alive(pid: int) -> bool:
    """Checks whether a process with the given PID is currently active on the OS."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return exit_code.value == STILL_ACTIVE
        else:
            os.kill(pid, 0)
            return True
    except OSError:
        return False
    except Exception:
        return False


class ProcessLockError(Exception):
    """Raised when process lock cannot be acquired or manipulated."""
    pass


class ProcessLock:
    """
    Manages process singularity for the Production Runner using OS-level file locking
    and database heartbeat reflection.
    """

    DB_SETTING_KEY = "system:active_runner"

    def __init__(
        self,
        db: Optional[Session] = None,
        lock_file_path: Optional[str] = None
    ):
        self.db = db
        self.lock_file_path = lock_file_path or settings.RUNNER_LOCK_FILE
        self._file_handle = None
        self._locked = False
        self._lock_metadata: Optional[Dict[str, Any]] = None

    @property
    def is_held(self) -> bool:
        """Returns True if this instance currently holds the lock."""
        return self._locked

    def acquire(self, worker_id: str, campaign_id: int) -> bool:
        """
        Attempts to acquire the authoritative OS file lock.
        If an existing lock is held by a dead process, it is recovered.
        Returns True if acquired, False if an active runner is already running.
        """
        if self._locked:
            return True

        lock_dir = os.path.dirname(os.path.abspath(self.lock_file_path))
        if lock_dir and not os.path.exists(lock_dir):
            os.makedirs(lock_dir, exist_ok=True)

        # Check existing lockfile content for active runner
        if os.path.exists(self.lock_file_path):
            existing_info = self.read_lock_file()
            if existing_info:
                existing_pid = existing_info.get("pid")
                if existing_pid and is_pid_alive(existing_pid):
                    logger.warning(
                        f"Runner lock held by active process PID {existing_pid} "
                        f"(Worker: {existing_info.get('worker_id')}, Campaign: {existing_info.get('campaign_id')})."
                    )
                    return False
                else:
                    logger.info(f"Removing stale lock file from inactive PID {existing_pid}.")
                    try:
                        os.remove(self.lock_file_path)
                    except Exception as e:
                        logger.warning(f"Could not remove stale lockfile: {e}")

        # Attempt to open and lock file
        try:
            self._file_handle = open(self.lock_file_path, "a+b")
            self._file_handle.seek(0)

            if os.name == "nt":
                # Non-blocking lock 1 byte on Windows
                msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Successfully locked; write metadata
            now_iso = datetime.now(timezone.utc).isoformat()
            self._lock_metadata = {
                "pid": os.getpid(),
                "worker_id": worker_id,
                "campaign_id": campaign_id,
                "started_at": now_iso,
                "last_heartbeat": now_iso,
            }

            self._file_handle.seek(0)
            self._file_handle.truncate()
            self._file_handle.write(json.dumps(self._lock_metadata, indent=2).encode("utf-8"))
            self._file_handle.flush()

            self._locked = True

            # Update DB heartbeat
            self._update_db_heartbeat()

            logger.info(f"Acquired authoritative OS process lock (PID: {os.getpid()}, Worker: {worker_id}).")
            return True

        except (IOError, OSError) as e:
            logger.warning(f"Failed to acquire OS file lock: {e}")
            if self._file_handle:
                try:
                    self._file_handle.close()
                except Exception:
                    pass
                self._file_handle = None
            return False

    def update_heartbeat(self) -> None:
        """Updates the runner heartbeat in the file lock and database."""
        if not self._locked or not self._file_handle or not self._lock_metadata:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        self._lock_metadata["last_heartbeat"] = now_iso

        try:
            self._file_handle.seek(0)
            self._file_handle.truncate()
            self._file_handle.write(json.dumps(self._lock_metadata, indent=2).encode("utf-8"))
            self._file_handle.flush()
        except Exception as e:
            logger.warning(f"Failed to write heartbeat to file lock: {e}")

        self._update_db_heartbeat()

    def _update_db_heartbeat(self) -> None:
        """Updates the active runner heartbeat in AppSetting."""
        if not self.db or not self._lock_metadata:
            return

        try:
            payload = json.dumps(self._lock_metadata)
            setting = self.db.query(AppSetting).filter(AppSetting.key == self.DB_SETTING_KEY).first()
            if not setting:
                setting = AppSetting(
                    key=self.DB_SETTING_KEY,
                    value=payload,
                    description="Active Production Runner Heartbeat"
                )
                self.db.add(setting)
            else:
                setting.value = payload
            self.db.commit()
        except Exception as e:
            logger.warning(f"Failed to write DB runner heartbeat: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

    def release(self) -> None:
        """Releases the OS file lock and cleans up metadata."""
        if not self._locked:
            return

        # Clear DB heartbeat
        if self.db:
            try:
                setting = self.db.query(AppSetting).filter(AppSetting.key == self.DB_SETTING_KEY).first()
                if setting:
                    self.db.delete(setting)
                    self.db.commit()
            except Exception as e:
                logger.warning(f"Failed to clear DB runner heartbeat: {e}")

        # Release OS file lock
        if self._file_handle:
            try:
                self._file_handle.seek(0)
                if os.name == "nt":
                    try:
                        msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                else:
                    try:
                        fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                self._file_handle.close()
            except Exception as e:
                logger.warning(f"Error releasing file handle: {e}")
            self._file_handle = None

        # Remove lock file
        if os.path.exists(self.lock_file_path):
            try:
                os.remove(self.lock_file_path)
            except Exception as e:
                logger.warning(f"Could not delete lockfile on release: {e}")

        self._locked = False
        self._lock_metadata = None
        logger.info("Released OS process lock cleanly.")

    def read_lock_file(self) -> Optional[Dict[str, Any]]:
        """Reads metadata from the lock file if it exists."""
        if self._locked and self._lock_metadata:
            return dict(self._lock_metadata)
        if not os.path.exists(self.lock_file_path):
            return None
        try:
            with open(self.lock_file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        except Exception:
            pass
        return None

    def get_active_runner_info(self) -> Optional[Dict[str, Any]]:
        """
        Returns active runner information if an active runner process is alive.
        Returns None if no runner is running.
        """
        # First check authoritative file lock
        info = self.read_lock_file()
        if info and info.get("pid"):
            if is_pid_alive(info["pid"]):
                return info

        # Fallback to DB check for visibility
        if self.db:
            try:
                setting = self.db.query(AppSetting).filter(AppSetting.key == self.DB_SETTING_KEY).first()
                if setting and setting.value:
                    data = json.loads(setting.value)
                    # If lock file exists on local host, verify local OS PID
                    if os.path.exists(self.lock_file_path) and data.get("pid"):
                        if is_pid_alive(data["pid"]):
                            return data
                    else:
                        # On remote host / serverless: determine liveness by heartbeat timestamp
                        hb_str = data.get("last_heartbeat") or data.get("heartbeat_at") or data.get("started_at")
                        if hb_str:
                            now_utc = datetime.now(timezone.utc)
                            hb_dt = datetime.fromisoformat(hb_str.replace("Z", "+00:00"))
                            age = (now_utc - hb_dt).total_seconds()
                            if age <= 60:
                                return data
                        elif data.get("pid") and is_pid_alive(data["pid"]):
                            return data
            except Exception:
                pass

        return None
