import os

# OpenSea API
OPENSEA_API_KEY = os.environ.get("OPENSEA_API_KEY", "")
OPENSEA_API_BASE = "https://api.opensea.io/api/v2"

# Magic Eden API (Solana)
MAGICEDEN_API_BASE = "https://api-mainnet.magiceden.dev/v2"

# Solana RPC
SOLANA_RPC_URL = os.environ.get(
    "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
)

# Thresholds — a listing is flagged as "fat finger" if price is below
# this percentage of the collection floor price
FAT_FINGER_THRESHOLD_PCT = float(os.environ.get("FAT_FINGER_THRESHOLD_PCT", "50"))

# Minimum floor price to monitor (ignore dust collections)
MIN_FLOOR_ETH = float(os.environ.get("MIN_FLOOR_ETH", "0.01"))
MIN_FLOOR_SOL = float(os.environ.get("MIN_FLOOR_SOL", "0.1"))

# Telegram alerts
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Polling intervals (seconds)
POLL_INTERVAL_ETH = int(os.environ.get("POLL_INTERVAL_ETH", "15"))
POLL_INTERVAL_SOL = int(os.environ.get("POLL_INTERVAL_SOL", "10"))

# Max collections to monitor simultaneously
MAX_WATCHLIST = int(os.environ.get("MAX_WATCHLIST", "50"))

PORT = int(os.environ.get("PORT", "8080"))
