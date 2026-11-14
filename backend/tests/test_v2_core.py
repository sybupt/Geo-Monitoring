"""v2 核心链路测试：指标口径、判定规则、豆包解析、mock 端到端。

跑法（在 backend 目录）：
    conda run -n geo python -m unittest tests.test_v2_core -v
"""

from __future__ import annotations

import os
import tempfile
import unittest

# 必须赶在 app.config 之前设置，否则会连到开发库
_TMP_DB = os.path.join(tempfile.gettempdir(), "geo_v2_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.judge import find_positions, judge, rule_judge  # noqa: E402
from app.metrics import did, summarize_engine, wilson_interval  # noqa: E402
from app.models import Business  # noqa: E402
from app.probes import engine_catalog  # noqa: E402
from app.probes.doubao import _parse_response  # noqa: E402
from app.v2 import service  # noqa: E402
from app.v2.models import ProbeSample  # noqa: E402

import app.v2.models  # noqa: E402,F401

Base.metadata.create_all(bind=engine)


class MetricsTests(unittest.TestCase):
    def test_wilson_edge_cases(self):
        lo, _ = wilson_interval(0, 5)
        self.assertEqual(lo, 0.0)
        _, hi = wilson_interval(5, 5)
        self.assertEqual(hi, 1.0)
        self.assertAlmostEqual(wilson_interval(0, 0)[0], 0.0)

    def test_wilson_narrows_with_more_samples(self):
        narrow = wilson_interval(30, 50)
        wide = wilson_interval(3, 5)
        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_did_neutralises_global_shift(self):
        # 两组都涨 20%，说明是模型/季节因素，DID 应该为 0
        res = did(after_t=5, n_t=10, before_t=3, n_t_before=10,
                  after_c=4, n_c=10, before_c=2, n_c_before=10)
        self.assertAlmostEqual(res["did"], 0.0, places=4)
        self.assertFalse(res["significant"])

    def test_did_detects_real_lift(self):
        res = did(after_t=9, n_t=10, before_t=2, n_t_before=10,
                  after_c=3, n_c=10, before_c=2, n_c_before=10)
        self.assertGreater(res["did"], 0.5)
        self.assertTrue(res["significant"])

    def test_summarize_engine_keeps_citation_types_apart(self):
        rows = [
            {"engine": "doubao", "ok": True, "mentioned": True, "sentiment": 1, "first_rank": 1,
             "citation_type": "answer_level", "citations": [{"domain": "dianping.com"}], "tier": "L1"},
            {"engine": "doubao", "ok": True, "mentioned": False, "sentiment": 0,
             "citation_type": "answer_level", "citations": [{"domain": "zhihu.com"}], "tier": "L1"},
        ]
        summary = summarize_engine(rows)
        self.assertEqual(summary["n"], 2)
        self.assertEqual(summary["mention_rate"]["count"], 1)
        self.assertEqual(summary["first_mention_rate"]["count"], 1)
        self.assertEqual(summary["citation_types"], {"answer_level": 2})
        self.assertIn("本地生活平台", dict(summary["source_types"]))


class JudgeTests(unittest.TestCase):
    def test_address_false_positive_is_filtered(self):
        positions = find_positions("店址在华为路 12 号，对面是华为大厦。", ["华为"])
        self.assertEqual(positions, [])

    def test_brand_name_with_shop_suffix_still_matches(self):
        positions = find_positions("推荐老王烧烤店，味道不错。", ["老王烧烤"])
        self.assertTrue(positions)

    def test_negation_marks_negative(self):
        verdict = rule_judge("这家不建议去，环境差。", "清镇康美口腔", [], [])
        self.assertFalse(verdict.mentioned)

    def test_mention_with_negative_window(self):
        verdict = rule_judge("清镇康美口腔不建议去，排队太久。", "清镇康美口腔", [], [])
        self.assertTrue(verdict.mentioned)
        self.assertEqual(verdict.sentiment, -1)

    def test_rule_only_mode_returns_quickly(self):
        verdict = judge("清镇哪家口腔好", "清镇康美口腔口碑不错", "清镇康美口腔", use_llm=False)
        self.assertTrue(verdict.mentioned)
        self.assertEqual(verdict.method, "rule")


class DoubaoParseTests(unittest.TestCase):
    def test_parses_answer_citations_and_queries(self):
        payload = {
            "model": "doubao-seed-2-1-pro-260915",
            "output": [
                {"type": "web_search_call", "action": {"query": "清镇 口腔 哪家好", "queries": ["清镇 口腔 哪家好"]}},
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "清镇康美口腔口碑不错。",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://www.dianping.com/shop/123",
                                    "title": "清镇康美口腔",
                                    "site_name": "大众点评",
                                }
                            ],
                        }
                    ],
                },
            ],
        }
        answer, citations, queries, searched = _parse_response(payload, "answer_level")
        self.assertIn("康美口腔", answer)
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0].domain, "dianping.com")
        self.assertEqual(citations[0].citation_type, "answer_level")
        self.assertTrue(searched)
        self.assertEqual(queries, ["清镇 口腔 哪家好"])

    def test_empty_annotations_is_recorded_as_no_citation(self):
        payload = {"model": "m", "output": [{"type": "message", "content": [{"type": "output_text", "text": "好的"}]}]}
        answer, citations, queries, searched = _parse_response(payload, "answer_level")
        self.assertEqual(answer, "好的")
        self.assertEqual(citations, [])
        self.assertFalse(searched)


