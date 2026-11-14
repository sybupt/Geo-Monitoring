import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))   # 读 backend\.env

# ===== API Keys =====
DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY")
KIMI_KEY = os.getenv("KIMI_KEY")
DOUBAO_KEY = os.getenv("DOUBAO_KEY")   # 豆包（火山方舟），可选，没有就留空
ZHIPU_KEY = os.getenv("ZHIPU_KEY")     # 智谱开放平台（bigmodel.cn）
# 通义千问：阿里云百炼的 Key 常见叫法有两种，都认
QWEN_KEY = os.getenv("QWEN_KEY") or os.getenv("DASHSCOPE_KEY")

# 文心一言（百度千帆）：两种凭据都支持
# - QIANFAN_API_KEY：控制台 API Key（bce-v3/...），v2 直连，推荐
# - QIANFAN_AK + QIANFAN_SK：老账号的 AK/SK，走 OAuth 换 access_token（v1）
QIANFAN_API_KEY = os.getenv("QIANFAN_API_KEY")
QIANFAN_AK = os.getenv("QIANFAN_AK")
QIANFAN_SK = os.getenv("QIANFAN_SK")
# 只有 ERNIE 4.5 系支持 web_search 搜索增强
QIANFAN_MODEL = os.getenv("QIANFAN_MODEL", "ernie-4.5-turbo-32k")
# v1 OAuth 路径下的服务 endpoint（控制台里开通的服务名），v2 用不到
QIANFAN_ENDPOINT = os.getenv("QIANFAN_ENDPOINT", "ernie-4.5-8k-preview")

# 腾讯元宝：没有公开 API，只能浏览器自动化（L3）。
# 登录态文件由 scripts/yuanbao_login.py 生成，跑批时复用，不要提交进 git。
YUANBAO_STATE = os.getenv(
    "YUANBAO_STATE", os.path.join(BASE_DIR, "data", "yuanbao_state.json")
)
# 用系统已装的 Chrome（channel=chrome）避免再下一套 Chromium；无 Chrome 时置空走内置
YUANBAO_CHANNEL = os.getenv("YUANBAO_CHANNEL", "chrome")
YUANBAO_HEADLESS = os.getenv("YUANBAO_HEADLESS", "true").lower() != "false"
YUANBAO_TIMEOUT_SEC = int(os.getenv("YUANBAO_TIMEOUT_SEC", "180"))

# ===== v2 探测量层用到的模型与限流配置 =====
# 豆包联网搜索需要支持 web_search 的模型 / 推理接入点 id，可用 .env 覆盖
DOUBAO_MODEL = os.getenv("DOUBAO_MODEL", "doubao-1-5-pro-32k")
KIMI_SEARCH_MODEL = os.getenv("KIMI_SEARCH_MODEL", "kimi-k2.6")
# 智谱：必须是支持 web_search 工具的模型，否则会 400
ZHIPU_MODEL = os.getenv("ZHIPU_MODEL", "glm-4.5-air")
ZHIPU_BASE = os.getenv("ZHIPU_BASE", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
# 通义：必须走 DashScope 原生协议才能拿到 search_info（OpenAI 兼容协议不透传）
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")
QWEN_BASE = os.getenv(
    "QWEN_BASE",
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
)
# 判定用模型（LLM-as-judge），填 deepseek / kimi
JUDGE_PROVIDER = os.getenv("JUDGE_PROVIDER", "deepseek")
# 每引擎每分钟调用上限，防止把账号打到限流
PROBE_RATE_PER_MIN = {
    "doubao": int(os.getenv("RATE_DOUBAO", "10")),
    "kimi": int(os.getenv("RATE_KIMI", "20")),
    "deepseek": int(os.getenv("RATE_DEEPSEEK", "30")),
    # 智谱搜索按次计费且并发有额度限制，压到 20/min
    "zhipu": int(os.getenv("RATE_ZHIPU", "20")),
    # 通义百炼对 enable_search 有 QPM 限制，保守些
    "qwen": int(os.getenv("RATE_QWEN", "20")),
    "wenxin": int(os.getenv("RATE_WENXIN", "30")),
    # 元宝走浏览器自动化，一次 8–20 秒，压到 4/min；本质是「不并发打账号」
    "yuanbao": int(os.getenv("RATE_YUANBAO", "4")),
    # mock 不消耗额度，放开跑，免得测试和演示被限流拖成几分钟
    "mock": int(os.getenv("RATE_MOCK", "6000")),
}

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
