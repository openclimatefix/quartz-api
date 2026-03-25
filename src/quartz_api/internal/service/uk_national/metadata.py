"""Function to format metadata."""
import datetime as dt

from quartz_api.internal.service.uk_national.endpoint_types import InputDataLastUpdated


def format_metadata(metadata: dict) -> InputDataLastUpdated:
    """Format metadata dictionary into InputDataLastUpdated object."""
    old = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

    # we dont want the API to fall over for this, so lets be defensive
    try:
        if "gsp_last_updated" in metadata:
            gsp = metadata.get("gsp_last_updated")
            if isinstance(gsp, str):
                gsp = dt.datetime.fromisoformat(gsp)
            if gsp.tzinfo is None:
                gsp = gsp.replace(tzinfo=dt.UTC)
        else:
            gsp = old

        # there can be two satellite keys, the 9 degree and the 0 degree
        satellite = old
        for satellite_key in [k for k in metadata if "satellite" in k]:
            new_satellite = metadata.get(satellite_key)
            if isinstance(new_satellite, str):
                new_satellite = dt.datetime.fromisoformat(new_satellite)
            if new_satellite.tzinfo is None:
                new_satellite = new_satellite.replace(tzinfo=dt.UTC)
            satellite = max([new_satellite, satellite])

        # the nwp keys could be nwp_ukv_last_updated, nwp_ecwmwf_last_updated, or nwp_last_updated
        nwp = old
        for nwp_key in [k for k in metadata if "nwp" in k]:
            new_nwp = metadata.get(nwp_key)
            if isinstance(new_nwp, str):
                new_nwp = dt.datetime.fromisoformat(new_nwp)
            if new_nwp.tzinfo is None:
                new_nwp = new_nwp.replace(tzinfo=dt.UTC)
            nwp = max([new_nwp, nwp])

        return InputDataLastUpdated(gsp=gsp, nwp=nwp, pv=old, satellite=satellite)
    except Exception as _:
        return InputDataLastUpdated(gsp=old, nwp=old, pv=old, satellite=old)
