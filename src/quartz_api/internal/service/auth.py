"""Authentication dependency for FastAPI using Auth0 JWT tokens."""

# ruff: noqa: B008
import os

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from typing_extensions import override

token_auth_scheme = HTTPBearer()

EMAIL_KEY = "https://openclimatefix.org/email"


class Auth:
    """Fast api dependency that validates an JWT token."""

    def __init__(self, domain: str, api_audience: str, algorithm: str) -> None:
        """Initialize the Auth dependency."""
        self._domain = domain
        self._api_audience = api_audience
        self._algorithm = algorithm

        self._jwks_client = jwt.PyJWKClient(f"https://{domain}/.well-known/jwks.json")

    def __call__(
        self,
        request: Request,
        auth_credentials: HTTPAuthorizationCredentials = Depends(token_auth_scheme),
    ) -> dict[str, str]:
        """Validate the JWT token and return the payload."""
        token = auth_credentials.credentials

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
        except (jwt.exceptions.PyJWKClientError, jwt.exceptions.DecodeError) as e:
            raise HTTPException(status_code=401, detail=str(e)) from e

        try:
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=self._algorithm,
                audience=self._api_audience,
                issuer=f"https://{self._domain}/",
            )
        except Exception as e:
            raise HTTPException(status_code=401, detail=str(e)) from e

        request.state.auth = payload

        return payload


class DummyAuth(Auth):
    """Dummy auth dependency for testing purposes."""

    def __init__(self, domain: str, api_audience: str, algorithm: str) -> None:
        """Initialize the DummyAuth dependency."""
        self._domain = domain
        self._api_audience = api_audience
        self._algorithm = algorithm

    @override
    def __call__(self) -> dict[str, str]:
        return {
            EMAIL_KEY: "test@test.com",
            "sub": "google-oath2|012345678909876543210",
        }


domain = os.getenv("AUTH0_DOMAIN")
api_audience = os.getenv("AUTH0_API_AUDIENCE")
if domain is None or api_audience is None:
    auth = DummyAuth(domain="dummy", api_audience="dummy", algorithm="dummy")
else:
    auth = Auth(
        domain=domain,
        api_audience=api_audience,
        algorithm="RS256",
    )

# TODO: add scopes for granular access across APIs
# auth = Auth(domain=os.getenv('AUTH0_DOMAIN'), api_audience=os.getenv('AUTH0_API_AUDIENCE'),
#     scopes={'read:india': ''})
