"""全局配置：路径、数据规模、模型超参、GEO 规则权重。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODEL_DIR = ROOT / "models"
STATIC_DIR = Path(__file__).resolve().parent / "static"

DATA_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "georec.db"
RANKER_WEIGHTS = MODEL_DIR / "ranker.npz"
RANKER_META = MODEL_DIR / "ranker_meta.json"

# --- 合成数据规模 ---------------------------------------------------------
N_USERS = 420
N_ITEMS = 280
N_COLD_ITEMS = 16
MIN_INTERACTIONS = 18
MAX_INTERACTIONS = 55
RANDOM_SEED = 20261114

# --- 排序模型 -------------------------------------------------------------
TOP_K_RECALL = 120
TOP_K_EVAL = 10
LR_EPOCHS = 600
LR_LR = 0.02
LR_L2 = 0.02

# --- GEO 规则权重（总和归一化到 1.0） -------------------------------------
GEO_RULE_WEIGHTS: dict[str, float] = {
    "conclusion_first": 0.14,   # 结论前置
    "structured": 0.14,         # 标题/列表/表格结构
    "quantified": 0.14,         # 量化细节
    "sourced": 0.10,            # 来源可核验
    "atomic": 0.10,             # 段落原子化
    "no_hype": 0.12,            # 去推广/绝对化用语
    "self_contained": 0.08,     # 信息自包含
    "term_defined": 0.06,       # 术语可理解
    "timeliness": 0.06,         # 时效标注
    "balanced": 0.06,           # 客观对比
}

GEO_RULE_LABELS: dict[str, str] = {
    "conclusion_first": "结论前置",
    "structured": "结构化呈现",
    "quantified": "量化细节",
    "sourced": "来源可核验",
    "atomic": "段落原子化",
    "no_hype": "去推广用语",
    "self_contained": "信息自包含",
    "term_defined": "术语可理解",
    "timeliness": "时效标注",
    "balanced": "客观对比",
}

GEO_RULE_DESC: dict[str, str] = {
    "conclusion_first": "开篇直接给出核心结论或推荐意见，而非背景铺垫。AutoGEO 规则：State the key conclusion at the beginning of the document.",
    "structured": "使用小标题、有序列表、参数表格组织内容，便于模型与用户解析。",
    "quantified": "用具体参数、数值、型号替代「很好」「超强」等模糊表述。",
    "sourced": "关键结论标注可核验来源（检测报告、官方参数、实测）。",
    "atomic": "每个段落只承载一个要点，避免大段混杂。",
    "no_hype": "清除「全网最低」「100% 有效」「第一名」等绝对化与促销话术。",
    "self_contained": "核心信息在正文内闭环，不依赖跳转外链或图片才能理解。",
    "term_defined": "专业术语首次出现时给出通俗解释。",
    "timeliness": "标注参数版本或更新时间，避免信息过期。",
    "balanced": "同时说明适用范围与不适用场景，给出客观权衡。",
}

# 新品扶持位：内容排序分超过该阈值才给曝光位
COLD_SLOT_SCORE = 0.45

# --- LLM 改写（可选） -----------------------------------------------------
LLM_BASE_URL = os.getenv("GEOREC_LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("GEOREC_LLM_API_KEY", "")
LLM_MODEL = os.getenv("GEOREC_LLM_MODEL", "gpt-4o-mini")


@dataclass
class RecConfig:
    recall_k: int = TOP_K_RECALL
    eval_k: int = TOP_K_EVAL
    channels: tuple[str, ...] = ("itemcf", "usercf", "content", "hot", "geo")
    weights: dict[str, float] = field(default_factory=lambda: {
        "itemcf": 1.0,
        "usercf": 0.8,
        "content": 0.7,
        "hot": 0.35,
        "geo": 0.5,
    })


REC_CONFIG = RecConfig()
