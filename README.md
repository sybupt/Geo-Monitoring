# 文旅 AI GEO

面向文旅及本地企业的 GEO 运营与效果评估原型。围绕真实企业资料提供渠道素材生成与投放记录、自动化问题生成和审核、多模型前后检测及企业报告。

## 功能概览

- **投喂中心**：输入企业业务关键词、活动名称、宣传点或事件的时间/地点/内容，选择小红书、知乎、百度、Google、企业官网、政府平台、文旅局官网或微信公众号，由已配置的大模型生成平台适配文案；支持编辑文案、上传图片、预览和下载图文素材包。当前版本需人工审核和发布，尚未接入平台一键投喂。
- **自动化问题工厂**：使用已配置的大模型，根据企业地区、行业和资料生成问题候选；人工审核并启用版本化问题集。
- **多模型效果检测**：支持 Kimi 联网搜索和 DeepSeek。检测批次绑定已审核问题集版本，保留逐题回答。
- **企业效果报告**：选择企业、已发布投放计划和投喂前/后批次，生成可下载 HTML 报告；只比较同模型、同问题集，并校验前测早于发布、后测晚于发布。
- **企业看板**：工作区包括效果总览、投喂中心、问题工厂、检测报告。

## 项目结构

```text
.
├── backend/
│   ├── app/                 # FastAPI、数据库模型、监测服务
│   ├── feeding/             # GEO 内容生成、发布清单和收录跟踪
│   ├── data/                # 运行时生成的内容与渠道状态
│   ├── .env                 # 后端 API 密钥和配置
│   ├── geo.db               # SQLite 数据库（首次运行时可自动创建）
│   └── requirements.txt     # 后端依赖
├── frontend/
│   ├── dashboard.py         # Streamlit 企业看板
│   └── requirements.txt     # 看板依赖
└── README.md
```

## 安装与配置（CMD / Anaconda Prompt）

在已激活 `geo` 环境的终端中，从项目根目录一次安装前后端依赖：

```cmd
cd /d D:\Project\Geo\geo
python -m pip install -r backend\requirements.txt -r frontend\requirements.txt
```

确认 `backend/.env` 中已配置模型密钥：

```dotenv
KIMI_KEY=你的Kimi_API密钥
DEEPSEEK_KEY=你的DeepSeek_API密钥
```

`KIMI_KEY` 用于 Kimi 联网搜索，`DEEPSEEK_KEY` 用于 DeepSeek 对照监测。未配置密钥的模型不会开放启动选项。请勿将真实密钥提交到版本库。数据库默认使用 `backend/geo.db`，数据库表会在 API 启动时自动创建。

## 启动 API 和看板

需要打开两个 CMD / Anaconda Prompt 窗口，并在两个窗口中都激活 `geo` 环境。

**窗口一：启动 API**（保持窗口运行）

```cmd
cd /d D:\Project\Geo\geo\backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8088 --reload
```

API 地址是 <http://127.0.0.1:8088>，接口文档是 <http://127.0.0.1:8088/docs>。

**窗口二：启动看板**（保持窗口运行）

```cmd
cd /d D:\Project\Geo\geo\frontend
python -m streamlit run dashboard.py
```

浏览器打开看板：<http://localhost:8501>。`8501` 是看板端口；看板连接的 API 端口是 `8088`，两者用途不同。

首次打开看板时，录入真实企业名称、地区、类别、服务信息及可核实依据。所有功能从侧边栏工作区进入。

### 建议的业务闭环

1. **问题工厂**：选择已配置的模型生成候选问题，编辑、去重和审核后启用问题集版本。
2. **投喂前基线**：使用该版本问题，在“问题工厂”运行投喂前检测。
3. **投喂中心**：填写真实业务和活动/事件信息，按平台生成文案，编辑并添加图片；下载素材包，人工核实后自行发布并记录公开链接和状态。
4. **投喂后复测**：等待内容被公开检索后，使用同一问题集和模型运行投喂后检测。
5. **检测报告**：选择企业、已发布计划和前后批次，生成并下载 HTML 报告。系统校验基线在计划发布前、复测在计划发布后。

每轮监测会对固定问题集逐条调用选定模型并产生 API 用量。Kimi 使用联网搜索；DeepSeek 当前为普通对话模型，不能等同于实时网页搜索。前后批次必须是同一企业、同一模型、同一问题文本集合，服务端才允许生成效果报告。

> 当前是单实例业务原型：虽可在报告中选择企业，但尚未实现登录、客户组织、租户隔离或角色权限，不能直接作为多家付费客户的安全生产服务部署。正式商业化前必须增加身份认证、组织/企业授权、操作审计、密钥管理与 API 访问控制。

