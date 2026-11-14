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
- 模型：DeepSeek、Kimi（`config.py` 的 `PROVIDERS` 可扩展）## 目录结构
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

**一键启动（推荐）**：双击根目录 `start-dev.bat`，自动拉起前后端并打开浏览器。

```bash
# 后端（端口 8088）
cd backend
uvicorn app.main:app --reload --reload-dir app --port 8088

# 前端（端口 5173，已代理 /api → 8088）
cd frontend
npm install
npm run dev
```

启动后打开 http://localhost:5173 ：

| 页面 | 路径 | 说明 |
|---|---|---|
| 可信度量台 | `/v2/console` | 引擎覆盖矩阵、发起多采样跑批、报告（区间/SoV/信源分布）、DID 归因 |
| 渠道分发 | `/v2/channel` | 渠道画像、账号登记、发布任务（复制文案 → 去平台发布 → 回填链接 → 收录回查） |
| 态势展示等旧页面 | `/situation` 等 | 仍走旧单次采样接口，数字不可作为交付依据 |

Swagger：http://localhost:8088/docs

---

## v2 可信度量层（P0）

旧的 `monitor_service` 有三个会直接在客户面前翻车的问题：**每题只问一次**、**用字符串 `in`
判提及**、**引用靠正文正则抓**。v2 不动旧接口，另起一层把这三件事做对。

### 目录

```
backend/app/
├── probes/                 # 探测量层：一个引擎一个适配器
│   ├── base.py             # 统一契约 + 引用语义分级 + 令牌桶 + 重试
│   ├── doubao.py           # 方舟 /responses + web_search，读 annotations
│   ├── kimi.py             # $web_search 两轮 tool call
│   ├── deepseek.py         # 官方 API 不联网，显式标注 memory_mode
│   ├── qwen.py             # DashScope 原生协议，读 search_info.search_results
│   ├── zhipu.py            # GLM web_search 工具 + 正文 ref_N 角标
│   ├── mock.py             # 不花钱跑通全链路（数据不可对外）
│   └── registry.py         # 七引擎画像 + 按签名过滤调度参数
├── judge.py                # 判定：规则初筛 + LLM 终判 + 排名推断
├── metrics.py              # Wilson 区间 / 提及率 / 首位率 / SoV / 信源分布 / DID
├── industry/local_life.py  # 本地生活问题矩阵 + 渠道画像
└── v2/                     # 批次、采样、渠道账号、发布任务、人工抽检
    ├── models.py
    ├── service.py          # 多采样跑批：引擎并行、引擎内串行、断点续跑
    └── api.py              # /api/v2/*
```

### 三条口径纪律（对外汇报前必须遵守）

1. **一次采样一行**。每题每引擎采 N 次（默认 5），报告一律给 Wilson 95% 区间，不给单点。
2. **引用不混算**。`answer_level`（豆包，URL 挂在句子上）与 `retrieval_pool`（Kimi/千问，
   只拿到检索候选）分组统计；`memory_mode` 的引擎（DeepSeek 不联网）单独展示，不与检索型排名。
