"""验证「停止跑批」：中途能停、已完成样本保留、续跑能继续。

用慢速 mock 适配器（每次探测 sleep 0.4s），不产生任何真实 API 调用和费用。
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest

# 必须在 import app.* 之前设置，避免污染开发库
_TMP_DB = os.path.join(tempfile.gettempdir(), "geo_cancel_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
for _k in ("DEEPSEEK_KEY", "KIMI_KEY", "DOUBAO_KEY", "ZHIPU_KEY", "QWEN_KEY", "DASHSCOPE_KEY"):
    os.environ[_k] = ""

import app.models  # noqa: E402,F401
import app.v2.models  # noqa: E402,F401
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Business  # noqa: E402
from app.probes.mock import MockAdapter  # noqa: E402
from app.v2 import service  # noqa: E402


class SlowMock(MockAdapter):
    """每条探测慢 0.4 秒，给「取消」留出触发窗口。"""

    def probe(self, question, **kwargs):  # noqa: D102
        time.sleep(0.4)
        return super().probe(question, **kwargs)


class CancelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as db:
            biz = Business(
                name="取消测试摄影",
                category="摄影",
                region="宜昌",
                alias="取消测试摄影",
            )
            db.add(biz)
            db.commit()
            cls.biz_id = biz.id

    def _make_batch(self, n_samples: int) -> int:
        questions = [
            {
                "question_key": f"T:摄影:recommend:{i}",
                "text": f"宜昌哪家摄影工作室好？{i}",
                "intent": "recommend",
                "is_control": False,
            }
            for i in range(4)
        ]
        with SessionLocal() as db:
            batch = service.create_batch(
                db,
                business_id=self.biz_id,
                engines=["mock"],
                questions=questions,
                n_samples=n_samples,
                competitors=["竞品A"],
                note="cancel-test",
            )
            return batch.id

    @staticmethod
    def _city() -> str:
        return "宜昌"

    def test_cancel_stops_early_and_keeps_finished_samples(self):
        batch_id = self._make_batch(n_samples=5)  # 4 题 × 5 = 20 条，慢速下约 8 秒
        original = service.get_adapter
        service.get_adapter = lambda *a, **k: SlowMock()  # noqa: ARG005
        try:
            t = threading.Thread(target=service.run_batch, args=(batch_id,), daemon=True)
            t.start()
            time.sleep(1.5)  # 让它先跑几条
            service.request_cancel(batch_id)
            t.join(timeout=30)
        finally:
            service.get_adapter = original

        with SessionLocal() as db:
            from app.v2.models import ProbeBatch, ProbeSample

            batch = db.get(ProbeBatch, batch_id)
            self.assertEqual(batch.status, "cancelled")
            done = db.query(ProbeSample).filter(ProbeSample.batch_id == batch_id, ProbeSample.ok == 1).count()
            pending = db.query(ProbeSample).filter(ProbeSample.batch_id == batch_id, ProbeSample.error == "pending").count()
            total = db.query(ProbeSample).filter(ProbeSample.batch_id == batch_id).count()
        # 关键：中途停下 —— 跑完了一部分，且还剩一部分没跑
        self.assertGreater(done, 0, "取消前应已完成若干条")
        self.assertGreater(pending, 0, "取消后应残留待跑样本，供续跑")
        self.assertEqual(done + pending, total)

    def test_resume_clears_cancel_flag(self):
        batch_id = self._make_batch(n_samples=5)
        service.request_cancel(batch_id)  # 先置上旧标志
        original = service.get_adapter
        service.get_adapter = lambda *a, **k: SlowMock()  # noqa: ARG005
        try:
            service.run_batch(batch_id)  # 开跑时应清掉旧标志，而不是立刻退出
        finally:
            service.get_adapter = original

        with SessionLocal() as db:
            from app.v2.models import ProbeBatch

            batch = db.get(ProbeBatch, batch_id)
            self.assertIn(batch.status, ("done", "partial"))
            self.assertFalse(service.is_cancelled(batch_id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
