"""Constants for Ring Clip Downloader."""
DOMAIN = "ring_clip_downloader"
PLATFORMS = ["sensor"]

# Config entry keys
CONF_RING_ENTRY_ID = "ring_entry_id"
CONF_DOWNLOAD_PATH = "download_path"
CONF_RETENTION_DAYS = "retention_days"
CONF_POLL_INTERVAL = "poll_interval"

# Defaults
DEFAULT_DOWNLOAD_PATH = "/media/ring_clips"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_POLL_INTERVAL = 5  # minutes

# Ring API — no credentials stored here; all auth comes from the Ring config entry
RING_API_BASE = "https://api.ring.com"
RING_OAUTH_URL = "https://oauth.ring.com/oauth/token"
RING_CLIENT_ID = "ring_official_android"
RING_USER_AGENT = "android:com.ringapp"

# Clip URL readiness polling
# Ring's documented rate limit is ~12 req/min; we budget ≤3/min (one per 20 s)
CLIP_RETRY_INTERVAL_S = 20
CLIP_RETRY_MAX_S = 180  # give up after 3 minutes

# Persistent storage
STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1

# Coordinator data keys (never exposed directly to the UI)
DATA_COORDINATOR = "coordinator"
DATA_STORE = "store"
