import json
import unittest
from types import SimpleNamespace
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
    @patch("app.services.feeding_service.OpenAI")
    def test_generates_and_orders_platform_specific_assets(self, openai):
        response_content = json.dumps(
            {
                "assets": [
                    {
                        "channel": "zhihu",
                        "title": "徒步活动说明",
                        "content": "活动信息与住宿服务介绍",
                    },
                    {
                        "channel": "rednote",
                        "title": "恩施秋日徒步",
                        "content": "活动时间和地点以提供信息为准",
                    },
                ]
            },
            ensure_ascii=False,
        )
        openai.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=response_content)
                )
            ]
        )

        assets = feeding_service.build_assets(
            self.business,
            ["rednote", "zhihu"],
            self.brief,
            "test_provider",
        )

        self.assertEqual([asset["channel"] for asset in assets], ["rednote", "zhihu"])
        call = openai.return_value.chat.completions.create.call_args
        prompt = json.loads(call.kwargs["messages"][1]["content"])
        self.assertEqual(prompt["campaign_input"], self.brief)
        self.assertEqual(
            [item["channel"] for item in prompt["requested_platforms"]],
            ["rednote", "zhihu"],
        )

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
    @patch("app.services.feeding_service.OpenAI")
    def test_rejects_incomplete_platform_output(self, openai):
        openai.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"assets":[{"channel":"rednote","title":"标题","content":"正文"}]}'
                    )
                )
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "知乎"):
            feeding_service.build_assets(
                self.business,
                ["rednote", "zhihu"],
                self.brief,
                "test_provider",
            )

    def test_rejects_unknown_provider_before_request(self):
        with self.assertRaisesRegex(ValueError, "不支持"):
            feeding_service.build_assets(
                self.business,
                ["rednote"],
                self.brief,
                "unknown",
            )


if __name__ == "__main__":
    unittest.main()
