"""Config struct for application running."""
import os
from distutils.util import strtobool
from typing import get_type_hints

import structlog

log = structlog.getLogger()


class EnvParser:
    """Mixin to parse environment variables into class fields.

    Whilst this could be done with Pydantic, it's nice to avoid the
    extra dependency if possible, and pydantic would be overkill for
    this small use case.
    """

    def __init__(self) -> None:
        """Parse environment variables into class fields.

        If the class field is upper case, parse it into the indicated
        type from the environment. Required fields are those set in
        the child class without a default value.

        Examples:
        >>> MyEnv(EnvParser):
        >>>     REQUIRED_ENV_VAR: str
        >>>     OPTIONAL_ENV_VAR: str = "default value"
        >>>     ignored_var: str = "ignored"
        """
        for field, t in get_type_hints(self).items():
            # Skip item if not upper case
            if not field.isupper():
                continue

            # Log Error if required field not supplied
            default_value = getattr(self, field, None)
            match (default_value, os.environ.get(field)):
                case (None, None):
                    # No default value, and field not in env
                    raise OSError(f"Required field {field} not supplied")
                case (_, None):
                    # A default value is set and field not in env
                    pass
                case (_, _):
                    # Field is in env
                    env_value: str | bool = os.environ[field]
                    # Handle bools seperately as bool("False") == True
                    if t == bool:
                        env_value = bool(strtobool(os.environ[field]))
                    # Cast to desired type
                    self.__setattr__(field, t(env_value))


class Config(EnvParser):
    """Config for the application."""

    SOURCE: str = "quartzdb"
    DB_URL: str = ""
    PORT: int = 8000
    AUTH0_DOMAIN: str = ""
    AUTH0_API_AUDIENCE: str = ""
