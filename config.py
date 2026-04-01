"""Bot configuration and constants"""
import os

# Discord Configuration
TOKEN = os.getenv("DISCORD_TOKEN")
MONGODB_URI = os.getenv("MONGODB_URI")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Optional: for private model repos
BOT_PREFIX = ['p!', 'P!']
POKETWO_USER_ID = 716390085896962058

# Embed Configuration
EMBED_COLOR = 0xf4e5ba

# Custom Emojis
class Emojis:
    GREEN_DOT = "<:greendot:1423970586245201920>"
    GREY_DOT = "<:greydot:1423970632130887710>"
    MALE = "<:male:1420708128785170453>"
    FEMALE = "<:female:1420708136943095889>"
    UNKNOWN = "<:unknown:1420708112310210560>"
    GIGANTAMAX = "<:gigantamax:1420708122267226202>"
    EGG = "<:egg:1427226230352117825>"
    MISSINGNO = "<:missingno:1420713960465760357>"
    GIFTBOX = "<:giftbox:1421047453511323658>"
    ANIMATED_GIFTBOX = "<a:animatedgiftbox:1421047436625055754>"

# Cache Configuration
CACHE_TTL = 60  # seconds
CACHE_TTL_SETTINGS = 300  # 5 minutes

# Collection Configuration
ITEMS_PER_PAGE = 20
MAX_DISPLAY_ITEMS = 150

# IV Thresholds
HIGH_IV_THRESHOLD = 90.0
LOW_IV_THRESHOLD = 10.0
PREDICTION_CONFIDENCE = 90.0

# Database Configuration
DB_TIMEOUT_MS = 3000
DB_MAX_POOL_SIZE = 10
DB_MIN_POOL_SIZE = 1

# File Paths
POKEMON_DATA_PATH = "data/pokemondata.json"
STARBOARD_DATA_PATH = "data/starboard.txt"

# Model Configuration (for predict.py)
MODEL_CACHE_DIR = "model_cache"
