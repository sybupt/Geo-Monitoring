"""智谱 / 通义适配器的解析与注册测试。

重点不是「能不能跑通网络」，而是**解析是否会产生假数据**：
- 拿不到 URL 列表时，必须 citations 为空，而不是塞占位链接；
- 引用的 citation_type 必须与引擎真实语义一致，不许把检索面当答案级用；
- 没配 Key 时必须在 probe 阶段抛错，不能返回空答案被算成「未提及」。
"""

import unittest

from app.probes import get_adapter, implemented_engines
from app.probes.base import CITATION_ANSWER_LEVEL, CITATION_RETRIEVAL_POOL, ProbeError
from app.probes.qwen import QwenAdapter
from app.probes.qwen import _parse as qwen_parse
from app.probes.zhipu import ZhipuAdapter
from app.probes.zhipu import _parse as zhipu_parse


class ZhipuParseTests(unittest.TestCase):
    def test_answer_level_citation_with_ref_position(self):
        data = {
            "choices": [{"message": {"content": "清镇看牙可以选康美口腔。[来源：ref_1] 其次是仁爱。[来源：ref_2]"}}],
            "web_search": [
                {
                    "title": "清镇口腔医院排名",
                    "link": "https://www.dianping.com/shop/123",
                    "media": "大众点评",
                    "publish_date": "2026-05-01",
                    "refer": "ref_1",
                },
                {"title": "仁爱口腔", "link": "https://www.zhihu.com/q/9", "media": "知乎", "refer": "ref_2"},
            ],
        }
        answer, cites, searched, refs = zhipu_parse(data, CITATION_ANSWER_LEVEL)
        self.assertIn("康美口腔", answer)
        self.assertTrue(searched)
        self.assertEqual(refs, 2)
        self.assertEqual(len(cites), 2)
        self.assertEqual(cites[0].domain, "dianping.com")
        self.assertEqual(cites[0].citation_type, CITATION_ANSWER_LEVEL)
        # ref_1 在正文里的位置必须早于 ref_2，否则位置加权是错的
        self.assertGreater(cites[1].position, cites[0].position)

    def test_ref_marks_without_urls_yields_no_fake_citations(self):
        """只有角标、没回传结果数组时，绝不能编造引用。"""
        data = {"choices": [{"message": {"content": "推荐 A。[来源：ref_1] 也推荐 B。[来源：ref_2]"}}]}
        answer, cites, searched, refs = zhipu_parse(data, CITATION_ANSWER_LEVEL)
        self.assertTrue(answer)
        self.assertTrue(searched)  # 检索确实发生了
        self.assertEqual(len(cites), 0)  # 但没有任何可用的 URL
        self.assertEqual(refs, 2)

    def test_tool_calls_path(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": "见 ref_1",
                        "tool_calls": [
                            {
                                "search_result": [
                                    {"title": "t", "link": "https://www.zhihu.com/question/1", "media": "知乎", "refer": "ref_1"}
                                ]
                            }
                        ],
                    }
                }
            ]
        }
        _, cites, searched, _ = zhipu_parse(data, CITATION_ANSWER_LEVEL)
        self.assertTrue(searched)
        self.assertEqual(len(cites), 1)
        self.assertEqual(cites[0].domain, "zhihu.com")

    def test_dedup_same_url(self):
        data = {
            "choices": [{"message": {"content": "x ref_1"}}],
            "search_result": [
                {"title": "a", "link": "https://a.com/1", "refer": "ref_1"},
                {"title": "a again", "link": "https://a.com/1", "refer": "ref_2"},
            ],
        }
        _, cites, _, _ = zhipu_parse(data, CITATION_ANSWER_LEVEL)
        self.assertEqual(len(cites), 1)


