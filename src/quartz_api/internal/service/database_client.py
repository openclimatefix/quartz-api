from typing import Annotated

from fastapi import Depends

from quartz_api.internal import DatabaseInterface


def get_db_client() -> DatabaseInterface:
    """Dependency injection for the database client."""
    return DatabaseInterface()


DBClientDependency = Annotated[DatabaseInterface, Depends(get_db_client)]
