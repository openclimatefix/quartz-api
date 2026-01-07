"""# Substation-Level Forecasts

This API provides access to substation-level solar forecasts. There are three main routes
- `/substations/`: to get a list of all substations
- `/substations/{substation_uuid}`: to get metadata about a specific substation
- `/substations/{substation_uuid}/forecast`: to get the latest forecasts for a specific substation

"""

from .router import router
