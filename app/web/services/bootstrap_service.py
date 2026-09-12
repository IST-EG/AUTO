"""
First-Run OWNER Bootstrap Service.

Provides a safe, atomic mechanism to create the initial OWNER account.
Implements a SQLite-safe constraint-based concurrency guard to guarantee
that concurrent setup requests cannot create duplicate owners or corrupt state.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

from app.models.user import User, UserRole
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.web.security.hasher import default_password_hasher, validate_password_policy


class BootstrapService:
    """Service orchestrating first-run system initialization."""

    LOCK_SETTING_KEY = "system_bootstrap_owner_lock"

    @classmethod
    def is_setup_available(cls, db: Session) -> bool:
        """
        Checks whether first-run setup is available.
        Returns True ONLY if zero users exist in the system.
        """
        user_count = db.query(func.count(User.id)).scalar() or 0
        if user_count > 0:
            return False

        # Also verify sentinel setting has not been committed
        lock_setting = db.query(AppSetting).filter(AppSetting.key == cls.LOCK_SETTING_KEY).first()
        if lock_setting:
            return False

        return True

    @classmethod
    def bootstrap_owner(
        cls,
        db: Session,
        username: str,
        email: str,
        password: str,
        ip_address: str = "127.0.0.1"
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Executes atomic first-run OWNER account creation.

        Concurrency & Safety Strategy (SQLite & PostgreSQL Compatible):
            1. Pre-check: Ensure setup is currently available.
            2. Password Policy: Validate complexity (>= 12 chars, upper, lower, digit, symbol).
            3. Atomic Sentinel: Inserts a unique AppSetting sentinel ("system_bootstrap_owner_lock").
               Because `app_settings.key` has a strict UNIQUE constraint, concurrent attempts
               will trigger an IntegrityError at the database level, guaranteeing exactly-once execution.
            4. User Creation: Inserts the initial User record with role=OWNER.
            5. Audit Trail: Emits SYSTEM_BOOTSTRAP_OWNER_CREATED record in audit_logs.
        """
        norm_user = (username or "").strip().lower()
        norm_email = (email or "").strip().lower()

        if not norm_user:
            return False, "Username is required.", {}
        if not norm_email:
            return False, "Email is required.", {}

        # 1. Validate password policy
        valid_pwd, pwd_msg = validate_password_policy(password)
        if not valid_pwd:
            return False, pwd_msg, {}

        try:
            # 2. Check if setup is already complete
            if not cls.is_setup_available(db):
                return False, "First-run setup has already been completed.", {}

            now = datetime.now(timezone.utc)
            owner_id = str(uuid.uuid4())

            # 3. Insert sentinel lock into app_settings (atomic uniqueness enforcement)
            sentinel = AppSetting(
                key=cls.LOCK_SETTING_KEY,
                value=owner_id,
                description="Atomic lock sentinel for first-run OWNER bootstrap",
                updated_at=now
            )
            db.add(sentinel)
            db.flush()

            # 4. Hash password and create OWNER user
            password_hash = default_password_hasher.hash(password)
            owner = User(
                id=owner_id,
                username=norm_user,
                email=norm_email,
                password_hash=password_hash,
                role=UserRole.OWNER.value,
                is_active=True,
                created_at=now,
                updated_at=now
            )
            db.add(owner)
            db.flush()

            # 5. Record audit log
            audit = AuditLog(
                event_type="SYSTEM_BOOTSTRAP_OWNER_CREATED",
                status="SUCCESS",
                result=json.dumps({
                    "actor": norm_user,
                    "user_id": owner_id,
                    "username": norm_user,
                    "email": norm_email,
                    "role": UserRole.OWNER.value,
                    "ip_address": ip_address
                }),
                created_at=now
            )
            db.add(audit)

            # Commit the entire atomic setup transaction
            db.commit()

            return True, "Initial OWNER account created successfully.", {
                "id": owner.id,
                "username": owner.username,
                "email": owner.email,
                "role": owner.role,
                "created_at": owner.created_at.isoformat()
            }

        except IntegrityError:
            try:
                db.rollback()
            except Exception:
                pass
            return False, "First-run setup conflict: an OWNER account was already created concurrently.", {}
        except Exception as ex:
            try:
                db.rollback()
            except Exception:
                pass
            return False, f"Setup failed ({type(ex).__name__}): {str(ex)}", {}
