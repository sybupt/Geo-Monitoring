"""文心（千帆）与元宝（浏览器）适配器测试。

这两家的风险点不同：
- 文心：两种鉴权路径 + 两套响应结构（v1 `result` / v2 `choices`），解析要都对；
- 元宝：没有 API，最怕「抓不到却当成功」——空答案会被判成未提及，污染整张报表。
"""

import json
import os
import tempfile
import unittest

from app.probes import implemented_engines, get_adapter
from app.probes.base import ProbeError
from app.probes.wenxin import WenxinAdapter
from app.probes.wenxin import _parse as wenxin_parse
from app.probes.yuanbao import YuanbaoAdapter


class WenxinAuthTests(unittest.TestCase):
    def test_api_key_prefers_v2(self):
        adapter = WenxinAdapter(api_key="bce-v3/ALTAK-x", ak="a", sk="b")
        self.assertEqual(adapter.auth_mode, "v2_bearer")
        self.assertTrue(adapter.capabilities()["configured"])

    def test_ak_sk_falls_back_to_oauth(self):
        adapter = WenxinAdapter(api_key="", ak="ak-x", sk="sk-x")
        self.assertEqual(adapter.auth_mode, "v1_oauth")
        self.assertTrue(adapter.capabilities()["configured"])

    def test_no_credentials(self):
        adapter = WenxinAdapter(api_key="", ak="", sk="")
        self.assertEqual(adapter.auth_mode, "none")
        self.assertFalse(adapter.capabilities()["configured"])
        with self.assertRaises(ProbeError) as ctx:
            adapter.probe("清镇哪家摄影工作室好")
        self.assertIn("QIANFAN_API_KEY", str(ctx.exception))


class WenxinParseTests(unittest.TestCase):
    def test_v2_choices_with_search_info(self):
        data = {
            "id": "as-xxx",
            "model": "ernie-4.5-turbo-32k",
            "choices": [{"message": {"role": "assistant", "content": "清镇可以看康美口腔。"}}],
            "search_info": {
                "search_results": [
                    {"index": 1, "url": "https://www.dianping.com/shop/1", "title": "清镇口腔排名"},
                    {"index": 2, "url": "https://baike.baidu.com/item/x", "title": "百科"},
                ]
            },
            "usage": {"total_tokens": 120},
        }
        answer, cites, searched = wenxin_parse(data, "answer_level")
        self.assertIn("康美口腔", answer)
        self.assertTrue(searched)
        self.assertEqual(len(cites), 2)
        self.assertEqual(cites[0].domain, "dianping.com")
        self.assertEqual(cites[0].citation_type, "answer_level")

    def test_v1_result_and_dedup(self):
        data = {
            "result": "推荐 A 工作室。",
            "search_info": {
                "search_results": [
                    {"index": 1, "url": "https://www.xiaohongshu.com/a", "title": "笔记"},
                    {"index": 2, "url": "https://www.xiaohongshu.com/a", "title": "重复"},
                ]
            },
        }
        answer, cites, searched = wenxin_parse(data, "answer_level")
        self.assertEqual(answer, "推荐 A 工作室。")
        self.assertTrue(searched)
        self.assertEqual(len(cites), 1)  # 同 URL 去重

    def test_references_field_form(self):
        """部分版本的 v2 把来源放在顶层 references。"""
        data = {
            "choices": [{"message": {"content": "答案"}}],
            "references": [{"url": "https://www.zhihu.com/q/1", "title": "知乎回答", "website": "知乎"}],
        }
        _, cites, searched = wenxin_parse(data, "answer_level")
        self.assertTrue(searched)
        self.assertEqual(cites[0].domain, "zhihu.com")
        self.assertEqual(cites[0].site_name, "知乎")

    def test_no_search_results_means_not_searched(self):
        """没返回 search_info 就是没检索，必须如实记录，不能假设检索过。"""
        data = {"choices": [{"message": {"content": "纯模型回答"}}]}
        answer, cites, searched = wenxin_parse(data, "answer_level")
        self.assertEqual(answer, "纯模型回答")
        self.assertEqual(cites, [])
        self.assertFalse(searched)


class YuanbaoAdapterTests(unittest.TestCase):
    def test_missing_state_blocks_run(self):
        adapter = YuanbaoAdapter(state_path="D:/__not_exists__/state.json")
        self.assertFalse(adapter.capabilities()["configured"])
        with self.assertRaises(ProbeError) as ctx:
            adapter.probe("清镇哪家摄影好")
        self.assertIn("yuanbao_login.py", str(ctx.exception))

    def test_state_file_marks_configured(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"cookies": [], "origins": []}, f)
            path = f.name
        try:
            adapter = YuanbaoAdapter(state_path=path)
            self.assertTrue(adapter.capabilities()["configured"])
            self.assertEqual(adapter.tier, "L3")
        finally:
            os.unlink(path)


class FullCoverageTests(unittest.TestCase):
    def test_all_seven_engines_wired(self):
        wired = set(implemented_engines())
        self.assertEqual(
            wired, {"doubao", "kimi", "deepseek", "qwen", "zhipu", "wenxin", "yuanbao"}
        )

    def test_every_engine_instantiates_with_scheduler_kwargs(self):
        for eng in ("wenxin", "yuanbao"):
            adapter = get_adapter(eng, brand_name="x", competitors=["y"])
            self.assertEqual(adapter.engine_id, eng)


if __name__ == "__main__":
    unittest.main(verbosity=2)
