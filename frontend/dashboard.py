from html import escape
import os

import pandas as pd
import requests
import streamlit as st


API = os.getenv("GEO_API_URL", "http://localhost:8088").rstrip("/")
PROVIDER_LABELS = {
    "kimi_search": "Kimi 联网搜索",
    "deepseek": "DeepSeek",
}
FEEDING_CHANNEL_LABELS = {
    "rednote": "小红书",
    "zhihu": "知乎",
    "baidu": "百度",
    "google": "Google",
    "official_site": "企业官网",
    "government": "政府平台投稿",
    "tourism_bureau": "文旅局官网投稿",
    "wechat": "微信公众号",
}
STAGE_LABELS = {"baseline": "投喂前", "after": "投喂后", "legacy": "历史记录"}

st.set_page_config(
    page_title="AI 搜索可见性 | GEO",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    """
    <style>
    :root { --ink:#182d2a; --muted:#70817b; --line:#e4ebe6; --green:#116b55; --mint:#e9f5ef; }
    html, body, [class*="css"] { font-family:'Segoe UI','Microsoft YaHei',sans-serif; }
    .stApp { background:#f7f9f7; color:var(--ink); }
    .block-container { max-width:1440px; padding-top:2rem; padding-bottom:3rem; }
    [data-testid="stSidebar"] { background:#f0f5f1; border-right:1px solid var(--line); }
    [data-testid="stMetric"] { background:white; border:1px solid var(--line); border-radius:14px; padding:18px 20px; }
    [data-testid="stMetricLabel"] { color:var(--muted); }
    [data-testid="stMetricValue"] { color:var(--ink); }
    .hero { background:linear-gradient(115deg,#123c32 0%,#17664f 58%,#41876d 100%);
        color:white; padding:30px 34px; border-radius:20px; margin-bottom:24px; }
    .hero h1 { font-size:30px; line-height:1.25; margin:0 0 9px 0; color:white; }
    .hero p { margin:0; color:#d5e9df; font-size:14px; }
    .eyebrow { text-transform:uppercase; letter-spacing:.14em; color:#a9d7bf;
        font-size:11px; font-weight:700; margin-bottom:10px; }
    .section-note { color:var(--muted); font-size:13px; margin-top:-10px; margin-bottom:18px; }
    div[data-testid="stButton"] button[kind="primary"] { background:#116b55; border-color:#116b55; }
    div[data-testid="stButton"] button { border-radius:9px; }
    </style>
    """,
    unsafe_allow_html=True,
)


