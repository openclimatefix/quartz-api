"""Site time series, single-site and multi-site."""

import datetime as dt
from typing import Annotated
from uuid import UUID

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from ..cache import invalidate_company_site_cache, site_key_builder
from ..clearsky import clearsky_times, site_clearsky_kw
from ..endpoint_types import (
    CountryParam,
    GenerationValue,
    SiteClearskyMatrix,
    SiteClearskyResponse,
    SiteForecastMatrix,
    SiteForecastResponse,
    SiteForecastSnapshot,
    SiteGenerationInput,
    SiteGenerationMatrix,
    SiteGenerationResponse,
    SiteGenerationSnapshot,
    SitePowerSeries,
    SiteSeries,
    SiteSnapshotValue,
    ValidSource,
    ValidWindowEnd,
    ValidWindowStart,
)
from ..helpers import (
    check_country_access,
    latest_run_timestamps,
    timeseries_window,
    to_uuid,
    validate_window,
    window_chunks,
)
from ..sites_scoping import (
    get_site,
    list_owned_sites,
    resolve_site_forecaster,
    select_sites_for_period,
    site_config_for,
)

router = APIRouter(tags=["Sites"])


@router.get(
    "/{country}/{source}/sites/{site_id}/forecast",
    response_model=SiteForecastResponse,
    response_model_exclude_none=True,
)
@cache(key_builder=site_key_builder, namespace="sites", expire=60)
async def get_site_forecast(
    country: CountryParam,
    source: ValidSource,
    site_id: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_utc: ValidWindowStart = None,
    end_utc: ValidWindowEnd = None,
    horizon_minutes: Annotated[int | None, Query(ge=0)] = None,
    creation_limit_utc: Annotated[dt.datetime | None, Query()] = None,
) -> SiteForecastResponse:
    """Return the forecast for a site."""
    check_country_access(auth, country)

    # Set forecast window.
    now = pd.Timestamp.now(tz="UTC").floor("15min").to_pydatetime()
    window_start = start_utc or now
    window_end = end_utc or now + dt.timedelta(hours=48)
    validate_window(window_start, window_end)

    # Select site and resolve forecast model.
    site_cfg = site_config_for(country, source)
    site = await get_site(db, site_id, auth, source, str(country.code))
    forecaster_name = resolve_site_forecaster(site, site_cfg)

    # Fetch forecast values.
    values: list[models.PredictedGenerationValue] = []

    for chunk_start, chunk_end in window_chunks(window_start, window_end):
        values.extend(
            await db.get_predicted_generation(
                location_uuid=site.uuid,
                window_start=chunk_start,
                window_end=chunk_end,
                energy_type=source,
                location_type=models.LocationType.SITE,
                authdata=auth,
                created_cutoff=creation_limit_utc,
                forecast_horizon_minutes=horizon_minutes or 0,
                forecaster_name=forecaster_name,
            ),
        )

    # Build response metadata.
    first_value = values[0] if values else None
    last_updated, latest_init = latest_run_timestamps(values)

    return SiteForecastResponse(
        site_id=site.uuid,
        capacity_kW=site.capacity_kilowatts,
        model_name=forecaster_name,
        model_version=first_value.forecaster_version if first_value else None,
        last_updated_utc=last_updated,
        latest_init_utc=latest_init,
        values=[
            GenerationValue(
                time_utc=value.valid_timestamp,
                power_kW=value.power_kilowatts,
            )
            for value in values
        ],
    )


@router.get(
    "/{country}/{source}/sites/{site_id}/generation",
    response_model=SiteGenerationResponse,
    response_model_exclude_none=True,
)
@cache(key_builder=site_key_builder, namespace="sites", expire=60)
async def get_site_generation(
    country: CountryParam,
    source: ValidSource,
    site_id: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_utc: ValidWindowStart = None,
    end_utc: ValidWindowEnd = None,
) -> SiteGenerationResponse:
    """Return the generation a site has reported. Defaults to the last 24 hours."""
    check_country_access(auth, country)

    # Set generation window.
    now = pd.Timestamp.now(tz="UTC").floor("15min").to_pydatetime()
    window_end = end_utc or now
    window_start = start_utc or window_end - dt.timedelta(hours=24)
    validate_window(window_start, window_end)

    # Select site.
    site_cfg = site_config_for(country, source)
    site = await get_site(db, site_id, auth, source, str(country.code))

    # Fetch observed values.
    values: list[models.ActualGenerationValue] = []

    for chunk_start, chunk_end in window_chunks(window_start, window_end):
        values.extend(
            await db.get_actual_generation(
                location_uuid=site.uuid,
                window_start=chunk_start,
                window_end=chunk_end,
                energy_type=source,
                location_type=models.LocationType.SITE,
                authdata=auth,
                observer_name=site_cfg.observer_name,
            ),
        )

    return SiteGenerationResponse(
        site_id=site.uuid,
        capacity_kW=site.capacity_kilowatts,
        observer_name=site_cfg.observer_name,
        values=[
            GenerationValue(
                time_utc=value.valid_timestamp,
                power_kW=value.power_kilowatts,
            )
            for value in values
        ],
    )


