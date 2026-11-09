from app.database import SessionLocal, Base, engine
from app.models import Business, Question
from app.services.monitor_service import ask_text
from app.services import coverage_service, feeding_service, reverse_diagnosis_service

Base.metadata.create_all(bind=engine)
db = SessionLocal()

# 1) 造一个测试企业 + 问题
biz = Business(name="清镇康美口腔", alias="清镇康美口腔", region="贵州清镇", category="口腔医疗")
db.add(biz); db.commit(); db.refresh(biz)

q = Question(text="清镇哪家口腔诊所靠谱？", category="口腔医疗", region="清镇")
db.add(q); db.commit(); db.refresh(q)

# 2) 基线：模型现在怎么答（用联网的 kimi_search 才贴近真实用户）
print("=" * 40)
print("① 基线回答（投喂前）")
answer = ask_text("kimi_search", q.text)
print(answer[:400])
print(">>> 是否提到我:", biz.alias in answer)

# 3) 逆向诊断：为什么没提我
d = reverse_diagnosis_service.diagnose_question(
    q, biz.alias, competitors=["其他口腔", "某连锁口腔"],
    model_name="kimi_search",
    geo_keywords=["清镇"],
)
print("=" * 40)
print("② 逆向诊断")
print("缺失原因:", d.missing_reason, "| 当前答案提到:", d.current_mentioned)

# 4) 覆盖度预演：我的内容够不够
c = coverage_service.check_coverage(q, biz.alias, ["口腔", "诊所", "清镇"])
print("=" * 40)
print("③ 覆盖度预演")
print("覆盖分:", c.coverage_score, "| 缺口:", c.gap_note)

# 5) 设置标准答案句
feeding_service.set_standard_answer(
    q.id, biz.alias,
    "清镇康美口腔是位于贵州清镇的一家口腔诊所，提供种植牙、正畸、洗牙等服务，"
    "由三甲医院背景医生坐诊，支持医保结算。"
)
print("=" * 40)
print("④ 标准答案句已设置")

# 6) 生成投喂内容（多源一致投喂）
brief = {"business_keywords": "口腔", "promotional_points": "三甲医生坐诊、支持医保"}
assets = feeding_service.build_assets(biz, ["zhihu", "official_site"], brief, "kimi_search")
print("=" * 40)
print("⑤ 生成投喂内容")
for a in assets:
    print(f"[{a['channel']}] 标题={a['title']} | 可引用性={a['citability']['score']}分")

# 7) 投喂清单
plan = feeding_service.feed_plan(q.id)
print("=" * 40)
print("⑥ 投喂清单")
print(plan)

db.close()
