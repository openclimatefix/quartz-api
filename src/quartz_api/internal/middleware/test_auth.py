from quartz_api.internal.middleware.auth import get_org_id_from_authdata


def test_get_org_id_from_authdata_regular_user() -> None:
    authdata = {
        "app_metadata": {
            "hubspot_company_id": "12345",
        },
        "permissions": ["read:india"],
    }
    assert get_org_id_from_authdata(authdata) == "12345"

def test_get_org_id_from_authdata_admin_user() -> None:
    authdata = {
        "app_metadata": {
            "hubspot_company_id": "99999999999",
        },
        "permissions": ["ocf:admin", "read:india"],
    }
    assert get_org_id_from_authdata(authdata) is None

def test_get_org_id_from_authdata_no_metadata() -> None:
    authdata = {
        "permissions": ["read:india"],
    }
    assert get_org_id_from_authdata(authdata) == "no-org-access"

