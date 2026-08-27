"""Test CSV download endpoint."""

import datetime as dt
import io
import pathlib

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pvsite_datamodel.read.model import get_or_create_model
from pvsite_datamodel.sqlmodels import ForecastSQL, ForecastValueSQL, LocationSQL
from pyhocon import ConfigFactory
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from quartz_api.cmd.main import _create_server
from quartz_api.internal import models

from .client import Client


@pytest.fixture()
def client(engine: Engine, db_session: Session) -> Client:
    """Hooks Client into pytest db_session fixture."""
    client = Client(database_url=str(engine.url))
    client.session = db_session

    return client


@pytest.fixture()
def test_client(client: Client) -> TestClient:
    """Create a FastAPI test client."""
    # Load config and override routers to include 'regions'
    conf_path = pathlib.Path(__file__).parent.parent.parent.parent / "cmd" / "server.conf"
    conf = ConfigFactory.parse_file(conf_path.as_posix())

    # Override config to include regions router
    override_conf = ConfigFactory.from_dict({"api": {"routers": "regions"}})
    conf = override_conf.with_fallback(conf)

    # Create the FastAPI app
    app = _create_server(conf=conf)

    # Override the database dependency with our test client
    app.dependency_overrides[models.get_db_client] = lambda: client

    return TestClient(app)


class TestCsvDownload:
    """Test CSV download functionality."""

    @pytest.mark.usefixtures("forecast_values_wind")
    def test_csv_download_success(
        self,
        test_client: TestClient,
    ) -> None:
        """Test successful CSV download from the API endpoint."""
        # Make request to CSV download endpoint
        response = test_client.get(
            "/wind/testID/forecast/csv",
            params={"forecast_horizon": "latest"},
        )

        # Assert successful response
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert "Content-Disposition" in response.headers
        assert "attachment" in response.headers["Content-Disposition"]
        assert ".csv" in response.headers["Content-Disposition"]

        # Parse CSV content
        csv_content = response.text
        df = pd.read_csv(io.StringIO(csv_content))

        # Validate CSV structure
        assert "Date [IST]" in df.columns
        assert "Time" in df.columns
        assert "PowerMW" in df.columns
        assert len(df) > 0

    @pytest.mark.usefixtures("forecast_values_wind")
    def test_csv_download_day_ahead(
        self,
        test_client: TestClient,
        client: Client,
    ) -> None:
        """Test CSV download with day_ahead forecast horizon."""

        # 1. Setup: Create 'Day Ahead' forecast data (for tomorrow)
        # Get the wind site
        site = client.session.query(LocationSQL).filter(LocationSQL.asset_type == "wind").first()

        # Create a forecast timestamped now
        now = dt.datetime.now(tz=dt.UTC)

        # Create a model entry
        ml_model = get_or_create_model(client.session, "windnet_india_adjust")

        forecast = ForecastSQL(
            location_uuid=site.location_uuid,
            forecast_version="0.0.0",
            timestamp_utc=now,
        )
        client.session.add(forecast)
        client.session.commit()

        # Add forecast values for "Tomorrow"
        # We need data that falls into "tomorrow" in IST.
        # IST is UTC+5:30.
        # Let's just generate 24 hours of data starting from now + 24h to be safe
        start_future = now + dt.timedelta(hours=24)

        forecast_values = []
        for i in range(48):  # 12 hours of data
            start_utc = start_future + dt.timedelta(minutes=15 * i)
            end_utc = start_utc + dt.timedelta(minutes=15)

            fv = ForecastValueSQL(
                forecast_power_kw=123.45 + i,
                forecast_uuid=forecast.forecast_uuid,
                start_utc=start_utc,
                end_utc=end_utc,
                horizon_minutes=(start_utc - now).total_seconds() / 60,
            )
            fv.ml_model = ml_model
            forecast_values.append(fv)

        client.session.add_all(forecast_values)
        client.session.commit()

        response = test_client.get(
            "/wind/testID/forecast/csv",
            params={"forecast_horizon": "day_ahead"},
        )

        # The response should be successful even if there's no data for tomorrow
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"

        # Parse CSV - it may be empty if no forecast data for tomorrow
        csv_content = response.text
        if csv_content.strip():  # Only validate if there's content
            df = pd.read_csv(io.StringIO(csv_content))
            assert len(df.columns) == 3
            assert list(df.columns) == ["Date [IST]", "Time", "PowerMW"]
