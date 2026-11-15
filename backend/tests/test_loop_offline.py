"""离线闭环测试：规则学习 → 内容生成 → 校验 → 投喂 → 复测。

全程不调用任何模型 API（mock + 确定性生成），所以可以随便跑、不花钱。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

_DB = os.path.join(tempfile.gettempdir(), "geo_loop_test.db")
if os.path.exists(_DB):
    os.remove(_DB)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB
for _k in ("DEEPSEEK_KEY", "KIMI_KEY", "DOUBAO_KEY", "ZHIPU_KEY", "QWEN_KEY", "DASHSCOPE_KEY"):
    os.environ[_k] = ""

import app.models  # noqa: E402,F401
import app.v2.models  # noqa: E402,F401
import app.rules.models  # noqa: E402,F401
from app.content.facts import build_facts  # noqa: E402
from app.content.generator import generate  # noqa: E402
from app.content.validator import validate  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Business  # noqa: E402
from app.rules import extractor, store  # noqa: E402
from app.rules.dsl import CHANNEL_SPECS, applies_to_channel  # noqa: E402
from app.rules.features import extract  # noqa: E402
from app.rules.prior import prior_rules  # noqa: E402
from app.rules.models import ContentRow  # noqa: E402

CHANNELS = ["zhihu", "baijiahao", "xiaohongshu", "douyin", "dianping", "baike", "wechat", "meituan", "amap", "gov_news"]


def _seed_business() -> int:
    with SessionLocal() as db:
        biz = db.query(Business).filter(Business.name == "测试影像工作室").first()
        if biz:
            return biz.id
        biz = Business(
            name="测试影像工作室",
            alias="测试影像",
            region="湖北宜昌",
            category="摄影",
            description=json.dumps(
                {
                    "price": "全家福 398 元起，证件照 68 元/套",
                    "area": "实景棚 260 平方米",
                    "founded": "2016 年开始经营，至今 9 年",
                    "hours": "每日 09:00-21:00",
                    "address": "宜昌市西陵区夷陵大道 88 号 3 楼",
                    "transport": "距公交站 200 米",
                    "rating": "评分 4.7 分",
                    "credentials": ["营业执照 91420000TEST"],
                    "updated_at": "2026 年 11 月",
                },
                ensure_ascii=False,
            ),
            selling_points=json.dumps(
                ["全家福拍摄：含服装 3 套、精修 20 张，交付 10 天", "儿童拍摄：有独立母婴区", "证件照快照：25 分钟出片"],
                ensure_ascii=False,
            ),
            evidence=json.dumps(["近 12 个月服务约 1800 组家庭"], ensure_ascii=False),
        )
        db.add(biz)
        db.commit()
        return biz.id


class FeatureTests(unittest.TestCase):
    def test_detects_structure(self):
        text = (
            "测试影像工作室是宜昌摄影服务点，价格 398 元起。\n"
            "## 服务\n1. 全家福：3 套服装\n2. 证件照：68 元\n3. 外景：3 个机位\n"
            "## 问答\nQ：多少钱？\nA：398 元起。\nQ：在哪？\nA：夷陵大道 88 号。\n"
        )
        feats = extract(text, brand="测试影像工作室", region="宜昌")
        self.assertEqual(feats["conclusion_first"], 1.0)
        self.assertEqual(feats["list_structure"], 1.0)
        self.assertEqual(feats["faq_block"], 1.0)
        self.assertEqual(feats["heading_hierarchy"], 1.0)
        self.assertEqual(feats["number_density"], 1.0)

    def test_ordinal_is_not_extreme_word(self):
        text = "第一，价格公开；第二，档期先约。测试影像工作室在宜昌。"
        from app.rules.features import metrics

        self.assertEqual(metrics(text, brand="测试影像工作室")["extreme_words"], [])

    def test_channel_applicability(self):
        self.assertFalse(applies_to_channel("faq_block", "douyin"))  # 口播不该被要求写 FAQ
        self.assertTrue(applies_to_channel("faq_block", "zhihu"))
        self.assertTrue(applies_to_channel("faq_block", "unknown_channel"))


class RuleLearningTests(unittest.TestCase):
    def test_measured_learning_finds_real_signal(self):
        # 造 20 条样本：带数字/结论的可见度高，不带的很低
        good = "测试影像工作室在宜昌，价格 398 元起，场地 260 平方米，评分 4.7 分。"
        # 低可见样本也要足够长，否则会被长度过滤器丢掉，导致「对照组不足」学不出东西
        bad = "这家店还行吧，具体价格和场地都不太清楚，反正去年去过一次，感觉一般，没留下什么印象。"
        records = [{"answer": good, "visibility": 0.9} for _ in range(12)]
        records += [{"answer": bad, "visibility": 0.05} for _ in range(8)]
        rules = extractor.learn_from_samples(records, engine="mock", brand="测试影像工作室", region="宜昌")
        dims = {r.dimension for r in rules}
        self.assertIn("number_density", dims)
        self.assertTrue(all(r.source == "measured" for r in rules))
        self.assertTrue(all(r.evidence_n >= 6 for r in rules))

    def test_prior_never_overrides_measured(self):
        from app.rules.dsl import Rule

        key = dict(engine="mock", industry="local_life", channel="zhihu", dimension="heading_hierarchy")
        with SessionLocal() as db:
            store.upsert(
                db,
                [
                    Rule(
                        instruction="实测：小标题分层",
                        weight=0.9,
                        evidence_n=30,
                        lift=0.5,
                        source="measured",
                        scope="engine",
                        **key,
                    )
                ],
            )
            stats = store.upsert(
                db,
                [
                    Rule(
                        instruction="先验：小标题分层（低权重）",
                        weight=0.5,
                        evidence_n=0,
                        lift=0.0,
                        source="prior",
                        **key,
                    )
                ],
            )
            self.assertGreaterEqual(stats["skipped"], 1)
            rows = store.list_rules(db, engine="mock", channel="zhihu")
            same = [r for r in rows if r["dimension"] == "heading_hierarchy"]
            self.assertTrue(same)
            self.assertEqual(same[0]["source"], "measured")
            self.assertEqual(same[0]["weight"], 0.9)


class GenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.biz_id = _seed_business()
        with SessionLocal() as db:
            for eng in ("mock", "doubao", "kimi", "deepseek"):
                store.upsert(db, prior_rules(eng, channels=CHANNELS))

    def _facts(self):
        with SessionLocal() as db:
            return build_facts(db.get(Business, self.biz_id))

    def test_all_channels_generate_and_pass(self):
        facts = self._facts()
        for ch in CHANNELS:
            with SessionLocal() as db:
                rules = store.effective_rules(db, engine="mock", channel=ch)
            self.assertTrue(rules, f"{ch} 没有生效规则")
            out = generate(facts, rules, engine="mock", channel=ch)
            report = validate(out["content"], facts=facts, rules=rules, channel=ch, brand=facts["name"])
            lo, hi = CHANNEL_SPECS[ch]["length"]
            self.assertGreaterEqual(out["chars"], lo, f"{ch} 篇幅过短")
            self.assertLessEqual(out["chars"], hi, f"{ch} 篇幅过长")
            self.assertTrue(report["pass"], f"{ch} 未通过校验：{[i['message'] for i in report['issues'] if i['level'] == 'fatal']}")

    def test_invented_number_is_blocked(self):
        facts = self._facts()
        with SessionLocal() as db:
            rules = store.effective_rules(db, engine="mock", channel="zhihu")
        bad = "测试影像工作室在宜昌，场地 9999 平方米，价格 398 元起。"
        report = validate(bad, facts=facts, rules=rules, channel="zhihu", brand=facts["name"])
        self.assertFalse(report["pass"])
        self.assertTrue(any(i["code"] == "unsupported_number" for i in report["issues"]))

    def test_extreme_words_blocked(self):
        facts = self._facts()
        with SessionLocal() as db:
            rules = store.effective_rules(db, engine="mock", channel="zhihu")
        bad = "测试影像工作室是宜昌最好的摄影店，价格 398 元起，场地 260 平方米。"
        report = validate(bad, facts=facts, rules=rules, channel="zhihu", brand=facts["name"])
        self.assertFalse(report["pass"])
        self.assertTrue(any(i["code"] == "extreme_words" for i in report["issues"]))

    def test_rule_filter_is_channel_aware(self):
        with SessionLocal() as db:
            douyin = {r.dimension for r in store.effective_rules(db, engine="mock", channel="douyin")}
            zhihu = {r.dimension for r in store.effective_rules(db, engine="mock", channel="zhihu")}
        self.assertNotIn("faq_block", douyin)
        self.assertIn("faq_block", zhihu)


class LoopTests(unittest.TestCase):
    def test_full_loop_improves_mention_rate(self):
        """投喂后 mock 的提及率必须高于投喂前——否则说明闭环没接上。"""
        from app.rules.api import LoopPayload, loop_demo

        with SessionLocal() as db:
            for eng in ("mock",):
                store.upsert(db, prior_rules(eng, channels=CHANNELS))
        biz_id = _seed_business()
        out = loop_demo(LoopPayload(business_id=biz_id, engines=["mock"], n_samples=3, reset=True))
        self.assertTrue(out["simulation"])
        self.assertGreater(out["after"]["mention_rate"], out["before"]["mention_rate"])
        self.assertGreater(out["delta"], 0)
        fed = [c for c in out["contents"] if c["fed"]]
        self.assertGreaterEqual(len(fed), 1)
        with SessionLocal() as db:
            self.assertGreater(db.query(ContentRow).filter(ContentRow.status == "fed").count(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
