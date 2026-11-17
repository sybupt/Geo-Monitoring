"""纯 numpy 逻辑回归排序器（含 L2、Adam 优化、模型持久化）。

不用 sklearn 是有意的：整条链路的依赖面越小，越容易在离线环境与容器里复现。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np

from ..config import LR_EPOCHS, LR_L2, LR_LR, MODEL_DIR


@dataclass
class Ranker:
    features: list[str]
    w: np.ndarray | None = None
    b: float = 0.0
    mu: np.ndarray | None = None
    sigma: np.ndarray | None = None
    history: list[dict] = field(default_factory=list)

    def _std(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mu) / self.sigma

    def _init(self, d: int) -> None:
        if self.w is None:
            self.w = np.zeros(d, dtype=np.float64)

    def fit(self, X: np.ndarray, y: np.ndarray, epochs: int = LR_EPOCHS, lr: float = LR_LR,
            l2: float = LR_L2, verbose: bool = False) -> "Ranker":
        n, d = X.shape
        self.mu = X.mean(axis=0)
        self.sigma = X.std(axis=0)
        self.sigma[self.sigma < 1e-8] = 1.0
        X = self._std(X)
        self._init(d)
        m = np.zeros(d)
        v = np.zeros(d)
        b1, b2, eps = 0.9, 0.999, 1e-8
        for t in range(1, epochs + 1):
            z = X @ self.w + self.b
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
            grad_w = X.T @ (p - y) / n + l2 * self.w
            grad_b = float(np.mean(p - y))
            m = b1 * m + (1 - b1) * grad_w
            v = b2 * v + (1 - b2) * grad_w ** 2
            m_hat = m / (1 - b1 ** t)
            v_hat = v / (1 - b2 ** t)
            self.w -= lr * m_hat / (np.sqrt(v_hat) + eps)
            self.b -= lr * grad_b
            if verbose and (t % 50 == 0 or t == 1):
                loss = -np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))
                self.history.append({"epoch": t, "loss": round(float(loss), 5)})
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        self._init(X.shape[1])
        if self.mu is not None and self.sigma is not None:
            X = (X - self.mu) / self.sigma
        z = X @ self.w + self.b
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

    def save(self, variant: str = "full") -> None:
        MODEL_DIR.mkdir(exist_ok=True)
        np.savez(MODEL_DIR / f"ranker_{variant}.npz", w=self.w, b=np.array([self.b]),
                 mu=self.mu, sigma=self.sigma)
        (MODEL_DIR / f"ranker_{variant}.json").write_text(
            json.dumps({"features": self.features, "history": self.history}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, variant: str = "full") -> "Ranker | None":
        wpath = MODEL_DIR / f"ranker_{variant}.npz"
        mpath = MODEL_DIR / f"ranker_{variant}.json"
        if not wpath.exists() or not mpath.exists():
            return None
        data = np.load(wpath)
        meta = json.loads(mpath.read_text(encoding="utf-8"))
        return cls(features=meta["features"], w=data["w"], b=float(data["b"][0]),
                   mu=data["mu"], sigma=data["sigma"], history=meta.get("history", []))

    def top_weights(self, k: int = 8) -> list[dict]:
        if self.w is None:
            return []
        order = np.argsort(-np.abs(self.w))[:k]
        return [{"feature": self.features[i], "weight": round(float(self.w[i]), 4)} for i in order]


def auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    y_true = np.asarray(y_true)
    y_score = np.asarray(y_score)
    pos = y_score[y_true == 1]
    neg = y_score[y_true == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    # 秩和法，正类平均秩
    order = np.argsort(y_score)
    ranks = np.empty(len(y_score), dtype=np.float64)
    ranks[order] = np.arange(1, len(y_score) + 1)
    # 处理并列：用平均秩
    uniq, inv, counts = np.unique(y_score, return_inverse=True, return_counts=True)
    for idx, c in enumerate(counts):
        if c > 1:
            mask = inv == idx
            ranks[mask] = ranks[mask].mean()
    sum_pos = ranks[y_true == 1].sum()
    return float((sum_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))