@router.post(
    "/{country}/{source}/sites/{site_id}/generation",
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_site_generation(
    country: CountryParam,
    source: ValidSource,
    site_id: UUID,
    readings: list[SiteGenerationInput],
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> Response:
    """Upload generation readings for a site. Returns 202 with no body."""
    check_country_access(auth, country)

    # nothing to save
    if not readings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No values to save.",
        )

    # Select site.
    site_cfg = site_config_for(country, source)
    site = await get_site(db, site_id, auth, source, str(country.code))

    # save the readings. The dp rejects timestamps in the future.
    await db.put_actual_generation(
        generation_values=[
            models.ActualGenerationValue(
                power_kilowatts=reading.power_kW,
                valid_timestamp=reading.time_utc,
                location_uuid=site.uuid,
                capacity_kilowatts=site.capacity_kilowatts,
                observer_name=site_cfg.observer_name,
            )
            for reading in readings
        ],
        location_uuid=site.uuid,
        energy_type=source,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )

    await invalidate_company_site_cache(auth)

    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.get(
    "/{country}/{source}/sites/clearsky",
    response_model=SiteClearskyMatrix,
)
@cache(key_builder=site_key_builder, namespace="sites", expire=60)
async def get_sites_clearsky(
    country: CountryParam,
    source: ValidSource,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    site_ids: Annotated[list[UUID] | None, Query(max_length=10)] = None,
    start_utc: ValidWindowStart = None,
    end_utc: ValidWindowEnd = None,
) -> SiteClearskyMatrix:
    """Clear-sky estimates for several sites. Defaults to now (floored to 6h) ± 2 days."""
    check_country_access(auth, country)

    if source != models.EnergyType.SOLAR:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Clearsky is only available for solar sites.",
        )

    # Select sites.
    owned_sites = await list_owned_sites(db, auth, source, str(country.code))
    sites = select_sites_for_period(owned_sites, site_ids)

    times = clearsky_times(*timeseries_window(start_utc, end_utc))

    return SiteClearskyMatrix(
        times_utc=times,
        sites=[
            SitePowerSeries(
                site_id=site.uuid,
                capacity_kW=site.capacity_kilowatts,
                power_kW=site_clearsky_kw(site, times),
            )
            for site in sites
        ],
    )


@router.get(
    "/{country}/{source}/sites/{site_id}/clearsky",
    response_model=SiteClearskyResponse,
    response_model_exclude_none=True,
)
@cache(key_builder=site_key_builder, namespace="sites", expire=60)
async def get_site_clearsky(
    country: CountryParam,
    source: ValidSource,
    site_id: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_utc: ValidWindowStart = None,
    end_utc: ValidWindowEnd = None,
) -> SiteClearskyResponse:
    """Clear-sky generation estimate for one site, every 15 minutes. Defaults to now → +48h."""
    check_country_access(auth, country)

    if source != models.EnergyType.SOLAR:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Clearsky is only available for solar sites.",
        )

    # Select site.
    site = await get_site(db, site_id, auth, source, str(country.code))

    times = clearsky_times(start_utc, end_utc)
    power = site_clearsky_kw(site, times)

    return SiteClearskyResponse(
        site_id=site.uuid,
        capacity_kW=site.capacity_kilowatts,
        values=[
            GenerationValue(time_utc=time, power_kW=power_value)
            for time, power_value in zip(times, power, strict=True)
        ],
    )