class EngineCatalogTests(unittest.TestCase):
    def test_seven_engines_registered_and_honest_about_coverage(self):
        catalog = engine_catalog()
        self.assertEqual(len(catalog), 7)
        wired = {e["engine"] for e in catalog if e["implemented"]}
        # 七家全部接线；元宝走浏览器自动化（L3），文心走千帆
        self.assertEqual(
            wired, {"doubao", "kimi", "deepseek", "qwen", "zhipu", "wenxin", "yuanbao"}
        )
        pending = {e["engine"] for e in catalog if not e["implemented"]}
        self.assertEqual(pending, set())
        for e in catalog:
            self.assertTrue(e["wire_hint"])


class MockEndToEndTests(unittest.TestCase):
    """不用一个 API Key，也要能把「多采样 → 判定 → 指标 → 报告」跑通。"""

    def test_full_pipeline(self):
        with SessionLocal() as db:
            biz = Business(
                name="清镇康美口腔",
                alias="康美口腔,康美",
                region="贵州清镇",
                category="口腔诊所",
            )
            db.add(biz)
            db.commit()
            db.refresh(biz)
            biz_id = biz.id

        questions = service.available_questions(
            biz_id, "清镇", ["口腔诊所"], ["火锅", "月子中心"]
        )
        self.assertTrue(any(q["is_control"] for q in questions))

        with SessionLocal() as db:
            batch = service.create_batch(
                db,
                business_id=biz_id,
                engines=["mock"],
                questions=questions,
                n_samples=5,
                competitors=["仁爱口腔", "美奥口腔"],
                judge_mode="rule_only",  # 测试不打真实 LLM，判定走规则
                note="unit-test",
            )
            batch_id = batch.id

        service.run_batch(batch_id)

        with SessionLocal() as db:
            rows = db.query(ProbeSample).filter(ProbeSample.batch_id == batch_id).all()
            self.assertEqual(len(rows), len(questions) * 5)
            self.assertTrue(all(r.ok == 1 for r in rows), "mock 不应有失败样本")
            self.assertTrue(all(r.model_version == "mock-1" for r in rows))
            self.assertTrue(all(r.judge_method in ("rule", "llm") for r in rows))

        report = service.report(batch_id)
        self.assertEqual(report["status"], "done")
        engine_block = report["engines"]["mock"]
        self.assertEqual(engine_block["n"], len(questions) * 5)
        lo, hi = engine_block["mention_rate"]["ci95"]
        self.assertLessEqual(lo, engine_block["mention_rate"]["rate"])
        self.assertGreaterEqual(hi, engine_block["mention_rate"]["rate"])
        self.assertEqual(report["caliber_notice"]["memory_engines"], [])

        # 断点续跑：已完成的一批再跑一次不应产生重复样本
        service.run_batch(batch_id)
        with SessionLocal() as db:
            count = db.query(ProbeSample).filter(ProbeSample.batch_id == batch_id).count()
        self.assertEqual(count, len(questions) * 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
