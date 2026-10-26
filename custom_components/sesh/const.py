"""Constants for the Sesh integration."""
from datetime import timedelta

DOMAIN = "sesh"
DEFAULT_URL = "https://sesh-api.gyle.online"
SITE = "https://sesh-rigs.vercel.app"
PANEL = "sesh"

CONF_PIN = "pin"
CONF_TOKEN = "token"

# Leaderboard + today's counts. Cheap on the API, and quick enough for "dim the lights when I dab".
POLL = timedelta(seconds=15)
# Rigs and the last session change slowly; refreshed every this many polls, and soon after a dab of yours.
SYNC_EVERY = 20

EVENT_DAB = "sesh_dab"
EVENT_CHEERS = "sesh_cheers"
KINDS = {"peakpro": "Peak Pro", "switch2": "Switch 2"}