3. **没有对照组不声称因果**。`/api/v2/did` 用「处理组（投喂题）− 对照组（同城不同品类题）」
   做双重差分，区间跨 0 就写「无法证明是投喂带来的」。

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v2/engines` | 七引擎能力矩阵（是否接线/是否配 key/引用语义/数据等级） |
| POST | `/api/v2/questions/preview` | 按「地域×品类×意图」生成问题集，含对照组 |
| POST | `/api/v2/batches` | 起一次多采样跑批（异步） |
| GET | `/api/v2/batches/{id}` | 进度 + 完整报告 |
| GET | `/api/v2/batches/{id}/report` | 报告（带口径告警）+ 分题明细 |
| GET | `/api/v2/batches/{id}/samples` | 原始回答与逐条判定，可人工复核 |
| POST | `/api/v2/batches/{id}/resume` | 断点续跑 |
| GET | `/api/v2/did?before=&after=` | 两次跑批的 DID 归因 |
| GET | `/api/v2/channels` | 本地生活渠道画像（api/rpa/guide 三种发布模式） |
| POST | `/api/v2/channel-accounts` | 渠道账号绑定（只存凭据引用，不存明文） |
| POST | `/api/v2/publish-tasks` | 生成投喂任务：guide 模式返回平台登录/发布页 + 一键复制文案 |
| POST | `/api/v2/publish-tasks/{id}/published` | 用户发完回填链接 |
| POST | `/api/v2/publish-tasks/{id}/check` | 收录回查（第一层：链接是否可访问） |
| POST | `/api/v2/judge-audits` | 人工抽检 |
| GET | `/api/v2/judge-quality?batch_id=` | 抽检一致率，<90% 不许出报告 |

### 引擎覆盖现状（诚实版）

| 引擎 | 状态 | 引用语义 | 备注 |
|---|---|---|---|
| 豆包 | 已接线 | answer_level / L1 | 必须 `/responses` + `web_search`；`max_output_tokens` 给小了会空返回 |
| Kimi | 已接线 | retrieval_pool / L1 | `$web_search` 两轮 tool call |
| DeepSeek | 已接线 | none / L2 | 官方 API 不联网 = 模型记忆口径 |
| 通义千问 | 已接线 | retrieval_pool / L1 | DashScope 原生协议 + `enable_search`，读 `output.search_info.search_results`；OpenAI 兼容协议拿不到检索面 |
| 智谱清言 | 已接线 | answer_level / L1 | GLM `web_search` 工具，正文 `ref_N` 角标；结果数组多路径探测，抓不到 URL 时宁缺毋假 |
| 文心一言 | 已接线 | answer_level / L1 | 千帆 v2 API Key 直连（AK/SK 走 OAuth 兜底）；搜索增强仅 ERNIE 4.5 支持 |
| 腾讯元宝 | 已接线 | answer_level / L3 | 无公开 API：Playwright 驱动真实浏览器，先跑 `scripts/yuanbao_login.py` 扫码登录；DOM 会随发版失效 |
| Mock | 演练用 | answer_level | 报表带 MOCK 告警，不可作为效果证明 |

> 全部七家已接线。每家的「缺什么才能跑」在 `/api/v2/engines` 里如实返回：
> API 引擎看有没有配 Key，元宝看登录态文件在不在。建批次时没配好的引擎直接 400 拦截。
> 元宝的数据等级是 L3（DOM 会变），给客户的报告里要标注，不要和 L1 混排。

### 本地验证（不需要任何 API Key）

```bash
cd backend
python -m unittest tests.test_v2_core tests.test_probes_new -v   # 28 个用例，含 mock 端到端
```

起服务后用 mock 跑一份完整报告：

```bash
curl -s -X POST http://127.0.0.1:8088/api/v2/batches \
  -H 'Content-Type: application/json' \
  -d '{"business_id":2,"engines":["mock"],"n_samples":5,"judge_mode":"rule_only",
       "city":"清镇","categories":["口腔诊所"],"control_categories":["火锅"]}'
```

换成真实引擎只需把 `engines` 改成 `["doubao","kimi","deepseek"]` 并配好 `.env`。

### 已知边界

- 七家引擎全部接线（含元宝浏览器自动化 L3）。元宝跑批前需人工扫码登录一次，登录态失效会在跑批中明确报错并提示重跑登录脚本。
- 智谱的搜索结果数组在不同模型版本间位置会变（顶层 `web_search` / `search_result` / `tool_calls[].search_result`），适配器已做多路径探测；抓不到 URL 时引用记空、正文角标数进 raw，不会编造信源。
- 通义的 `search_info` 是检索候选面，不能严格证明答案采纳了哪条来源；报表按 citation_type 分组，不与豆包的答案级引用加总。
- 发布仍是半自动：`guide` 模式只到「给出平台发布页 + 一键复制文案」，真实自动发帖需要 RPA 与账号池。
- SQLite 只适合单机验证；服务多个企业（多租户）并发跑批前要换 PostgreSQL，`business_id` 已是隔离边界。
- 判定的 LLM 版本、模型版本都已落库；模型静默升级时**不要跨版本比基线**，要重跑一次基线再对比。
