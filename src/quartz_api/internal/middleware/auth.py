"""Authentication dependency for FastAPI using Auth0 JWT tokens."""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer
from fastapi_plugin.fast_api_client import Auth0FastAPI

log = logging.getLogger(__name__)
EMAIL_KEY = "https://openclimatefix.org/email"

# Uninstantiated OAuth2 scheme that enables authorization button in swagger.
# Must be overwritten when configuring server.
oauth2_scheme = HTTPBearer(auto_error=False)

class DummyBackend:
    """Mock backend for testing without auth."""

    def __init__(self) -> None:
        """Initialize the dummy backend."""
        log.warning("Using DummyBackend for authentication. This should not be used in production!")

    def require_auth(
        self,
        scopes: str | list[str] | None = None, # noqa: ARG002
    ) -> Callable[[Request], Awaitable[dict[str, str]]]:
        """Return a simulated authentication function."""
        async def _dummy_dependency(_: Request) -> dict[str, str]:
            return {
                "sub": "dummy|123456",
                EMAIL_KEY: "test@test.com",
                "scope": "openid profile email",
            }
        return _dummy_dependency

class AuthClient:
    """Generic client interface for authorization.

    Must be instantiated with a backend implementation.
    """

    def __init__(self) -> None:
        """Initialize with a dummy backend by default."""
        self._backend: Auth0FastAPI | DummyBackend | None = None

    def instantiate_auth0(self, domain: str, audience: str) -> None:
        """Instantiate the Auth0 backend."""
        self._backend = Auth0FastAPI(
            domain=domain,
            audience=audience,
        )

    def instantiate_dummy(self) -> None:
        """Instantiate the dummy backend."""
        self._backend = DummyBackend()

    def require_auth(self, scopes: str | list[str] | None = None) -> Callable[[Request], Awaitable[dict[str, str]]]: # noqa
        """Authentication function to be used as a FastAPI dependency."""
        async def _proxy_dependency(
            request: Request,
            token: str = Depends(oauth2_scheme), # noqa: ARG001
        ) -> dict[str, str]:
            if self._backend is None:
                raise HTTPException(status_code=500, detail="Auth backend not configured")

            validator_dependency = self._backend.require_auth(scopes)
            try:
                claims = await validator_dependency(request)
            except HTTPException as e:
                if e.status_code == 403:
                    log.info(f"Unauthorized access attempt: {e.detail}")

                raise e

            return claims

        return _proxy_dependency

auth_instance = AuthClient()

AuthDependency = Annotated[dict[str, str], Depends(auth_instance.require_auth())]

def get_oauth_id_from_sub(auth0_sub: str) -> str:
    """Extract the auth ID from a auth0 sub ID.

    For example auth0|66a4 .... or google-oauth2|1042
    """
    if "|" not in auth0_sub:
        return auth0_sub

    return auth0_sub.split("|")[1]


def make_api_auth_description(
        domain:str,
        audience:str,
        host_url:str,
        client_id:str) -> str:
    """Generate API authentication description."""
    # note that the odd indentation here is needed for to make the f-string and markdown work
    t = f"""
# Authentication

Some routes may require authentication. An access token can be obtained via cURL:

```
export AUTH=$(curl --request POST"

--url https://{domain}/oauth/token
--header 'content-type: application/json'
--data '{{
    "client_id":"{client_id}",
    "audience": {audience},
    "grant_type":"password",
    "username":"username",
    "password":"password"
    }}'
)

export TOKEN=$(echo "${{AUTH}}" | jq '.access_token' | tr -d '"') \n\n
```

enabling authenticated requests using the Bearer scheme:
```
curl -X GET '{host_url}/<route>' -H "Authorization: Bearer $TOKEN"
```

"""

    return t
