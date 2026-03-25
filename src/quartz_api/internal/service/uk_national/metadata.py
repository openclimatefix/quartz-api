"""Function to format metadata."""
import datetime as dt

from quartz_api.internal.service.uk_national.endpoint_types import InputDataLastUpdated


def format_metadata(metadata: dict) -> InputDataLastUpdated:
    """Format metadata dictionary into InputDataLastUpdated object."""
    old = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    gsp = metadata.get("gsp_last_updated", old)

    # there can be two satellite keys, the 9 degree and the 0 degree
    satellite = old
    for satellite_key in [k for k in metadata if "satellite" in k]:
        satellite = max([metadata.get(satellite_key, old)])

    # the nwp keys could be nwp_ukv_last_updated, nwp_ecwmwf_last_updated, or nwp_last_updated
    nwp = old
    for nwp_key in [k for k in metadata if "nwp" in k]:
        nwp = max([metadata.get(nwp_key, old)])
    return InputDataLastUpdated(gsp=gsp, nwp=nwp, pv=old, satellite=satellite)
