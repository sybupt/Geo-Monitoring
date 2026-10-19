import argparse
import os
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

def main():
    parser = argparse.ArgumentParser(description="为单家企业生成内容发布清单。")
    parser.add_argument("--business-id", type=int, required=True)
    business_id = parser.parse_args().business_id
    content_dir = f"content/business_{business_id}"
    tasks = [
        {"channel": "企业官网", "action": "审核并发布企业网页和结构化数据", "file": f"{content_dir}/web.html", "status": "todo"},
        {"channel": "知乎", "action": "审核后发布回答", "file": f"{content_dir}/zhihu.txt", "status": "todo"},
        {"channel": "百科/公开资料", "action": "核实来源后补充企业公开信息", "file": f"{content_dir}/profile.json", "status": "todo"},
        {"channel": "地图 POI", "action": "认领商家并补全可核实信息", "file": f"{content_dir}/profile.json", "status": "todo"},
        {"channel": "小红书", "action": "审核后发布笔记", "file": f"{content_dir}/notes.txt", "status": "todo"},
        {"channel": "公众号", "action": "审核后发布文章", "file": f"{content_dir}/notes.txt", "status": "todo"},
    ]

    print("=== 投喂分发清单（照做）===")
    for i, t in enumerate(tasks, 1):
        print(f"{i}. 【{t['channel']}】 {t['action']}")
        print(f"   内容文件：{t['file']}\n")

    os.makedirs(DATA_DIR, exist_ok=True)
    plan_path = os.path.join(DATA_DIR, f"distribute_plan_{business_id}.json")
    with open(plan_path, "w", encoding="utf-8") as file:
        json.dump(tasks, file, ensure_ascii=False, indent=2)
    print(f"分发清单已保存到 {plan_path}")

if __name__ == "__main__":
    main()
