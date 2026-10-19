import argparse
import os
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

def main():
    parser = argparse.ArgumentParser(description="人工记录单家企业的发布与收录状态。")
    parser.add_argument("--business-id", type=int, required=True)
    business_id = parser.parse_args().business_id
    plan_path = os.path.join(DATA_DIR, f"distribute_plan_{business_id}.json")
    status_path = os.path.join(DATA_DIR, f"channel_status_{business_id}.json")
    # 读取分发计划
    with open(plan_path, encoding="utf-8") as file:
        plan = json.load(file)

    print("=== 收录状态追踪 ===")
    print("请根据各平台的实际公开页面，人工确认发布和收录状态。\n")

    status = []
    for t in plan:
        done = input(f"【{t['channel']}】已发布并确认可搜到吗？(y/n)：").strip().lower() == "y"
        status.append({**t, "published": done})
        mark = "已收录" if done else "未收录"
        print(f"   → {t['channel']}: {mark}\n")

    with open(status_path, "w", encoding="utf-8") as file:
        json.dump(status, file, ensure_ascii=False, indent=2)
    done_count = sum(1 for s in status if s["published"])
    print(f"收录状态已保存：{done_count}/{len(status)} 个渠道已收录")

if __name__ == "__main__":
    main()
