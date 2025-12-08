"""Test fixtures to set up fake database for testing."""

import datetime as dt
import logging
from collections.abc import Generator

import pytest
from pvsite_datamodel.read.model import get_or_create_model
from pvsite_datamodel.read.user import get_user_by_email
from pvsite_datamodel.sqlmodels import (
    Base,
    ForecastSQL,
    ForecastValueSQL,
    GenerationSQL,
    LocationSQL,
)
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

log = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def engine() -> Generator[Engine]:
    """Database engine fixture."""
    with PostgresContainer("postgres:14.5") as postgres:
        url = postgres.get_connection_url()
        engine = create_engine(url)

        yield engine


@pytest.fixture(scope="session")
def tables(engine: Engine) -> Generator[None]:
    """Create tables fixture."""
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db_session(
    engine: Engine,
    tables: None,  # noqa: ARG001
) -> Generator[Session]:
    """Return a sqlalchemy session, which tears down everything properly post-test."""
    connection = engine.connect()
    # begin the nested transaction
    transaction = connection.begin()
    # use the connection with the already started transaction
    session = Session(bind=connection)

    yield session

    session.close()
    # roll back the broader transaction
    transaction.rollback()
    # put back the connection to the connection pool
    connection.close()


@pytest.fixture()
def sites(db_session: Session) -> list[LocationSQL]:
    """Seed some initial data into DB."""
    sites = []
    # PV site
    site = LocationSQL(
        client_location_id=1,
        latitude=20.59,
        longitude=78.96,
        capacity_kw=4,
        ml_id=1,
        asset_type="pv",
        country="india",
        region="testID",
        client_location_name="ruvnl_pv_testID1",
    )
    db_session.add(site)
    sites.append(site)

    # Wind site
    site = LocationSQL(
        client_location_id=2,
        latitude=20.59,
        longitude=78.96,
        capacity_kw=4,
        ml_id=2,
        asset_type="wind",
        country="india",
        region="testID",
        client_location_name="ruvnl_wind_testID",
    )
    db_session.add(site)
    sites.append(site)

    db_session.commit()

    # create user
    user = get_user_by_email(session=db_session, email="test@test.com")
    user.location_group.locations = sites

    db_session.commit()

    return sites


@pytest.fixture()
def generations(
    db_session: Session,
    sites: list[LocationSQL],
) -> list[GenerationSQL]:
    """Create some fake generations."""
    start_times = [
        dt.datetime.now(tz=dt.UTC).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        - dt.timedelta(minutes=x)
        for x in range(10)
    ]
    all_generations = []

    for site in sites:
        for i in range(0, 10):
            generation = GenerationSQL(
                location_uuid=site.location_uuid,
                generation_power_kw=i,
                start_utc=start_times[i],
                end_utc=start_times[i] + dt.timedelta(minutes=5),
            )
            all_generations.append(generation)

    db_session.add_all(all_generations)
    db_session.commit()

    return all_generations


@pytest.fixture()
def forecast_values(
    db_session: Session,
    sites: list[LocationSQL],
) -> None:
    """Create some fake forecast values."""
    make_fake_forecast_values(db_session, sites, "pvnet_india")


@pytest.fixture()
def forecast_values_wind(
    db_session: Session,
    sites: list[LocationSQL],
) -> None:
    """Create some fake forecast values."""
    make_fake_forecast_values(db_session, sites, "windnet_india_adjust")


@pytest.fixture()
def forecast_values_site(
    db_session: Session,
    sites: list[LocationSQL],
) -> None:
    """Create some fake forecast values."""
    make_fake_forecast_values(db_session, sites, "pvnet_ad_sites")


def make_fake_forecast_values(
    db_session: Session,
    sites: list[LocationSQL],
    model_name: str,
) -> list[ForecastValueSQL]:
    """Create some fake forecast values."""
    forecast_values = []
    forecast_version: str = "0.0.0"

    num_forecasts = 10
    num_values_per_forecast = 11

    timestamps = [
        dt.datetime.now().astimezone(dt.UTC) - dt.timedelta(minutes=10 * i)
        for i in range(num_forecasts)
    ]

    # To make things trickier we make a second forecast at the same for one of the timestamps.
    timestamps = timestamps + timestamps[-1:]

    # get model
    ml_model = get_or_create_model(db_session, model_name)

    for site in sites:
        for timestamp in timestamps:
            forecast: ForecastSQL = ForecastSQL(
                location_uuid=site.location_uuid,
                forecast_version=forecast_version,
                timestamp_utc=timestamp,
            )

            db_session.add(forecast)
            db_session.commit()

            for i in range(num_values_per_forecast):
                # Forecasts of 15 minutes.
                duration = 15
                horizon = duration * i
                forecast_value: ForecastValueSQL = ForecastValueSQL(
                    forecast_power_kw=i,
                    forecast_uuid=forecast.forecast_uuid,
                    start_utc=timestamp + dt.timedelta(minutes=horizon),
                    end_utc=timestamp + dt.timedelta(minutes=horizon + duration),
                    horizon_minutes=horizon,
                )
                forecast_value.ml_model = ml_model
                site.ml_model = ml_model

                forecast_values.append(forecast_value)

    db_session.add_all(forecast_values)
    db_session.commit()

    return forecast_values