## API 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | API 健康检查 |
| `GET` | `/api/businesses` | 企业列表 |
| `POST` | `/api/businesses` | 创建企业资料 |
| `PUT` | `/api/businesses/{business_id}` | 更新企业资料及事实依据 |
| `GET` | `/api/providers` | 模型列表及密钥配置状态 |
| `GET` | `/api/questions?region=湖北恩施` | 获取某地区问题及通用问题 |
| `POST` | `/api/questions` | 批量添加问题 |
| `POST` | `/api/question-sets/generate` | 调用模型生成待审核候选问题 |
| `GET` | `/api/question-sets?business_id=1` | 查询企业的问题集版本 |
| `POST` | `/api/question-sets/{id}/approve` | 保存审核结果并启用问题集 |
| `POST` | `/api/feeding-plans` | 根据企业资料、活动输入和平台列表调用模型生成文案 |
| `GET` | `/api/feeding-plans?business_id=1` | 查看计划、素材、图片和发布记录 |
| `PUT` | `/api/feeding-assets/{id}` | 编辑平台素材标题和正文 |
| `POST` | `/api/feeding-assets/{id}/images` | 为素材上传图片 |
| `GET` | `/api/feeding-assets/{id}/download` | 下载包含文案和图片的图文素材 ZIP |
| `POST` | `/api/feeding-assets/{id}/review` | 人工审核通过渠道素材 |
| `PUT` | `/api/feeding-assets/{id}/publish` | 登记人工发布链接和时间 |
| `POST` | `/api/monitor-runs` | 启动投喂前或投喂后监测（后台运行） |
| `GET` | `/api/monitor-runs?business_id=1` | 查询监测批次和状态 |
| `GET` | `/api/monitor-runs/{run_id}` | 查询批次汇总及逐题回答 |
| `GET` | `/api/dashboard/overview?business_id=1` | 看板所需的趋势及前后对比汇总 |
| `GET` | `/api/dashboard/summary?business_id=1` | 企业最新一次监测汇总 |
| `GET` | `/api/visibility/trend?business_id=1` | 企业历史提及率趋势 |
| `GET` | `/api/mentions/latest?business_id=1` | 最新监测回答及提及情况 |
| `POST` | `/api/reports` | 选择企业、已发布计划和前后批次，生成固定快照报告 |
| `GET` | `/api/reports?business_id=1` | 查询企业已保存报告 |
| `GET` | `/api/reports/{id}/html` | 下载 HTML 效果报告 |

## GEO 内容辅助流程

以下命令均在 `backend/` 目录运行（CMD），并使用已激活的 `geo` 环境：

```cmd
cd /d D:\Project\Geo\geo\backend

# 用看板创建真实企业资料后，以同一个企业 ID 生成网页、知乎及短内容素材
python feeding\producer.py --business-id 1

# 生成需要人工完成的发布任务清单
python feeding\distribute.py --business-id 1

# 发布并确认内容后，人工登记发布/收录状态
python feeding\track.py --business-id 1
```

内容生成文件默认写入 `backend/data/content/`。发布渠道需要人工确认和操作；项目不会向第三方平台自动发布内容。

> 模型是否推荐企业，取决于资料真实性、公开内容是否被搜索引擎/联网模型检索、问题相关性和模型自身排序。系统可以记录投喂前后的观测变化，但不能保证推荐次数显著增加，也不会通过编造背书来制造效果。

## 数据与实现说明

- SQLite 文件路径默认为 `backend/geo.db`；新增的问题集、投放计划、渠道素材和报告表在 API 启动时创建，旧运行表按需补充新字段，不会删除已有业务记录。
- API 启动时会为旧数据库补充投喂阶段和失败原因字段；已有监测批次标记为历史记录，不会误算为投喂前基线。
- `python -m app.seed` 只创建表，不写演示数据。API 启动时会为旧库补充监测阶段、错误信息和企业资料字段。
- 数据库默认是 `backend/geo.db`；`DATABASE_URL` 可用于指定其他 SQLAlchemy 数据库地址，需自行安装对应驱动。
- 当前无用户认证和租户隔离。API 可访问实例中的企业和报告数据，不应直接暴露到公网或向不同客户开放。
- 投喂图片存放在 `backend/data/feeding_images/`；每张图片最大 10 MB，仅支持 JPG、PNG 和 WebP。
- 渠道发布状态由操作人员登记；尚未接入各平台授权发布 API，不会自动登录或发布。一键投喂属于后续阶段，需要逐个平台完成授权、合规审核和接口接入。
- 提及率是基于回答中是否包含企业名称或别名的简单匹配结果，不代表模型排名、推荐质量或真实客流效果。
- 投喂前后对比是观测指标，不构成内容投放带来因果提升的保证；模型回答、索引和搜索结果会随时间波动。
