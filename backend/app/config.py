import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))   # 读 backend\.env

DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY")
KIMI_KEY = os.getenv("KIMI_KEY")

DEEPSEEK_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
KIMI_BASE = "https://api.moonshot.cn/v1"
KIMI_MODEL = "kimi-k2.6"   # 原来是 "kimi-latest"

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{os.path.join(BASE_DIR, 'geo.db')}",
)
