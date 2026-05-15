"""The Quartz API provides renewable generation forecasts and observed
data across multiple countries, powered by Open Climate Fix.

## Resources

- **Discovery** — list available energy sources, countries, region types, and
  generation sources per country, as well as browse locations by type (national, GSP, province, …).
  Each region carries a `region_name` identifier used across all other endpoints.
- **Forecasts** — per-region time series, filterable all-region snapshots for a point in time,
  and a limited, filterable matrix of all-region data over a rolling ±2-day window.
- **Generation** — observed PV output via the same three shapes: per-region,
  snapshot, and period matrix.

## Authentication

All endpoints require a **Bearer token** issued via Auth0. Click `Authorize`
at the top of this page to log in interactively — your token is then injected
into every request you send from here.

For programmatic access (scripts, integrations), see the
[Quartz API authentication guide](https://www.notion.so/openclimatefix/API-Access-2d8d2f64215d4432be830cbcc9220012)
for copy-paste examples in Python, curl, and JavaScript.

A free trial is available — sign up through the `Authorize` button at the top or on any
request in these docs, or at [app.quartz.solar](https://app.quartz.solar) to get started.

## Units & conventions

- Power values are in **kW** throughout.
- All timestamps are **UTC ISO 8601** (`2020-01-01T12:00:00Z`).
- Region IDs are unique **strings** — obtain them from the `/regions` endpoints.
"""

from .router import router