def api_get(path, params=None):
    response = requests.get(f"{API}{path}", params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def show_request_error(error):
    response = getattr(error, "response", None)
    if response is not None:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        st.error(f"服务请求失败：{detail}")
    else:
        st.error(f"无法连接监测服务 {API}。请确认 FastAPI 已启动。")


def display_rate(value):
    return f"{value:.1f}%" if value is not None else "—"


def display_time(value):
    if not value:
        return "—"
    return value.replace("T", " ")[:16]


try:
    businesses = api_get("/api/businesses")
    providers = api_get("/api/providers")
except requests.RequestException as error:
    show_request_error(error)
    st.stop()

if not businesses:
    st.markdown(
        """
        <div class="hero">
          <div class="eyebrow">AI Search Visibility · GEO Intelligence</div>
          <h1>建立你的企业监测档案</h1>
          <p>使用真实企业资料和可核实的信息开始监测，不会自动创建虚构的演示品牌或搜索问题。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.form("create_business", border=True):
        st.markdown("#### 企业资料")
        name = st.text_input("企业名称 *")
        alias = st.text_input("品牌别名（可选）")
        region = st.text_input("所在地区 *", placeholder="例如：湖北恩施")
        category = st.text_input("行业类别", placeholder="例如：餐饮、住宿、景区")
        website = st.text_input("官方网站（可选）")
        description = st.text_area("企业简介", placeholder="请填写真实、准确的业务介绍。")
        selling_points = st.text_area(
            "希望被用户了解的服务/产品",
            placeholder="逐条填写真实服务、产品或特色，不要填写无法证实的排名和承诺。",
        )
        evidence = st.text_area(
            "可核实依据（可选）",
            placeholder="资质、获奖信息、公开数据或对应来源链接。",
        )
        create_submitted = st.form_submit_button(
            "创建企业档案",
            type="primary",
            width="stretch",
        )
    if create_submitted:
        if not name.strip() or not region.strip():
            st.error("企业名称和所在地区为必填项。")
        else:
            try:
                response = requests.post(
                    f"{API}/api/businesses",
                    json={
                        "name": name,
                        "alias": alias,
                        "region": region,
                        "category": category,
                        "website": website,
                        "description": description,
                        "selling_points": selling_points,
                        "evidence": evidence,
                    },
                    timeout=15,
                )
                response.raise_for_status()
                st.success("企业档案已创建。请添加真实用户搜索问题，再运行投喂前基线。")
                st.rerun()
            except requests.RequestException as error:
                show_request_error(error)
    st.stop()

configured_providers = [provider for provider in providers if provider["configured"]]

with st.sidebar:
    st.markdown("### ◈ GEO 智能监测")
    st.caption("AI 搜索可见性 · 企业数据空间")
    business_id = st.selectbox(
        "当前企业",
        [business["id"] for business in businesses],
        format_func=lambda business_id: next(
            business["name"] for business in businesses if business["id"] == business_id
        ),
    )
    current_business = next(
        business for business in businesses if business["id"] == business_id
    )
    business_name = escape(current_business["name"] or "未命名企业")
    business_region = escape(current_business.get("region") or "区域未设置")
    business_category = escape(current_business.get("category") or "行业未设置")
    st.caption(
        " · ".join(
            value for value in (
                current_business.get("region"),
                current_business.get("category"),
            ) if value
        )
    )
    st.divider()
    st.markdown("**模型连接**")
    for provider in providers:
        status = "已配置密钥" if provider["configured"] else "未配置密钥"
        st.caption(f"{'🟢' if provider['configured'] else '⚪'} {provider['label']} · {status}")
    st.divider()
    st.caption(f"API 服务 · `{API}`")
    workspace = st.radio(
    "工作台",
    ["问题工厂", "投喂中心", "检测报告", "效果总览"],
    label_visibility="collapsed",
    )


try:
    overview = api_get("/api/dashboard/overview", {"business_id": business_id})
    runs = api_get("/api/monitor-runs", {"business_id": business_id, "limit": 50})
    questions = api_get(
        "/api/questions",
        {"region": current_business.get("region")} if current_business.get("region") else None,
    )
except requests.RequestException as error:
    show_request_error(error)
    st.stop()

st.markdown(
    f"""
    <div class="hero">
      <div class="eyebrow">AI Search Visibility · GEO Intelligence</div>
      <h1>{business_name} 的 AI 搜索表现</h1>
      <p>{business_region} · {business_category}
      &nbsp;&nbsp;|&nbsp;&nbsp; 追踪多个大模型对真实用户问题的回答与品牌提及</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.expander("企业资料与事实依据", expanded=False):
    st.caption("请保持信息真实、可核实。监测只使用企业名称和品牌别名判断是否被提及。")
    with st.form("update_business"):
        profile_left, profile_right = st.columns(2)
        with profile_left:
            profile_name = st.text_input("企业名称", value=current_business.get("name") or "")
            profile_alias = st.text_input("品牌别名", value=current_business.get("alias") or "")
            profile_region = st.text_input("所在地区", value=current_business.get("region") or "")
            profile_category = st.text_input("行业类别", value=current_business.get("category") or "")
            profile_website = st.text_input("官方网站", value=current_business.get("website") or "")
        with profile_right:
            profile_description = st.text_area(
                "企业简介", value=current_business.get("description") or "", height=90
            )
            profile_selling_points = st.text_area(
                "服务/产品信息",
                value=current_business.get("selling_points") or "",
                height=90,
            )
            profile_evidence = st.text_area(
                "资质、公开数据及来源",
                value=current_business.get("evidence") or "",
                height=90,
            )
        profile_submitted = st.form_submit_button("保存企业资料", width="stretch")
    if profile_submitted:
        if not profile_name.strip():
            st.error("企业名称不能为空。")
        else:
            try:
                response = requests.put(
                    f"{API}/api/businesses/{business_id}",
                    json={
                        "name": profile_name,
                        "alias": profile_alias,
                        "region": profile_region,
                        "category": profile_category,
                        "website": profile_website,
                        "description": profile_description,
                        "selling_points": profile_selling_points,
                        "evidence": profile_evidence,
                    },
                    timeout=15,
                )
                response.raise_for_status()
                st.success("企业资料已保存。")
                st.rerun()
            except requests.RequestException as error:
                show_request_error(error)

if workspace == "投喂中心":
    st.subheader("平台图文素材")
    st.markdown(
        "填写企业关键词、活动或事件信息，选择平台和大模型生成适配文案；之后可编辑、上传图片并下载图文素材包。"
        "当前版本只准备素材，发布仍需人工完成，不会自动向平台投放。"
    )
    if not configured_providers:
        st.warning("尚未配置可用模型 API 密钥，暂时无法生成投喂文案。")
    else:
        with st.form("create_feeding_plan", border=True):
            plan_name = st.text_input(
                "素材计划名称",
                value=f"{current_business['name']} 内容准备",
            )
            business_keywords = st.text_input(
                "企业业务关键词",
                placeholder="例如：恩施民宿、亲子研学、土家文化体验",
            )
            activity_name = st.text_input(
                "活动名称（可选）",
                placeholder="例如：秋季山野徒步体验活动",
            )
            promotional_points = st.text_area(
                "宣传点（可选）",
                placeholder="填写希望重点介绍的真实服务、产品或亮点。",
                height=90,
            )
            st.markdown("**新鲜事件（可选；填写时请补齐时间、地点和事情）**")
            event_time = st.text_input(
                "事件时间",
                placeholder="例如：2026年10月18日",
            )
            event_location = st.text_input(
                "事件地点",
                placeholder="例如：湖北省恩施市某景区",
            )
            event_description = st.text_area(
                "发生的事情",
                placeholder="只填写已发生或已确认的事实。",
                height=80,
            )
            generator = st.selectbox(
                "文案生成模型",
                [provider["id"] for provider in configured_providers],
                format_func=lambda provider_id: PROVIDER_LABELS[provider_id],
            )
            selected_channels = st.multiselect(
                "目标投喂平台",
                list(FEEDING_CHANNEL_LABELS),
                default=["rednote", "zhihu", "official_site", "wechat"],
                format_func=lambda channel: FEEDING_CHANNEL_LABELS[channel],
            )
            create_plan = st.form_submit_button("生成平台文案", type="primary")
        if create_plan:
            if not plan_name.strip() or not selected_channels:
                st.error("请填写素材计划名称并至少选择一个平台。")
            elif not any(
                value.strip()
                for value in (
                    business_keywords,
                    activity_name,
                    promotional_points,
                    event_time,
                    event_location,
                    event_description,
                )
            ):
                st.error("请至少填写企业业务关键词、活动名称、宣传点或新鲜事件中的一项。")
            elif any((event_time, event_location, event_description)) and not all(
                (event_time.strip(), event_location.strip(), event_description.strip())
            ):
                st.error("填写新鲜事件时，请补全事件时间、地点和事情。")
            else:
                try:
                    response = requests.post(
                        f"{API}/api/feeding-plans",
                        json={
                            "business_id": business_id,
                            "name": plan_name,
                            "provider": generator,
                            "channels": selected_channels,
                            "business_keywords": business_keywords,
                            "activity_name": activity_name,
                            "promotional_points": promotional_points,
                            "event_time": event_time,
                            "event_location": event_location,
                            "event_description": event_description,
                        },
                        timeout=180,
                    )
                    response.raise_for_status()
                    st.success("平台文案已生成。请核对事实、编辑文案并添加图片。")
                    st.rerun()
                except requests.RequestException as error:
                    show_request_error(error)
    try:
        feeding_plans = api_get("/api/feeding-plans", {"business_id": business_id})
    except requests.RequestException as error:
        show_request_error(error)
        feeding_plans = []
    if feeding_plans:
        selected_plan_id = st.selectbox(
            "选择投放计划",
            [plan["id"] for plan in feeding_plans],
            format_func=lambda plan_id: next(
                f"{plan['name']} · {plan['status']}"
                for plan in feeding_plans if plan["id"] == plan_id
            ),
        )
        selected_plan = next(plan for plan in feeding_plans if plan["id"] == selected_plan_id)
        if selected_plan.get("campaign_input"):
            st.caption("本计划输入：" + " · ".join(
                value
                for value in selected_plan["campaign_input"].values()
                if value
            ))
        st.caption("每条素材可单独编辑、添加图片并打包下载。审核与发布记录是人工操作；当前未接入平台发布 API。")
        for asset in selected_plan["assets"]:
            with st.expander(f"{asset['channel_name']} · {asset['title']} · {asset['status']}"):
                if asset["status"] == "published":
                    st.markdown(f"**{asset['title']}**")
                    st.markdown(asset["content"])
                    st.caption("已发布的素材不可再编辑。")
                else:
                    with st.form(f"edit_asset_{asset['id']}"):
                        edited_title = st.text_input(
                            "标题",
                            value=asset["title"],
                            key=f"asset_title_{asset['id']}",
                        )
                        edited_content = st.text_area(
                            "文案正文（支持 Markdown）",
                            value=asset["content"],
                            height=260,
                            key=f"asset_content_{asset['id']}",
                        )
                        save_asset = st.form_submit_button("保存文案")
                    if save_asset:
                        try:
                            response = requests.put(
                                f"{API}/api/feeding-assets/{asset['id']}",
                                json={"title": edited_title, "content": edited_content},
                                timeout=15,
                            )
                            response.raise_for_status()
                            st.success("文案已保存，请重新审核后再登记发布。")
                            st.rerun()
                        except requests.RequestException as error:
                            show_request_error(error)
                    st.markdown(edited_content)

                for image in asset.get("images", []):
                    image_column, remove_column = st.columns([5, 1])
                    with image_column:
                        st.image(
                            f"{API}{image['url']}",
                            caption=image["alt_text"] or image["filename"],
                            width=360,
                        )
                    if asset["status"] != "published":
                        with remove_column:
                            if st.button(
                                "移除图片",
                                key=f"delete_image_{image['id']}",
                            ):
                                try:
                                    response = requests.delete(
                                        f"{API}/api/feeding-assets/{asset['id']}/images/{image['id']}",
                                        timeout=15,
                                    )
                                    response.raise_for_status()
                                    st.rerun()
                                except requests.RequestException as error:
                                    show_request_error(error)

                st.link_button(
                    "下载图文素材包（ZIP）",
                    f"{API}/api/feeding-assets/{asset['id']}/download",
                )
                if asset["status"] != "published":
                    with st.form(f"upload_image_{asset['id']}"):
                        uploaded_image = st.file_uploader(
                            "添加图片（JPG、PNG、WebP，单张不超过 10 MB）",
                            type=["jpg", "jpeg", "png", "webp"],
                            key=f"asset_image_{asset['id']}",
                        )
                        image_alt = st.text_input(
                            "图片说明（可选）",
                            key=f"asset_image_alt_{asset['id']}",
                        )
                        upload_submitted = st.form_submit_button("上传图片")
                    if upload_submitted:
                        if uploaded_image is None:
                            st.error("请先选择要上传的图片。")
                        else:
                            try:
                                response = requests.post(
                                    f"{API}/api/feeding-assets/{asset['id']}/images",
                                    files={
                                        "file": (
                                            uploaded_image.name,
                                            uploaded_image.getvalue(),
                                            uploaded_image.type,
                                        )
                                    },
                                    data={"alt_text": image_alt},
                                    timeout=30,
                                )
                                response.raise_for_status()
                                st.success("图片已添加到该平台素材。")
                                st.rerun()
                            except requests.RequestException as error:
                                show_request_error(error)
                if asset["status"] == "draft":
                    if st.button("审核通过该素材", key=f"review_asset_{asset['id']}"):
                        try:
                            response = requests.post(
                                f"{API}/api/feeding-assets/{asset['id']}/review",
                                timeout=15,
                            )
                            response.raise_for_status()
                            st.success("素材已标记为审核通过。")
                            st.rerun()
                        except requests.RequestException as error:
                            show_request_error(error)
                else:
                    st.caption("已完成内容审核；如已发布，请填写公开页面链接登记。")
                with st.form(f"publish_asset_{asset['id']}"):
                    published_url = st.text_input(
                        "发布后公开链接",
                        value=asset.get("published_url") or "",
                        placeholder="https://...",
                        disabled=asset["status"] == "draft",
                    )
                    published = st.form_submit_button(
                        "更新发布记录" if asset["status"] == "published" else "记录已发布",
                        disabled=asset["status"] == "draft",
                    )
                if published:
                    try:
                        response = requests.put(
                            f"{API}/api/feeding-assets/{asset['id']}/publish",
                            json={"published_url": published_url},
                            timeout=15,
                        )
                        response.raise_for_status()
                        st.success("发布链接和时间已记录。")
                        st.rerun()
                    except requests.RequestException as error:
                        show_request_error(error)
    else:
        st.info("还没有投放计划。创建计划后即可查看素材并记录各渠道发布情况。")
    st.stop()

if workspace == "问题工厂":
    st.subheader("自动化测试问题")
    st.markdown(
        "根据企业资料生成候选问题；人工编辑、删减并审核后形成固定版本。每次检测绑定该版本，保证前后样本一致。"
    )
    available_generators = [provider for provider in configured_providers]
    if not available_generators:
        st.warning("尚未配置可用模型 API 密钥，暂时无法生成问题。")
    else:
        with st.form("generate_question_set", border=True):
            generator = st.selectbox(
                "候选问题生成模型",
                [provider["id"] for provider in available_generators],
                format_func=lambda provider_id: PROVIDER_LABELS[provider_id],
            )
            desired_count = st.slider("候选问题数量", min_value=3, max_value=30, value=12)
            generate_submitted = st.form_submit_button("生成候选问题", type="primary")
        if generate_submitted:
            try:
                response = requests.post(
                    f"{API}/api/question-sets/generate",
                    json={
                        "business_id": business_id,
                        "provider": generator,
                        "count": desired_count,
                    },
                    timeout=90,
                )
                response.raise_for_status()
                st.success("候选问题已生成，请审核后激活。")
                st.rerun()
            except requests.RequestException as error:
                show_request_error(error)
    try:
        question_sets = api_get("/api/question-sets", {"business_id": business_id})
    except requests.RequestException as error:
        show_request_error(error)
        question_sets = []
    if question_sets:
        st.dataframe(
            pd.DataFrame([
                {
                    "版本": f"v{item['version']}",
                    "状态": item["status"],
                    "问题数": item["question_count"],
                    "生成时间": display_time(item["created_at"]),
                }
                for item in question_sets
            ]),
            hide_index=True,
            width="stretch",
        )
        draft_sets = [item for item in question_sets if item["status"] == "draft"]
        if draft_sets:
            draft_id = st.selectbox(
                "待审核版本",
                [item["id"] for item in draft_sets],
                format_func=lambda item_id: next(
                    f"v{item['version']} · {item['question_count']} 个候选"
                    for item in draft_sets if item["id"] == item_id
                ),
            )
            try:
                draft = api_get(f"/api/question-sets/{draft_id}")
            except requests.RequestException as error:
                show_request_error(error)
                draft = None
            if draft:
                initial_questions = "\n".join(
                    f"{item['text']}\t{item.get('category') or ''}"
                    for item in draft["questions"]
                )
                with st.form(f"review_question_set_{draft_id}", border=True):
                    reviewed_text = st.text_area(
                        "审核问题（每行一个；可用 Tab 分隔问题和分类）",
                        value=initial_questions,
                        height=280,
                    )
                    approve_submitted = st.form_submit_button(
                        "审核并启用此问题集", type="primary"
                    )
                if approve_submitted:
                    reviewed_questions = []
                    for line in reviewed_text.splitlines():
                        text, separator, category = line.partition("\t")
                        if text.strip():
                            reviewed_questions.append({
                                "text": text.strip(),
                                "category": category.strip() if separator else "",
                            })
                    try:
                        response = requests.post(
                            f"{API}/api/question-sets/{draft_id}/approve",
                            json={"questions": reviewed_questions},
                            timeout=20,
                        )
                        response.raise_for_status()
                        st.success("问题集已审核并启用。后续检测将固定使用此版本。")
                        st.rerun()
                    except requests.RequestException as error:
                        show_request_error(error)
        active_set = next((item for item in question_sets if item["status"] == "active"), None)
        if active_set:
            st.success(f"当前启用问题集：v{active_set['version']}，共 {active_set['question_count']} 个问题")
            with st.form("start_versioned_monitor", border=True):
                stage = st.radio(
                    "检测阶段",
                    ["baseline", "after"],
                    format_func=lambda value: "投喂前基线" if value == "baseline" else "投喂后复测",
                    horizontal=True,
                )
                monitor_providers = st.multiselect(
                    "检测模型",
                    [provider["id"] for provider in configured_providers],
                    default=[provider["id"] for provider in configured_providers[:1]],
                    format_func=lambda provider_id: PROVIDER_LABELS[provider_id],
                )
                start_submitted = st.form_submit_button("启动固定问题集检测", type="primary")
            if start_submitted:
                if not monitor_providers:
                    st.error("请至少选择一个已配置的模型。")
                else:
                    try:
                        response = requests.post(
                            f"{API}/api/monitor-runs",
                            json={
                                "business_id": business_id,
                                "stage": stage,
                                "providers": monitor_providers,
                                "question_set_id": active_set["id"],
                            },
                            timeout=20,
                        )
                        response.raise_for_status()
                        st.success("检测已启动，可在效果总览查看运行状态。")
                        st.rerun()
                    except requests.RequestException as error:
                        show_request_error(error)
    else:
        st.info("尚无问题集。选择一个已配置模型生成候选问题。")
    st.stop()

if workspace == "检测报告":
    st.subheader("企业效果报告")
    st.markdown(
        "选择企业对应的投喂前基线和投喂后检测。报告只允许比较同一模型、同一问题集的已完成批次，并保存生成时的数据快照。"
    )
    try:
        report_runs = api_get(
            "/api/monitor-runs",
            {"business_id": business_id, "limit": 100},
        )
        feeding_plans_for_report = api_get(
            "/api/feeding-plans",
            {"business_id": business_id},
        )
        reports = api_get("/api/reports", {"business_id": business_id})
    except requests.RequestException as error:
        show_request_error(error)
        report_runs, feeding_plans_for_report, reports = [], [], []
    baseline_runs = [
        run for run in report_runs
        if run["status"] == "done" and run["stage"] == "baseline"
    ]
    after_runs = [
        run for run in report_runs
        if run["status"] == "done" and run["stage"] == "after"
    ]
    comparable_pairs = [
        (baseline, after)
        for baseline in baseline_runs
        for after in after_runs
        if baseline["provider"] == after["provider"]
        and baseline.get("question_set_id") == after.get("question_set_id")
    ]
    published_plans = [
        plan for plan in feeding_plans_for_report
        if any(asset["status"] == "published" for asset in plan["assets"])
    ]
    if comparable_pairs and published_plans:
        with st.form("create_effect_report", border=True):
            report_title = st.text_input(
                "报告名称",
                value=f"{current_business['name']} GEO 效果检测报告",
            )
            pair_index = st.selectbox(
                "可比较的检测批次组合",
                list(range(len(comparable_pairs))),
                format_func=lambda index: (
                    f"{comparable_pairs[index][0]['provider_name']} · "
                    f"投喂前 #{comparable_pairs[index][0]['run_id']} "
                    f"({display_time(comparable_pairs[index][0]['time'])}) → "
                    f"投喂后 #{comparable_pairs[index][1]['run_id']} "
                    f"({display_time(comparable_pairs[index][1]['time'])})"
                ),
            )
            baseline_id = comparable_pairs[pair_index][0]["run_id"]
            after_id = comparable_pairs[pair_index][1]["run_id"]
            feeding_plan_id = st.selectbox(
                "选择已发布的投放计划",
                [plan["id"] for plan in published_plans],
                format_func=lambda plan_id: next(
                    plan["name"] for plan in feeding_plans_for_report if plan["id"] == plan_id
                ),
            )
            report_submitted = st.form_submit_button("生成并保存报告", type="primary")
        if report_submitted:
            try:
                response = requests.post(
                    f"{API}/api/reports",
                    json={
                        "business_id": business_id,
                        "baseline_run_id": baseline_id,
                        "after_run_id": after_id,
                        "feeding_plan_id": feeding_plan_id,
                        "title": report_title,
                    },
                    timeout=30,
                )
                response.raise_for_status()
                st.success("效果报告已生成并保存。")
                st.rerun()
            except requests.RequestException as error:
                show_request_error(error)
    elif not comparable_pairs:
        st.info(
            "需要使用同一个已审核问题集和同一个模型，分别完成投喂前基线与投喂后复测，"
            "才能生成可比较报告。"
        )
    else:
        st.info("请先在投喂中心审核素材、发布至少一个渠道，并记录公开页面链接。")
    if reports:
        report_id = st.selectbox(
            "已保存报告",
            [report["id"] for report in reports],
            format_func=lambda item_id: next(
                f"#{report['id']} · {report['title']} · {display_time(report['created_at'])}"
                for report in reports if report["id"] == item_id
            ),
        )
        try:
            report_detail = api_get(f"/api/reports/{report_id}")
            html_response = requests.get(
                f"{API}/api/reports/{report_id}/html",
                timeout=20,
            )
            html_response.raise_for_status()
            snapshot = report_detail["snapshot"]
            metric_cols = st.columns(3)
            metric_cols[0].metric("投喂前提及率", display_rate(snapshot["baseline"]["mention_rate"]))
            metric_cols[1].metric("投喂后提及率", display_rate(snapshot["after"]["mention_rate"]))
            metric_cols[2].metric("变化", f"{snapshot['change']:+.1f} 个百分点")
            st.download_button(
                "下载 HTML 检测报告",
                data=html_response.content,
                file_name=f"geo-report-{report_id}.html",
                mime="text/html",
            )
            st.caption("报告是生成时的固定快照；更新企业资料或后续检测不会改写已保存报告。")
        except requests.RequestException as error:
            show_request_error(error)
    else:
        st.caption("生成后的企业报告会保存在这里，可重复下载。")
    st.stop()

comparisons = overview["comparisons"]
post_rates = [
    item["after"]["mention_rate"]
    for item in comparisons
    if item["after"] and item["after"]["mention_rate"] is not None
]
changes = [item["change"] for item in comparisons if item["change"] is not None]
latest_times = [
    item["after"]["time"]
    for item in comparisons if item["after"] and item["after"]["time"]
]
mean_post_rate = sum(post_rates) / len(post_rates) if post_rates else None
mean_change = sum(changes) / len(changes) if changes else None
last_updated = max(latest_times) if latest_times else None

metric_columns = st.columns(4)
metric_columns[0].metric(
    "投喂后平均提及率",
    display_rate(mean_post_rate),
    help="已完成的模型投喂后监测结果的算术平均值。",
)
metric_columns[1].metric(
    "较投喂前变化",
    f"{mean_change:+.1f} 个百分点" if mean_change is not None else "待建立基线",
    help="只比较同一个模型的投喂前后提及率，不混合不同模型之间的差异。",
)
metric_columns[2].metric("已接入模型", f"{len(configured_providers)} / {len(providers)}")
metric_columns[3].metric(
    "监测状态",
    f"{overview['active_runs']} 个任务运行中" if overview["active_runs"] else "运行正常",
    f"最近完成 {display_time(last_updated)}" if last_updated else "等待首次监测",
)

left, right = st.columns([1.2, 1], gap="large")
with left:
    st.subheader("模型可见性对比")
    st.markdown(
        '<div class="section-note">同一模型内对比投喂前基线与投喂后结果；变化值以百分点计。</div>',
        unsafe_allow_html=True,
    )
    chart_rows = []
    for item in comparisons:
        for stage, label in (("baseline", "投喂前"), ("after", "投喂后")):
            run = item[stage]
            if run and run["mention_rate"] is not None:
                chart_rows.append({
                    "模型": item["provider_name"],
                    "阶段": label,
                    "提及率": run["mention_rate"],
                })
    if chart_rows:
        chart = pd.DataFrame(chart_rows).pivot(
            index="模型", columns="阶段", values="提及率"
        )
        st.bar_chart(chart, y_label="提及率 (%)", color=["#a9c9b7", "#167458"])
    else:
        st.info("还没有已完成的监测。先建立投喂前基线，投放内容后再执行投喂后监测。")

with right:
    st.subheader("可见性变化")
    st.markdown(
        '<div class="section-note">展示各模型投喂后相对自身基线的提及率变化。</div>',
        unsafe_allow_html=True,
    )
    delta_rows = [
        {
            "模型": item["provider_name"],
            "百分点": item["change"],
        }
        for item in comparisons if item["change"] is not None
    ]
    if delta_rows:
        delta_frame = pd.DataFrame(delta_rows).set_index("模型")
        st.bar_chart(delta_frame, color="#36a17b", x_label="模型", y_label="变化 (百分点)")
        for item in comparisons:
            if item["change"] is not None:
                direction = "提升" if item["change"] > 0 else "下降" if item["change"] < 0 else "持平"
                st.caption(f"{item['provider_name']}：{direction} {abs(item['change']):.1f} 个百分点")
    else:
        mismatched_sets = [
            item for item in comparisons
            if item["baseline"] and item["after"] and not item["same_question_set"]
        ]
        if mismatched_sets:
            st.info(
                "投喂前后问题集不一致，暂不计算变化值。"
                "请使用当前同一问题集重新建立基线和投喂后监测。"
            )
        else:
            st.info("完成同一模型、同一问题集的投喂前和投喂后监测后，这里会显示效果变化。")

st.divider()
st.subheader("逐题投喂前后效果")
paired = [
    item for item in comparisons
    if item["baseline"] and item["after"]
]
if paired:
    paired_provider = st.selectbox(
        "选择对比模型",
        [item["provider"] for item in paired],
        format_func=lambda provider_id: PROVIDER_LABELS[provider_id],
        key="question_comparison_provider",
    )
    selected_comparison = next(
        item for item in paired if item["provider"] == paired_provider
    )
    try:
        baseline_detail = api_get(
            f"/api/monitor-runs/{selected_comparison['baseline']['run_id']}"
        )
        after_detail = api_get(
            f"/api/monitor-runs/{selected_comparison['after']['run_id']}"
        )
    except requests.RequestException as error:
        show_request_error(error)
        baseline_detail = None
        after_detail = None
    if baseline_detail and after_detail:
        before_by_question = {
            result["question"]: result for result in baseline_detail["results"]
        }
        after_by_question = {
            result["question"]: result for result in after_detail["results"]
        }
        paired_questions = list(dict.fromkeys(
            list(before_by_question) + list(after_by_question)
        ))
        comparison_rows = []
        for question in paired_questions:
            before_result = before_by_question.get(question)
            after_result = after_by_question.get(question)
            before_mentioned = bool(before_result and before_result["mentioned"])
            after_mentioned = bool(after_result and after_result["mentioned"])
            if not before_result or not after_result:
                outcome = "问题集有变化"
            elif after_mentioned and not before_mentioned:
                outcome = "新增提及"
            elif before_mentioned and after_mentioned:
                outcome = "持续提及"
            elif before_mentioned and not after_mentioned:
                outcome = "本次未提及"
            else:
                outcome = "仍未提及"
            comparison_rows.append({
                "用户搜索问题": question,
                "投喂前": "已提及" if before_mentioned else "未提及",
                "投喂后": "已提及" if after_mentioned else "未提及",
                "变化": outcome,
            })
        st.dataframe(
            pd.DataFrame(comparison_rows),
            hide_index=True,
            width="stretch",
        )
        for question in paired_questions:
            before_result = before_by_question.get(question)
            after_result = after_by_question.get(question)
            with st.expander(f"查看问题回答：{question}"):
                before_column, after_column = st.columns(2)
                with before_column:
                    st.markdown("**投喂前回答**")
                    st.markdown(
                        before_result["raw_answer"] if before_result else "该问题不在投喂前批次中。"
                    )
                with after_column:
                    st.markdown("**投喂后回答**")
                    st.markdown(
                        after_result["raw_answer"] if after_result else "该问题不在投喂后批次中。"
                    )
else:
    st.info("同一个模型完成投喂前与投喂后监测后，即可查看逐题变化及模型回答对比。")

st.divider()
st.subheader("监测趋势")
trend_rows = []
for point in overview["trend"]:
    if point["mention_rate"] is not None:
        trend_rows.append({
            "时间": point["time"],
            "模型 · 阶段": (
                f"{point['provider_name']} · "
                f"{STAGE_LABELS.get(point['stage'], point['stage'])}"
            ),
            "提及率": point["mention_rate"],
        })
if trend_rows:
    trend_frame = pd.DataFrame(trend_rows)
    chart_data = trend_frame.pivot(
        index="时间", columns="模型 · 阶段", values="提及率"
    )
    st.line_chart(chart_data, y_label="提及率 (%)")
else:
    st.info("完成监测后即可查看企业在不同模型和阶段的可见性趋势。")

st.divider()
st.subheader("用户搜索问题库")
st.markdown(
    '<div class="section-note">维护真实用户可能提出的问题。监测批次会对问题库中的全部问题逐一查询。</div>',
    unsafe_allow_html=True,
)
with st.expander(f"查看或添加问题 · 当前 {len(questions)} 个", expanded=False):
    if questions:
        st.dataframe(
            pd.DataFrame([
                {
                    "问题": question["text"],
                    "类别": question["category"] or "—",
                    "地区": question["region"] or "—",
                }
                for question in questions
            ]),
            hide_index=True,
            width="stretch",
        )
    with st.form("add_questions", border=False):
        question_text = st.text_area(
            "添加问题（每行一个）",
            placeholder="例如：恩施适合亲子入住的民宿有哪些？\n恩施当地人推荐的特色餐厅？",
            height=100,
        )
        question_category = st.text_input("问题类别（可选）", placeholder="餐饮、住宿、亲子等")
        add_submitted = st.form_submit_button("添加到问题库")
    if add_submitted:
        try:
            response = requests.post(
                f"{API}/api/questions",
                json={
                    "questions": question_text.splitlines(),
                    "category": question_category,
                    "region": current_business.get("region") or "",
                },
                timeout=15,
            )
            response.raise_for_status()
            result = response.json()
            st.success(f"新增 {result['added']} 个问题，跳过重复项 {result['skipped']} 个。")
            st.rerun()
        except requests.RequestException as error:
            show_request_error(error)

st.divider()
st.subheader("运行投喂效果监测")
st.markdown(
    '<div class="section-note">对同一问题集逐一调用所选模型。联网搜索模型可反映检索结果；'
    '纯对话模型作为补充参照，不代表实时网页搜索表现。</div>',
    unsafe_allow_html=True,
)

if not configured_providers:
    st.warning("尚未配置模型密钥。请在 `backend/.env` 添加 KIMI_KEY 或 DEEPSEEK_KEY 后重启 API。")
elif not questions:
    st.warning("当前企业地区还没有监测问题。请先在“用户搜索问题库”中添加真实搜索词。")
else:
    with st.form("start_monitor", border=True):
        stage = st.radio(
            "监测阶段",
            ["baseline", "after"],
            format_func=lambda value: "投喂前基线" if value == "baseline" else "投喂后复测",
            horizontal=True,
        )
        selected = st.multiselect(
            "选择模型",
            [provider["id"] for provider in configured_providers],
            default=[provider["id"] for provider in configured_providers],
            format_func=lambda value: PROVIDER_LABELS[value],
        )
        submitted = st.form_submit_button(
            "开始投喂前监测" if stage == "baseline" else "开始投喂后复测",
            type="primary",
            width="stretch",
        )
    if submitted:
        if not selected:
            st.error("请至少选择一个模型。")
        elif not questions:
            st.error("请先在问题库中添加监测问题。")
        else:
            try:
                response = requests.post(
                    f"{API}/api/monitor-runs",
                    json={
                        "business_id": business_id,
                        "stage": stage,
                        "providers": selected,
                    },
                    timeout=15,
                )
                response.raise_for_status()
                queued = response.json().get("runs", [])
                st.success(
                    "监测已启动：" + "、".join(run["provider_name"] for run in queued)
                    + "。模型调用可能需要数分钟，完成后点击下方刷新。"
                )
                st.rerun()
            except requests.RequestException as error:
                show_request_error(error)

st.divider()
st.subheader("逐题回答与运行记录")
if st.button("刷新监测数据", icon="🔄"):
    st.rerun()

if runs:
    run_labels = {
        run["run_id"]: (
            f"{STAGE_LABELS.get(run['stage'], run['stage'])} · "
            f"{run['provider_name']} · {display_time(run['time'])} · "
            f"{run['status']}"
        )
        for run in runs
    }
    selected_run_id = st.selectbox(
        "选择监测批次",
        list(run_labels),
        format_func=lambda run_id: run_labels[run_id],
    )
    try:
        run_detail = api_get(f"/api/monitor-runs/{selected_run_id}")
    except requests.RequestException as error:
        show_request_error(error)
        run_detail = None

    if run_detail:
        if run_detail["status"] == "running":
            st.info("此批次仍在运行中。完成后刷新页面查看结果。")
        elif run_detail["status"] == "failed":
            st.error(f"监测失败：{run_detail.get('error') or '未知错误'}")
        else:
            st.caption(
                f"{run_detail['mentioned']} / {run_detail['total']} 个问题提及企业 · "
                f"提及率 {display_rate(run_detail['mention_rate'])}"
            )
            results = run_detail["results"]
            if not results:
                st.info("本批次尚无逐题回答记录。")
            else:
                for index, result in enumerate(results, start=1):
                    marker = "✅ 已提及" if result["mentioned"] else "未提及"
                    title = f"{marker}  ·  Q{index}  {result['question']}"
                    with st.expander(title):
                        if result["snippet"]:
                            st.markdown(f"**命中品牌：** `{result['snippet']}`")
                        st.markdown(result["raw_answer"] or "模型未返回回答。")
else:
    st.info("暂无运行记录。建议先运行一次投喂前基线监测。")

if runs:
    st.markdown("#### 最近运行")
    history = pd.DataFrame([
        {
            "阶段": STAGE_LABELS.get(run["stage"], run["stage"]),
            "模型": run["provider_name"],
            "状态": {"done": "已完成", "running": "运行中", "failed": "失败"}.get(
                run["status"], run["status"]
            ),
            "提及率": display_rate(run["mention_rate"]),
            "提及 / 问题": f"{run['mentioned']} / {run['total']}",
            "时间": display_time(run["time"]),
            "错误信息": run["error"] or "",
        }
        for run in runs
    ])
    st.dataframe(history, hide_index=True, width="stretch")

st.caption(
    "指标说明：提及率按模型回答中是否出现企业名称或别名计算。"
    "前后对比是观测结果，不构成投放带来因果提升的保证；模型输出具有波动性。"
)
