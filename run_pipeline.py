"""一键流水线：生成数据 → 训练并评测 → 打印摘要。

    python run_pipeline.py            # 全量重跑
    python run_pipeline.py --skip-data  # 只重训模型
"""
from __future__ import annotations

import argparse
import json

from app.config import DB_PATH
from app.data_gen import generate
from app.db import table_counts
from app.rec.evaluate import run_evaluation


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-data", action="store_true", help="跳过数据生成，仅重训与评测")
    args = ap.parse_args()

    if not args.skip_data:
        print("== 1/2 生成合成数据集 ==")
        summary = generate(reset=True)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("== 跳过数据生成 ==", table_counts())

    print("\n== 2/2 训练排序模型并离线评测 ==")
    report = run_evaluation()
    print(json.dumps({k: report[k] for k in ("dataset", "variants", "lift", "cold_start")}, ensure_ascii=False, indent=2))
    print(f"\n数据库：{DB_PATH}")
    print("启动服务： python -m uvicorn app.api:app --host 127.0.0.1 --port 8000")


if __name__ == "__main__":
    main()
