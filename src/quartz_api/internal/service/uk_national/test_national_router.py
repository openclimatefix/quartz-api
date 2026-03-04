""" Test for format metadata"""
import datetime as dt

from .national_router import format_metadata


def test_format_metadata():
    metadata = {
        "gsp_last_updated": dt.datetime(2023, 1, 1, 12, 0, 0, tzinfo=dt.UTC),
        "nwp_ukv_last_updated": dt.datetime(2023, 1, 1, 12, 0, 0, tzinfo=dt.UTC),
        "nwp_ecmwf_last_updated": dt.datetime(2023, 1, 1, 12, 0, 0, tzinfo=dt.UTC),
    }
    result = format_metadata(metadata)
    assert result.gsp == dt.datetime(2023, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
    assert result.nwp == dt.datetime(2023, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
    assert result.satellite == dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

def test_format_metadata_no_nwp():
    metadata = {
        "gsp_last_updated": dt.datetime(2023, 1, 1, 12, 0, 0, tzinfo=dt.UTC),
        "app_version": "1.2.3",
    }
    result = format_metadata(metadata)
    assert result.gsp == dt.datetime(2023, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
    assert result.nwp == dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    assert result.satellite == dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
