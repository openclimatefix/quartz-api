"""Function to format metadata."""
import datetime as dt

from quartz_api.internal.service.uk_national.endpoint_types import InputDataLastUpdated


def format_metadata(metadata: dict) -> InputDataLastUpdated:
    """Format metadata dictionary into InputDataLastUpdated object."""
    old = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    input_data_dict = {"gsp": old, "nwp": old, "satellite": old}

    # we dont want the API to fall over for this, so lets be defensive
    try:
        # Note there can be
        # two satellite keys, the 9 degree and the 0 degree and the nwp keys
        # could be nwp_ukv_last_updated, nwp_ecwmwf_last_updated, or nwp_last_updated

        for name in ["gsp", "nwp", "satellite"]:
            for key in [k for k in metadata if name in k]:
                value = metadata.get(key)
                if isinstance(value, str):
                    value = dt.datetime.fromisoformat(value)
                if value.tzinfo is None:
                    value = value.replace(tzinfo=dt.UTC)
                input_data_dict[name] = value

        return InputDataLastUpdated(**input_data_dict, pv=old)
    except Exception as _:
        return InputDataLastUpdated(**input_data_dict, pv=old)
