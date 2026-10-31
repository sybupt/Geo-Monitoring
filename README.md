# GEO 智能监测

企业 AI 可见性监测与投喂平台——让本地企业在用户向 AI（豆包/DeepSeek/Kimi）提问时被推荐。

## 架构

- **后端**：FastAPI + SQLAlchemy + SQLite（`backend/`）
- **前端**：Vue3 + Vite（`frontend/`）
- **双引擎**：
  - 投喂引擎：多渠道内容生成（小红书/知乎/百度/微信公众号等，按会员套餐匹配平台）
  - 监测引擎：多模型提及率追踪（投喂前基线 vs 投喂后复测对比）

## 核心功能

- 会员管理：企业会员 + 套餐（试用/基础/标准/旗舰）+ 缴费状态 + 平台评分算法
- 问题工厂：AI 生成候选问题 → 人工审核 → 固定问题集
- 投喂中心：生成文案 → 预览发布效果 → 审核 → 引导发布 → 记录链接
- 检测报告：前后对比 + HTML 报告导出

## 本地开发

```bash
# 后端
cd backend
uvicorn app.main:app --reload --reload-dir app --port 8088

# 前端
cd frontend
npm install
npm run dev
