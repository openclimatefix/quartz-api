import pytest


@pytest.mark.asyncio(loop_scope="session")
async def test_app_start(
    api_client_dataplatform,
    gsp_locations,  # noqa: ARG001
    make_forecasters,  # noqa: ARG001
    make_national_forecast_values,  # noqa: ARG001
) -> None:
    """Ensures FastAPI boots and can successfully talk to the real Docker backend."""
    # Check Health
    response = await api_client_dataplatform.get("/health")

