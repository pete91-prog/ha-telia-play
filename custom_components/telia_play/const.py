"""Constants for the Telia Play integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "telia_play"
PLATFORMS: Final = ["sensor", "switch", "button"]

CONF_COUNTRY: Final = "country"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_INCLUDE_CHANNELS: Final = "include_channels"

CONF_DEVICE_ID: Final = "device_id"
CONF_BOOT_ID: Final = "boot_id"
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_VALID_TO: Final = "valid_to"

DEFAULT_COUNTRY: Final = "NO"
SCAN_INTERVAL_SECONDS: Final = 300

SERVICE_ENABLE: Final = "enable_service"
SERVICE_DISABLE: Final = "disable_service"
SERVICE_SET_SERVICES: Final = "set_streaming_services"
SERVICE_ACTIVATE: Final = "activate_service"
SERVICE_SUBMIT_CODE: Final = "submit_activation_code"

ATTR_SERVICE: Final = "service"
ATTR_SERVICES: Final = "services"
ATTR_CODE: Final = "code"

NOTIFICATION_ID_PREFIX: Final = "telia_play_activate"

USER_AGENT: Final = "HomeAssistant-TeliaPlay/1.0"
CLIENT_NAME: Final = "accountweb"
CLIENT_VERSION: Final = "1.24.1"
WHITE_LABEL: Final = "TELIA"

PURCHASE_GATEWAY: Final = (
    "https://purchasegateway-telia.clientapi-prod.live.tv.telia.net/purchasegateway"
)
ENGAGEMENT_GATEWAY: Final = (
    "https://engagementgateway-telia.clientapi-prod.live.tv.telia.net/engagementgateway"
)

TELIA_AUTH_HOST_SUFFIXES: Final = (
    "teliaplay.no",
    "teliaplay.se",
    "teliaplay.fi",
    "teliaplay.dk",
    "telia.no",
    "telia.se",
    "telia.fi",
    "telia.dk",
    "telia.net",
    "telia.com",
    "live.tv.telia.net",
    "t6a.net",
)

COUNTRIES: Final = {
    "NO": {
        "code": "NO",
        "name": "Norway",
        "login_url": "https://logingateway.teliaplay.no/logingateway",
        "login_origin": "https://login.teliaplay.no",
        "account_url": "https://account.teliaplay.no",
        "timezone": "Europe/Oslo",
        "has_points_menu": True,
    },
    "SE": {
        "code": "SE",
        "name": "Sweden",
        "login_url": "https://logingateway.teliaplay.se/logingateway",
        "login_origin": "https://login.teliaplay.se",
        "account_url": "https://account.teliaplay.se",
        "timezone": "Europe/Stockholm",
        "has_points_menu": False,
    },
    "FI": {
        "code": "FI",
        "name": "Finland",
        "login_url": "https://logingateway.teliaplay.fi/logingateway",
        "login_origin": "https://login.teliaplay.fi",
        "account_url": "https://account.teliaplay.fi",
        "timezone": "Europe/Helsinki",
        "has_points_menu": False,
    },
    "DK": {
        "code": "DK",
        "name": "Denmark",
        "login_url": "https://logingateway.teliaplay.dk/logingateway",
        "login_origin": "https://login.teliaplay.dk",
        "account_url": "https://account.teliaplay.dk",
        "timezone": "Europe/Copenhagen",
        "has_points_menu": False,
    },
}