@router.get(
    "/{country}/{source}/sites/forecasts/period",
    response_model=SiteForecastMatrix,
    response_model_exclude_none=True,
)
@cache(key_builder=site_key_builder, namespace="sites", expire=60)
async def get_sites_forecasts_period(
    country: CountryParam,
    source: ValidSource,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    site_ids: Annotated[list[UUID] | None, Query(max_length=10)] = None,
    start_utc: ValidWindowStart = None,
    end_utc: ValidWindowEnd = None,
    horizon_minutes: Annotated[int | None, Query(ge=0)] = None,
    creation_limit_utc: Annotated[dt.datetime | None, Query()] = None,
) -> SiteForecastMatrix:
    """Return forecasts for several sites on a shared time axis."""
    check_country_access(auth, country)

    # Set forecast window.
    window_start, window_end = timeseries_window(start_utc, end_utc)
    validate_window(window_start, window_end)

    # Select sites and resolve forecast model.
    site_cfg = site_config_for(country, source)
    owned_sites = await list_owned_sites(db, auth, source, str(country.code))
    sites = select_sites_for_period(owned_sites, site_ids)

    models_by_site: dict[str, list[str]] = {}

    for site in sites:
        forecaster_name = resolve_site_forecaster(site, site_cfg)
        models_by_site.setdefault(forecaster_name, []).append(str(site.uuid))

    if len(models_by_site) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "The selected sites do not all use the same forecast model "
                f"({', '.join(sorted(models_by_site))}), and this endpoint reports one model "
                "for the whole response. Request them in separate calls: "
                + "; ".join(
                    f"{model}: {', '.join(site_ids)}"
                    for model, site_ids in sorted(models_by_site.items())
                )
            ),
        )

    forecaster_name = next(iter(models_by_site), None)

    # Fetch forecast values.
    all_values: list[models.PredictedGenerationValue] = []
    per_site: list[tuple[models.Location, dict[dt.datetime, float]]] = []

    for site in sites:
        values: list[models.PredictedGenerationValue] = []

        for chunk_start, chunk_end in window_chunks(window_start, window_end):
            values.extend(
                await db.get_predicted_generation(
                    location_uuid=site.uuid,
                    window_start=chunk_start,
                    window_end=chunk_end,
                    energy_type=source,
                    location_type=models.LocationType.SITE,
                    authdata=auth,
                    created_cutoff=creation_limit_utc,
                    forecast_horizon_minutes=horizon_minutes or 0,
                    forecaster_name=forecaster_name,
                ),
            )

        all_values.extend(values)
        per_site.append(
            (
                site,
                {
                    value.valid_timestamp: value.power_kilowatts
                    for value in values
                },
            ),
        )

    # Align all sites on one time axis.
    times = sorted(
        {
            time
            for _, values_by_time in per_site
            for time in values_by_time
        },
    )

    series = [
        SiteSeries(
            site_id=site.uuid,
            capacity_kW=site.capacity_kilowatts,
            power_kW=[values_by_time.get(time) for time in times],
        )
        for site, values_by_time in per_site
    ]

    # Build response metadata.
    first_value = all_values[0] if all_values else None
    last_updated, latest_init = latest_run_timestamps(all_values)

    return SiteForecastMatrix(
        model_name=forecaster_name,
        model_version=first_value.forecaster_version if first_value else None,
        last_updated_utc=last_updated,
        latest_init_utc=latest_init,
        times_utc=times,
        sites=series,
    )


@router.get(
    "/{country}/{source}/sites/generation/period",
    response_model=SiteGenerationMatrix,
)
@cache(key_builder=site_key_builder, namespace="sites", expire=60)
async def get_sites_generation_period(
    country: CountryParam,
    source: ValidSource,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    site_ids: Annotated[list[UUID] | None, Query(max_length=10)] = None,
    start_utc: ValidWindowStart = None,
    end_utc: ValidWindowEnd = None,
) -> SiteGenerationMatrix:
    """Return generation for several sites on a shared time axis."""
    check_country_access(auth, country)

    # Set generation window.
    window_start, window_end = timeseries_window(start_utc, end_utc)
    validate_window(window_start, window_end)

    # Select sites.
    site_cfg = site_config_for(country, source)
    owned_sites = await list_owned_sites(db, auth, source, str(country.code))
    sites = select_sites_for_period(owned_sites, site_ids)

    # Fetch generation values.
    per_site: list[tuple[models.Location, dict[dt.datetime, float]]] = []

    for site in sites:
        values: list[models.ActualGenerationValue] = []

        for chunk_start, chunk_end in window_chunks(window_start, window_end):
            values.extend(
                await db.get_actual_generation(
                    location_uuid=site.uuid,
                    window_start=chunk_start,
                    window_end=chunk_end,
                    energy_type=source,
                    location_type=models.LocationType.SITE,
                    authdata=auth,
                    observer_name=site_cfg.observer_name,
                ),
            )

        per_site.append(
            (
                site,
                {
                    value.valid_timestamp: value.power_kilowatts
                    for value in values
                },
            ),
        )

    # Align all sites on one time axis.
    times = sorted(
        {
            time
            for _, values_by_time in per_site
            for time in values_by_time
        },
    )

    series = [
        SiteSeries(
            site_id=site.uuid,
            capacity_kW=site.capacity_kilowatts,
            power_kW=[values_by_time.get(time) for time in times],
        )
        for site, values_by_time in per_site
    ]

    return SiteGenerationMatrix(
        observer_name=site_cfg.observer_name,
        times_utc=times,
        sites=series,
    )


