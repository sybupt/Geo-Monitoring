# GEO 智能监测系统

监测企业在豆包 / DeepSeek / Kimi 等 AI 回答中的可见性（提及率），通过「投喂引擎」在知乎 / 小红书 / 微信 / 官网 / 政府等渠道发布内容，提升被 AI 引用的概率。

核心闭环：**投喂 → 收录 → 监测 → 效果对比报告**

## 功能模块

- **态势展示**：GEO 驾驶舱，KPI 卡 + 提及率趋势（折线）+ 模型对比（柱状）+ 信源分布（条形），ECharts 可视化
- **效果总览**：多模型逐题前后对比，投喂效果一目了然
- **引用洞察**：信源聚合 / 模型对比 / 引用文章 / 归因
- **问题工厂**：问题集管理与基线监测
- **投喂中心**：套餐联动、逆向诊断、覆盖度预演、标准答案、多平台素材生成与一致性校验、渠道发布及复测
- **检测报告**：投喂前后效果对比报告（支持 HTML 下载）
- **会员管理**：企业、套餐、订阅管理

## 投喂中心诊断与多源一致流程

在前端「投喂中心」(`/feeding`) 中，按以下顺序准备并执行投喂：

1. **问题诊断与口径准备**
   - 选择要争取回答的问题，填写品牌、竞品、地域和品类关键词。
   - **逆向诊断**：调用所选模型回答目标问题，显示当前提及对象及推断的未提及原因。诊断模型需在 `backend/.env` 中配置对应的 API 密钥。
   - **覆盖度预演**：检查标准答案和已关联投喂素材对品牌及相关问题关键词的覆盖情况，并提示缺失词。
   - **标准答案**：为目标问题保存真实、可核验的品牌介绍，作为各平台生成内容和一致性校验的基准。
2. **创建投喂计划**：填写计划名称、业务事实及目标渠道。若已保存标准答案，生成素材时会将它作为参考，并把所选问题关联到素材。
3. **生成多平台素材**：按各平台风格生成文案和内容指纹。
4. **多源一致性校验**：逐条比较平台文案与标准答案关键词，显示匹配/缺失要点。当前评分是关键词重叠比例，只作为口径提示，不代表语义真实性验证；达到 **80 分**后才允许素材进入审核。
5. **发布审核**：校验通过后审核素材，发布到渠道并登记公开链接。编辑素材、重新生成或修改标准答案会使相关评分失效，需要重新校验。
6. **检测复测**：发布后针对已有基线运行投喂后检测，比较提及率变化。

相关后端接口（Swagger 文档可通过 `http://localhost:8088/docs` 查看）：

- `POST /api/diagnosis/reverse/{question_id}`：逆向诊断
- `POST /api/coverage/check/{question_id}`：覆盖度预演
- `POST /api/feeding/standard_answer`：保存标准答案
- `GET /api/feeding/plan/{question_id}`：获取标准答案和平台建议
- `POST /api/feeding-plans/{plan_id}/consistency-check?question_id={question_id}`：校验计划内各平台素材

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
