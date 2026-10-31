import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))   # 读 backend\.env

# ===== API Keys =====
DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY")
KIMI_KEY = os.getenv("KIMI_KEY")
DOUBAO_KEY = os.getenv("DOUBAO_KEY")   # 豆包（火山方舟），可选，没有就留空

# ===== 兼容旧代码的单模型常量 =====
DEEPSEEK_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
KIMI_BASE = "https://api.moonshot.cn/v1"
KIMI_MODEL = "kimi-k2.6"

# ===== 多模型配置表（监测引擎升级核心）=====
# 每个模型：label(显示名) / api_key / api_base(端点) / model(模型名) / web_search(是否联网)
MODELS = {
    "deepseek": {
        "label": "DeepSeek",
        "api_key": DEEPSEEK_KEY,
        "api_base": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "web_search": False,   # deepseek-chat 不联网
    },
    "kimi": {
        "label": "Kimi",
        "api_key": KIMI_KEY,
        "api_base": "https://api.moonshot.cn/v1",
        "model": "kimi-k2.6",
        "web_search": True,    # 支持 $web_search 联网
    },
    "doubao": {
        "label": "豆包",
        "api_key": DOUBAO_KEY,
        "api_base": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-1-5-pro-32k",
        "web_search": False,
    },
}

# 只启用已配置 key 的模型（监测时用它，避免调没 key 的模型报错）
ACTIVE_MODELS = [k for k, v in MODELS.items() if v["api_key"]]

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{os.path.join(BASE_DIR, 'geo.db')}",
)