@router.get(
    "/{country}/{source}/sites/forecasts/snapshot",
    response_model=SiteForecastSnapshot,
    response_model_exclude_none=True,
)
@cache(key_builder=site_key_builder, namespace="sites", expire=60)
async def get_sites_forecasts_snapshot(
    country: CountryParam,
    source: ValidSource,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    time_utc: Annotated[dt.datetime | None, Query()] = None,
) -> SiteForecastSnapshot:
    """Return the forecast for every site at one point in time."""
    check_country_access(auth, country)

    # Set snapshot time.
    stamp = (
        pd.Timestamp(time_utc)
        if time_utc is not None
        else pd.Timestamp.now(tz="UTC")
    )

    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")

    snapshot_time = stamp.floor("15min").to_pydatetime()

    # Select sites and resolve forecast model.
    site_cfg = site_config_for(country, source)
    sites = await list_owned_sites(db, auth, source, str(country.code))

    if not sites:
        return SiteForecastSnapshot(
            time_utc=snapshot_time,
            values=[],
        )

    models_by_site: dict[str, list[str]] = {}

    for site in sites:
        forecaster_name = resolve_site_forecaster(site, site_cfg)
        models_by_site.setdefault(forecaster_name, []).append(str(site.uuid))

    if len(models_by_site) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "The selected sites do not all use the same forecast model "
                f"({', '.join(sorted(models_by_site))}), and this endpoint reports one model "
                "for the whole response. Request them in separate calls: "
                + "; ".join(
                    f"{model}: {', '.join(site_ids)}"
                    for model, site_ids in sorted(models_by_site.items())
                )
            ),
        )

    forecaster_name = next(iter(models_by_site))

    # Fetch forecast values.
    snapshot = await db.get_predicted_generation_snapshot(
        location_uuids=[site.uuid for site in sites],
        snapshot_timestamp_utc=snapshot_time,
        energy_type=source,
        authdata=auth,
        forecaster_name=forecaster_name,
    )

    # Build site values in the same order as the selected sites.
    value_by_id = {
        to_uuid(value.location_uuid): value
        for value in snapshot
    }

    values = [
        SiteSnapshotValue(
            site_id=site.uuid,
            capacity_kW=site.capacity_kilowatts,
            power_kW=value_by_id[to_uuid(site.uuid)].power_kilowatts,
        )
        for site in sites
        if to_uuid(site.uuid) in value_by_id
    ]

    # Build response metadata.
    first_value = snapshot[0] if snapshot else None
    last_updated, latest_init = latest_run_timestamps(snapshot)

    return SiteForecastSnapshot(
        time_utc=snapshot_time,
        model_name=forecaster_name,
        model_version=first_value.forecaster_version if first_value else None,
        last_updated_utc=last_updated,
        latest_init_utc=latest_init,
        values=values,
    )


@router.get(
    "/{country}/{source}/sites/generation/snapshot",
    response_model=SiteGenerationSnapshot,
)
@cache(key_builder=site_key_builder, namespace="sites", expire=60)
async def get_sites_generation_snapshot(
    country: CountryParam,
    source: ValidSource,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    time_utc: Annotated[dt.datetime | None, Query()] = None,
) -> SiteGenerationSnapshot:
    """Return generation for every site at one point in time."""
    check_country_access(auth, country)

    # Set snapshot time.
    stamp = (
        pd.Timestamp(time_utc)
        if time_utc is not None
        else pd.Timestamp.now(tz="UTC")
    )

    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")

    snapshot_time = stamp.floor("15min").to_pydatetime()

    # Select sites.
    site_cfg = site_config_for(country, source)
    sites = await list_owned_sites(db, auth, source, str(country.code))

    if not sites:
        return SiteGenerationSnapshot(
            time_utc=snapshot_time,
            observer_name=site_cfg.observer_name,
            values=[],
        )

    # Fetch generation values.
    snapshot = await db.get_actual_generation_snapshot(
        location_uuids=[site.uuid for site in sites],
        snapshot_timestamp_utc=snapshot_time,
        energy_type=source,
        authdata=auth,
        observer_name=site_cfg.observer_name,
    )

    # Build site values in the same order as the selected sites.
    value_by_id = {
        to_uuid(value.location_uuid): value
        for value in snapshot
    }

    values = [
        SiteSnapshotValue(
            site_id=site.uuid,
            capacity_kW=site.capacity_kilowatts,
            power_kW=value_by_id[to_uuid(site.uuid)].power_kilowatts,
        )
        for site in sites
        if to_uuid(site.uuid) in value_by_id
    ]

    return SiteGenerationSnapshot(
        time_utc=snapshot_time,
        observer_name=site_cfg.observer_name,
        values=values,
    )
