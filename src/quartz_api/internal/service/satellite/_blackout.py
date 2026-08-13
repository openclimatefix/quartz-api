"""Sun times for the visible satellite channels' nighttime blackout.

The visible channels carry no signal once the region is dark, so they get zeroed
out at night. Rather than fixed clock times, the window comes from sunrise and
sunset at a reference point, so it follows the seasons: a timestamp is dark when
it falls outside the day's sunrise-to-sunset window, widened by a buffer at each
end and rounded out to a whole hour.

    first_light, last_light = apply_buffer(*sun_times(ts.date(), lon, lat))
    dark = not first_light <= ts < last_light

Sun times are looked up per UTC day, which assumes the reference point is close
enough to the prime meridian for a day's sunrise and sunset to share a UTC date.
That holds for the UK, and `sunrise` raises for a point where the sun never
crosses the horizon at all, which is outside the UK bounding box.
"""

import datetime as dt

from astral import Observer
from astral.sun import sunrise, sunset

# Extend the daylight window by 1 hour before sunrise and after sunset.
# For example, if sunrise is at 06:00 and sunset is at 18:00,
# the resulting daylight window is 05:00 to 19:00.
BLACKOUT_BUFFER = dt.timedelta(hours=1)


def sun_times(day: dt.date, lon: float, lat: float) -> tuple[dt.datetime, dt.datetime]:
    """Sunrise and sunset at a point on a given day.

    Args:
        day: UTC day to compute for.
        lon: Longitude in WGS84 degrees.
        lat: Latitude in WGS84 degrees.

    Returns:
        Tuple of (sunrise, sunset) as UTC datetimes.
    """
    observer = Observer(latitude=lat, longitude=lon)
    return (
        sunrise(observer, date=day, tzinfo=dt.UTC),
        sunset(observer, date=day, tzinfo=dt.UTC),
    )


def apply_buffer(rise: dt.datetime, set_: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """Widen a sunrise and sunset by the buffer, rounded out to whole hours.

    Both ends round away from midday, so the window only ever grows: the sunrise
    rounds down to the hour and the sunset up, and anything outside the result is
    dark by at least the buffer.

    Args:
        rise: Sunrise to pull earlier.
        set_: Sunset to push later.

    Returns:
        Tuple of (first light, last light) as UTC datetimes, on whole hours.
    """
    first_light = (rise - BLACKOUT_BUFFER).replace(minute=0, second=0, microsecond=0)

    last_light = set_ + BLACKOUT_BUFFER
    on_the_hour = last_light.replace(minute=0, second=0, microsecond=0)
    if last_light > on_the_hour:
        on_the_hour += dt.timedelta(hours=1)

    return first_light, on_the_hour