class QwenParseTests(unittest.TestCase):
    def test_message_format_with_search_info(self):
        data = {
            "output": {
                "choices": [{"message": {"content": "清镇口腔推荐康美口腔[ref_1]。"}, "finish_reason": "stop"}],
                "search_info": {
                    "search_results": [
                        {
                            "index": 1,
                            "title": "清镇口腔哪家好",
                            "url": "https://www.xiaohongshu.com/explore/abc",
                            "site_name": "小红书",
                        }
                    ]
                },
            },
            "usage": {"total_tokens": 100},
        }
        answer, cites, searched = qwen_parse(data, CITATION_RETRIEVAL_POOL)
        self.assertIn("康美口腔", answer)
        self.assertTrue(searched)
        self.assertEqual(len(cites), 1)
        self.assertEqual(cites[0].domain, "xiaohongshu.com")
        self.assertEqual(cites[0].site_name, "小红书")
        # 通义是检索面，语义不许升格成答案级
        self.assertEqual(cites[0].citation_type, CITATION_RETRIEVAL_POOL)
        self.assertGreater(cites[0].position, 0)

    def test_text_format_fallback(self):
        data = {"output": {"text": "纯文本回答", "search_info": {"search_results": []}}}
        answer, cites, searched = qwen_parse(data, CITATION_RETRIEVAL_POOL)
        self.assertEqual(answer, "纯文本回答")
        self.assertEqual(cites, [])
        self.assertFalse(searched)  # 没检索到，必须如实记录

    def test_no_search_info(self):
        data = {"output": {"choices": [{"message": {"content": "回答"}}]}}
        _, cites, searched = qwen_parse(data, CITATION_RETRIEVAL_POOL)
        self.assertEqual(cites, [])
        self.assertFalse(searched)


class AdapterContractTests(unittest.TestCase):
    def test_missing_key_raises_instead_of_empty_answer(self):
        """没 Key 必须抛错：空答案会被判成「未提及」，直接污染提及率。"""
        with self.assertRaises(ProbeError):
            ZhipuAdapter(api_key="").probe("清镇哪家口腔好")
        with self.assertRaises(ProbeError):
            QwenAdapter(api_key="").probe("清镇哪家口腔好")

    def test_capabilities_report_unconfigured(self):
        self.assertFalse(ZhipuAdapter(api_key="").capabilities()["configured"])
        self.assertFalse(QwenAdapter(api_key="").capabilities()["configured"])

    def test_citation_semantics(self):
        self.assertEqual(ZhipuAdapter(api_key="x").citation_type, CITATION_ANSWER_LEVEL)
        self.assertEqual(QwenAdapter(api_key="x").citation_type, CITATION_RETRIEVAL_POOL)


class RegistryTests(unittest.TestCase):
    def test_all_engines_wired(self):
        wired = set(implemented_engines())
        self.assertEqual(
            wired, {"doubao", "kimi", "deepseek", "qwen", "zhipu", "wenxin", "yuanbao"}
        )

    def test_unwired_engine_still_refuses(self):
        """注册表里没登记的引擎 id 仍要明确拒绝，不能静默当成某个默认引擎。"""
        with self.assertRaises(ProbeError) as ctx:
            get_adapter("no-such-engine")
        self.assertIn("未知引擎", str(ctx.exception))

    def test_new_engines_resolvable(self):
        self.assertEqual(get_adapter("zhipu", api_key="k").engine_id, "zhipu")
        self.assertEqual(get_adapter("qwen", api_key="k").engine_id, "qwen")

    def test_调度层多传参数不会炸(self):
        """回归：create_batch 会统一传 brand_name/competitors，真实引擎签名里没有这两个参数。

        以前直接透传导致建批次 500（只有 mock 接得住），用 mock 跑测试永远发现不了。
        """
        for eng in ("doubao", "kimi", "deepseek", "zhipu", "qwen"):
            adapter = get_adapter(eng, brand_name="清镇康美口腔", competitors=["仁爱口腔"])
            self.assertEqual(adapter.engine_id, eng)
        mock = get_adapter("mock", brand_name="清镇康美口腔", competitors=["仁爱口腔"])
        self.assertEqual(mock.engine_id, "mock")


if __name__ == "__main__":
    unittest.main(verbosity=2)
