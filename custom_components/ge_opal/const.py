"""Constants for the GE Opal integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ge_opal"
PLATFORMS: Final = ("button", "sensor", "switch")

CONF_ADDRESS: Final = "address"
DEFAULT_NAME: Final = "GE Opal Ice Maker"

ADVERTISEMENT_SERVICE_UUID: Final = "e2779da7-0a82-4be7-b754-31ed3e727253"
GATT_SERVICE_UUID: Final = "3e6763c5-9429-40cc-909e-bebf8c7487be"

CHAR_MAKE_STATE: Final = "097a2751-ca0d-432f-87b5-7d2f31e45551"
CHAR_ICE_BIN_STATE: Final = "5bcbf6b1-de80-94b6-0f4b-99fb984707b6"
CHAR_NIGHT_MODE: Final = "37988f00-ea39-4a2d-9983-afad6535c02e"
CHAR_CLEANING_PHASE: Final = "efe4bd77-0600-47d7-b3f6-dc81af0d9aaf"
CHAR_MAKE_ICE_CONTROL: Final = "79994230-4b04-40cd-85c9-02dd1a8d4dd0"

# The original ESPHome implementation maps the first byte of these values.
MAKE_STATE_NAMES: Final = {
    0: "Not Making Ice",
    1: "Making Ice",
    2: "Out of Water",
    3: "Ice Bin Full",
    4: "Cleaning",
}
ICE_BIN_NAMES: Final = {0: "In", 1: "Out"}

# Polling is retained as a fallback and a low-rate consistency check even for
# characteristics that also notify. Runtime GATT properties determine whether
# a read or notification subscription is actually attempted.
POLL_INTERVAL: Final = 45
CONNECT_TIMEOUT: Final = 20
RECONNECT_DELAY: Final = 8

DATA_COORDINATOR: Final = "coordinator"
