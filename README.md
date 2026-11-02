# GEO 智能监测系统

监测企业在豆包 / DeepSeek / Kimi 等 AI 回答中的可见性（提及率），通过「投喂引擎」在知乎 / 小红书 / 微信 / 官网 / 政府等渠道发布内容，提升被 AI 引用的概率。

核心闭环：**投喂 → 收录 → 监测 → 效果对比报告**

## 功能模块

- **态势展示**：GEO 驾驶舱，KPI 卡 + 提及率趋势（折线）+ 模型对比（柱状）+ 信源分布（条形），ECharts 可视化
- **效果总览**：多模型逐题前后对比，投喂效果一目了然
- **引用洞察**：信源聚合 / 模型对比 / 引用文章 / 归因
- **问题工厂**：问题集管理与基线监测
- **投喂中心**：套餐联动、内容生成、渠道发布、投喂记录与复测
- **检测报告**：投喂前后效果对比报告（支持 HTML 下载）
- **会员管理**：企业、套餐、订阅管理

## 技术栈

- 后端：FastAPI + SQLAlchemy + SQLite
- 前端：Vue3 + Vite + ECharts
- 部署：Nginx + uvicorn
- 模型：DeepSeek、Kimi（`config.py` 的 `PROVIDERS` 可扩展）

## 目录结构
geo/
├── backend/
│ ├── app/
│ │ ├── main.py # FastAPI 入口与全部接口
│ │ ├── models.py # SQLAlchemy 模型
│ │ ├── database.py # 数据库连接
│ │ ├── config.py # 配置与模型 PROVIDERS
│ │ └── services/ # 业务逻辑（monitor / feeding / question）
│ └── requirements.txt
├── frontend/
│ ├── src/views/ # 页面组件
│ ├── src/router/ # 路由
│ └── package.json
└── geo.db # SQLite 数据库（.gitignore，不入库）


## 本地开发

```bash
# 后端（端口 8088）
cd backend
uvicorn app.main:app --reload --reload-dir app --port 8088

# 前端
cd frontend
npm install
npm run dev


