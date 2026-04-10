"""Central configuration for fat-finger-sniper."""
import os

# --- Risk Controls ---
PAPER_MODE = os.environ.get("PAPER_MODE", "true").lower() == "true"
MAX_BANKROLL_USD = float(os.environ.get("MAX_BANKROLL_USD", "100"))
MAX_PER_SNIPE_USD = float(os.environ.get("MAX_PER_SNIPE_USD", "25"))
MAX_DAILY_USD = float(os.environ.get("MAX_DAILY_USD", "50"))
COOLDOWN_SECONDS = int(os.environ.get("COOLDOWN_SECONDS", "30"))
GAS_MULTIPLIER_MAX = float(os.environ.get("GAS_MULTIPLIER_MAX", "2.0"))

# --- Detection Thresholds ---
# Discount from fair value to flag
CRITICAL_THRESHOLD_PCT = 95  # >95% below fair value
HIGH_THRESHOLD_PCT = 85      # 85-95%
MEDIUM_THRESHOLD_PCT = 70    # 70-85%
MIN_DISCOUNT_PCT = float(os.environ.get("MIN_DISCOUNT_PCT", "85"))

# --- Kill Switch ---
KILLSWITCH_TIMEOUT_SECONDS = int(os.environ.get("KILLSWITCH_TIMEOUT_SECONDS", "60"))

# --- Telegram (separate bot from Phoebe) ---
TELEGRAM_BOT_TOKEN = os.environ.get("FATFINGER_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1516882079")

# --- Wallets ---
PHANTOM_TREASURY = "HmW2bQeLpJv3FJrSBV1jeyra2oof5rq6uBkB1cSLnSAK"
SOL_PRIVATE_KEY = os.environ.get("SOL_PRIVATE_KEY", "")  # base64 ed25519
EVM_PRIVATE_KEY = os.environ.get("EVM_PRIVATE_KEY", "")   # 0x hex

# --- RPC / API Keys ---
SOLANA_RPC_URL = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
ETH_RPC_URL = os.environ.get("ETH_RPC_URL", "https://eth.llamarpc.com")
BASE_RPC_URL = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
POLYGON_RPC_URL = os.environ.get("POLYGON_RPC_URL", "https://polygon-rpc.com")
BSC_RPC_URL = os.environ.get("BSC_RPC_URL", "https://bsc-dataseed.binance.org")

OPENSEA_API_KEY = os.environ.get("OPENSEA_API_KEY", "")
TENSOR_API_KEY = os.environ.get("TENSOR_API_KEY", "")
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY", "")

# --- Relay.link (cross-chain bridging) ---
RELAY_API_BASE = "https://api.relay.link"

# --- Marketplace APIs ---
OPENSEA_API_BASE = "https://api.opensea.io/api/v2"
MAGICEDEN_API_BASE = "https://api-mainnet.magiceden.dev/v2"
TENSOR_API_BASE = "https://api.tensor.so/graphql"
JUPITER_API_BASE = "https://api.jup.ag"
POLYMARKET_CLOB_BASE = "https://clob.polymarket.com"
POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com"

# --- Database ---
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# --- Polling intervals (seconds) ---
POLL_INTERVAL_NFT = int(os.environ.get("POLL_INTERVAL_NFT", "10"))
POLL_INTERVAL_DEX = int(os.environ.get("POLL_INTERVAL_DEX", "5"))
POLL_INTERVAL_POLY = int(os.environ.get("POLL_INTERVAL_POLY", "8"))
POLL_INTERVAL_TRAD = int(os.environ.get("POLL_INTERVAL_TRAD", "30"))

# --- Honeypot ---
MIN_COLLECTION_VOLUME_ETH = float(os.environ.get("MIN_COLLECTION_VOLUME_ETH", "10"))
MIN_COLLECTION_VOLUME_SOL = float(os.environ.get("MIN_COLLECTION_VOLUME_SOL", "50"))
MIN_SELLER_HISTORY = int(os.environ.get("MIN_SELLER_HISTORY", "3"))

PORT = int(os.environ.get("PORT", "8080"))
