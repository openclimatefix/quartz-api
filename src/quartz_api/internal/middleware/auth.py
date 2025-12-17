"""Authentication dependency for FastAPI using Auth0 JWT tokens."""

from typing import Annotated

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer
from fastapi_auth0 import Auth0User

token_auth_scheme = HTTPBearer()

EMAIL_KEY = "https://openclimatefix.org/email"


class DummyAuth:
    """Dummy auth dependency for testing purposes."""

    def __call__(self) -> dict[str, str]:
        """Return a dummy authentication payload."""
        return {
            EMAIL_KEY: "test@test.com",
            "sub": "google-oath2|012345678909876543210",
        }

    def get_user(self) -> Auth0User:
        """Return a dummy user payload."""
        return Auth0User(**{
            "email": "test@test.com",
            "id": "012345678909876543210",
        })


def get_user() -> Auth0User:
    """Get the authentication payload.

    Note: This should be overridden via FastAPI's dependency injection system with an actual
    authentication method (e.g., Auth0 or DummyAuth).
    """
    raise HTTPException(status_code=401, detail="No authentication method configured.")

AuthDependency = Annotated[Auth0User, Security(get_user)]


