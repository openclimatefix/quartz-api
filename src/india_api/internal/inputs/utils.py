import datetime as dt
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

def get_window() -> tuple[dt.datetime, dt.datetime]:
    """Returns the start and end of the window for timeseries data.
    
    Window start is the beginning of the day two days ago.
    Window end is the beginning of the day two days ahead.
    """
    # Using UTC for current window calculation
    start = (dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    end = (dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return (start, end)

def is_submission_valid(forecast_target: datetime, submission_time: datetime) -> bool:
    """
    Validates whether a forecast submission is made on time.
    
    For a forecast targeting a specific datetime (forecast_target) in IST,
    the submission must be made before 9:00 AM IST on the day prior to the forecast date.
    
    Returns True if submission_time is before the deadline, False otherwise.
    """
    ist = ZoneInfo("Asia/Kolkata")
    # Ensure both times are in IST
    forecast_target_ist = forecast_target.astimezone(ist)
    submission_time_ist = submission_time.astimezone(ist)
    
    # Deadline is set to 9:00 AM IST on the day before the forecast target date.
    deadline_date = forecast_target_ist.date() - timedelta(days=1)
    deadline = datetime.combine(deadline_date, time(9, 0, 0), tzinfo=ist)
    
    return submission_time_ist < deadline
