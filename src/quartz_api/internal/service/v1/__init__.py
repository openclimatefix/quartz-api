"""
Renewable generation forecasts and observed data across multiple countries.
<br/>

## Resources

### Discovery
List available energy sources, countries, region types, and
generation sources per country, as well as browse locations by type (national, GSP, province, …).
Each region carries a `name`, which is what every other endpoint identifies it by.

### Forecasts
Per-region time series, filterable all-region snapshots for a point in time,
and a limited, filterable matrix of all-region data over a rolling ±2-day window.

### Generation
Observed PV output via the same three shapes (per-region, snapshot, and period matrix).

## Authentication

All endpoints require a **Bearer token** issued via Auth0. Click `Authorize`
at the top of this page to log in interactively; your token is then injected
into every request you send from here.

For programmatic access (scripts, integrations), see the
[OCF Energy API authentication guide](https://www.notion.so/openclimatefix/API-Access-2d8d2f64215d4432be830cbcc9220012)
for copy-paste examples in Python, curl, and JavaScript.

A free trial is available. Sign up through the `Authorize` button at the top or on any
request in these docs, or at [app.ocf.energy](https://app.ocf.energy) to get started.

## Rate limits

Requests are limited to **20 per second** and **3,600 per hour**, counted per user and
per route. Over either limit the response is a **429** carrying a `Retry-After` header
with the number of seconds to wait.

## Units & Conventions

- Power values are in **kW** throughout.
- All timestamps are **UTC ISO 8601** (`2020-01-01T12:00:00Z`).
- Regions are identified by **name** (case-insensitive). Browse them with the
  `/regions` endpoints, which is where every name in this API comes from. Responses
  identify regions by name and never return a UUID. The platform's own UUIDs are also
  accepted anywhere a name is, for callers who already hold one.
"""

from .router import router
