"""Authentication dependency for FastAPI using Auth0 JWT tokens."""

import os
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import HTTPBearer
from fastapi_auth0 import Auth0, Auth0User

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
    
    def get_user(self, *kwargs) -> dict[str, str]:
        """Return a dummy user payload."""
        return {
            "email": "test@test.com",
            "id": "012345678909876543210",
        }
    


# Lets setup the auths
# 'auth' can be imported into the route if we want to limit a route by scopes
auth = DummyAuth()
if (os.getenv("AUTH0_DOMAIN") is not None) and (os.getenv("AUTH0_AUDIENCE") is not None):
    auth = Auth0(api_audience=os.getenv("AUTH0_AUDIENCE"), domain=os.getenv("AUTH0_DOMAIN"))

    # we do this so that "Authorization" button appears in swagger ui
    security = Security(auth.get_user)

# this is the dependency we can use in routes to get the user info
get_user = auth.get_user
AuthDependency = Annotated[Auth0User, Depends(get_user)]
