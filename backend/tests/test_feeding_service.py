import json
import unittest
from unittest.mock import patch

from app.models import Business
from app.services import feeding_service


class FeedingServiceTests(unittest.TestCase):
    def setUp(self):
        self.business = Business(
            name="恩施山居",
            region="湖北恩施",
            category="民宿",
            description="提供住宿服务",
        )
        self.brief = {
            "business_keywords": "恩施民宿",
            "activity_name": "秋季徒步",
            "promotional_points": "提供住宿",
            "event_time": "2026年10月18日",
            "event_location": "湖北恩施",
            "event_description": "举办徒步活动",
        }

    @patch.dict(
        feeding_service.PROVIDERS,
        {
            "test_provider": {
                "label": "测试模型",
                "model": "test-model",
                "key": "test-key",
                "base_url": "https://example.invalid",
            }
        },
        clear=True,
    )
    @patch("app.services.feeding_service.ask_text")
    def test_generates_and_orders_platform_specific_assets(self, ask_text):
        ask_text.side_effect = [
            json.dumps(
                {"title": "恩施秋日徒步", "content": "恩施山居提供住宿服务"},
                ensure_ascii=False,
            ),
            json.dumps(
                {"title": "徒步活动说明", "content": "恩施山居位于湖北恩施"},
                ensure_ascii=False,
            ),
        ]

        assets = feeding_service.build_assets(
            self.business,
            ["rednote", "zhihu"],
            self.brief,
            "test_provider",
            "恩施山居位于湖北恩施，提供住宿服务。",
        )

        self.assertEqual([asset["channel"] for asset in assets], ["rednote", "zhihu"])
        self.assertEqual([asset["title"] for asset in assets], ["恩施秋日徒步", "徒步活动说明"])
        self.assertEqual(ask_text.call_count, 2)
        prompts = [call.args[1] for call in ask_text.call_args_list]
        self.assertIn("标准答案参考", prompts[0])
        self.assertIn("恩施民宿", prompts[0])
        self.assertIn("小红书", prompts[0])
        self.assertIn("知乎", prompts[1])

    @patch.dict(
        feeding_service.PROVIDERS,
        {
            "test_provider": {
                "label": "测试模型",
                "model": "test-model",
                "key": "test-key",
                "base_url": "https://example.invalid",
            }
        },
        clear=True,
    )
    @patch("app.services.feeding_service.ask_text", side_effect=RuntimeError("模型不可用"))
    def test_wraps_generation_errors_with_platform_name(self, ask_text):
        with self.assertRaisesRegex(RuntimeError, "知乎素材生成失败"):
            feeding_service.build_assets(
                self.business,
                ["zhihu"],
                self.brief,
                "test_provider",
            )

    def test_rejects_unknown_provider_before_request(self):
        with self.assertRaisesRegex(ValueError, "未知模型"):
            feeding_service.build_assets(
                self.business,
                ["rednote"],
                self.brief,
                "unknown",
            )

    def test_consistency_report_identifies_missing_standard_terms(self):
        report = feeding_service.consistency_report(
            "提供住宿服务，适合秋季徒步游客。",
            "恩施山居位于湖北恩施，提供住宿服务。",
        )

        self.assertLess(report["score"], 1.0)
        self.assertTrue(report["matched_terms"])
        self.assertTrue(report["missing_terms"])
        self.assertEqual(
            feeding_service.consistency_score(
                "恩施山居位于湖北恩施，提供住宿服务。",
                "恩施山居位于湖北恩施，提供住宿服务。",
            ),
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
