"""
Timezone and calendar boundary helper.

Anchors all calendar semantics to APP_TIMEZONE (default: Africa/Cairo) and converts
boundaries to UTC half-open intervals [start_utc, end_utc) for database queries.
"""

from datetime import datetime, date, timedelta, timezone
from typing import Tuple, Optional, Union, NamedTuple
import pytz

from app.utils.settings import settings


class CalendarRange(NamedTuple):
    """Encapsulates resolved calendar range bounds in UTC and local timezone string representations."""
    start_utc: datetime
    end_utc: datetime
    preset: str
    timezone_name: str
    start_local_str: str
    end_local_str: str


def get_app_timezone() -> pytz.BaseTzInfo:
    """Returns the authoritative application timezone."""
    tz_name = getattr(settings, "APP_TIMEZONE", "Africa/Cairo")
    try:
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.timezone("Africa/Cairo")


def resolve_calendar_range(
    preset: Optional[str] = None,
    start_date: Optional[Union[str, date, datetime]] = None,
    end_date: Optional[Union[str, date, datetime]] = None,
    start_date_str: Optional[str] = None,
    end_date_str: Optional[str] = None,
) -> CalendarRange:
    """
    Resolves calendar boundaries in APP_TIMEZONE, returning half-open UTC interval [start_utc, end_utc)
    and the normalized preset string.

    Preset options:
    - 'today': Current calendar day in APP_TIMEZONE (00:00:00 to 23:59:59.999999).
    - 'yesterday': Previous calendar day in APP_TIMEZONE.
    - 'last_7_days': Past 6 days + current calendar day (7 calendar days total).
    - 'last_30_days': Past 29 days + current calendar day (30 calendar days total).
    - 'custom': User-specified start_date to end_date (inclusive calendar days).
    """
    if start_date is None and start_date_str is not None:
        start_date = start_date_str
    if end_date is None and end_date_str is not None:
        end_date = end_date_str

    tz = get_app_timezone()
    tz_name = getattr(settings, "APP_TIMEZONE", "Africa/Cairo")
    now_local = datetime.now(tz)
    today_local = now_local.date()

    norm_preset = (preset or "").strip().lower() if preset else ""

    # Parse custom dates if provided
    if start_date and end_date and (norm_preset == "custom" or not norm_preset):
        try:
            if isinstance(start_date, str):
                s_dt = datetime.strptime(start_date.strip()[:10], "%Y-%m-%d").date()
            elif isinstance(start_date, datetime):
                s_dt = start_date.date()
            else:
                s_dt = start_date

            if isinstance(end_date, str):
                e_dt = datetime.strptime(end_date.strip()[:10], "%Y-%m-%d").date()
            elif isinstance(end_date, datetime):
                e_dt = end_date.date()
            else:
                e_dt = end_date

            if s_dt > e_dt:
                s_dt, e_dt = e_dt, s_dt

            start_local_dt = tz.localize(datetime(s_dt.year, s_dt.month, s_dt.day, 0, 0, 0))
            end_local_dt = tz.localize(datetime(e_dt.year, e_dt.month, e_dt.day, 0, 0, 0)) + timedelta(days=1)

            start_utc = start_local_dt.astimezone(pytz.UTC)
            end_utc = end_local_dt.astimezone(pytz.UTC)
            return CalendarRange(
                start_utc=start_utc,
                end_utc=end_utc,
                preset="custom",
                timezone_name=tz_name,
                start_local_str=start_local_dt.strftime("%Y-%m-%d %H:%M:%S"),
                end_local_str=end_local_dt.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception:
            # Fall back to preset
            pass

    start_today_local = tz.localize(datetime(today_local.year, today_local.month, today_local.day, 0, 0, 0))
    end_today_local = start_today_local + timedelta(days=1)

    if norm_preset == "today":
        start_local = start_today_local
        end_local = end_today_local
        resolved = "today"
    elif norm_preset == "yesterday":
        start_local = start_today_local - timedelta(days=1)
        end_local = start_today_local
        resolved = "yesterday"
    elif norm_preset == "last_30_days":
        start_local = start_today_local - timedelta(days=29)
        end_local = end_today_local
        resolved = "last_30_days"
    else:
        # Default: last_7_days
        start_local = start_today_local - timedelta(days=6)
        end_local = end_today_local
        resolved = "last_7_days"

    start_utc = start_local.astimezone(pytz.UTC)
    end_utc = end_local.astimezone(pytz.UTC)

    return CalendarRange(
        start_utc=start_utc,
        end_utc=end_utc,
        preset=resolved,
        timezone_name=tz_name,
        start_local_str=start_local.strftime("%Y-%m-%d %H:%M:%S"),
        end_local_str=end_local.strftime("%Y-%m-%d %H:%M:%S"),
    )
