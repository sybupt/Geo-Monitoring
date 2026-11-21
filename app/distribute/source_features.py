"""信息源网站实采 HTML 样本管理与文章特征抽取。

存储位置：data/source_samples/
  - raw_html/<channel_id>/*.html   代表性信息源网页的原始 HTML 快照
  - profiles/<channel_id>.json     按渠道聚合的文章结构与文风特征画像
  - manifest.json                  样本来源台账（URL、渠道、抓取时间、字节数）

生成阶段（generator._llm_polish）读取对应渠道的特征画像，将真实信息源站点的
篇幅节奏、标题层级、列表/表格比例、数据密度与首段写法注入提示词，实现针对性生成。
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from ..config import DATA_DIR
from ..geo.analyzer import analyze

SAMPLES_DIR = DATA_DIR / "source_samples"
RAW_HTML_DIR = SAMPLES_DIR / "raw_html"
PROFILES_DIR = SAMPLES_DIR / "profiles"
MANIFEST_PATH = SAMPLES_DIR / "manifest.json"

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_FAQ_RE = re.compile(r"(常见问题|FAQ|Q[:：]|问[:：]|答[:：])", re.I)
_ANTIBOT_MARKERS = (
    "安全验证", "请完成验证", "captcha", "access denied", "just a moment",
    "verify you are human", "环境异常", "完成验证后即可继续访问",
)

_PROFILE_CACHE: dict[str, dict] = {}


def ensure_dirs() -> None:
    RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_manifest(entries: list[dict]) -> None:
    ensure_dirs()
    MANIFEST_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(s or "")).strip()


class _ArticleHTMLParser(HTMLParser):
    """零第三方依赖的轻量 HTML 结构特征解析器。"""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "aside"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.in_title = False
        self.cur_heading: str | None = None
        self.cur_li = False
        self.cur_p = False
        self.buf_title: list[str] = []
        self.buf_heading: list[str] = []
        self.buf_li: list[str] = []
        self.buf_p: list[str] = []
        self.buf_all: list[str] = []

        self.meta_desc = ""
        self.has_jsonld = False
        self.table_count = 0
        self.headings: list[str] = []
        self.list_items: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if t == "script" and "ld+json" in attr_map.get("type", "").lower():
            self.has_jsonld = True
        if t == "meta" and attr_map.get("name", "").lower() == "description":
            if attr_map.get("content"):
                self.meta_desc = _clean_text(attr_map["content"])
        if t in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth > 0:
            return
        if t == "title":
            self.in_title = True
        elif t in ("h1", "h2", "h3", "h4"):
            self.cur_heading = t
            self.buf_heading = []
        elif t == "li":
            self.cur_li = True
            self.buf_li = []
        elif t == "p":
            self.cur_p = True
            self.buf_p = []
        elif t == "table":
            self.table_count += 1

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth > 0:
            return
        if t == "title":
            self.in_title = False
        elif t == self.cur_heading:
            txt = _clean_text(" ".join(self.buf_heading))
            if 3 <= len(txt) <= 80 and txt not in self.headings:
                self.headings.append(txt)
            self.cur_heading = None
        elif t == "li" and self.cur_li:
            txt = _clean_text(" ".join(self.buf_li))
            if len(txt) >= 4:
                self.list_items.append(txt)
            self.cur_li = False
        elif t == "p" and self.cur_p:
            txt = _clean_text(" ".join(self.buf_p))
            if len(txt) >= 12:
                self.paragraphs.append(txt)
            self.cur_p = False
        elif t in ("div", "section", "br", "tr"):
            self.buf_all.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth > 0:
            return
        if self.in_title:
            self.buf_title.append(data)
            return
        if self.cur_heading:
            self.buf_heading.append(data)
        if self.cur_li:
            self.buf_li.append(data)
        if self.cur_p:
            self.buf_p.append(data)
        self.buf_all.append(data)


def extract_html_features(html_text: str, url: str = "", filename: str = "") -> dict:
    """从单篇真实 HTML 中抽取结构、篇幅、排版与 GEO 质量特征。"""
    p = _ArticleHTMLParser()
    p.feed(html_text)
    p.close()

    title = _clean_text(" ".join(p.buf_title))
    paragraphs = p.paragraphs
    if len(paragraphs) < 2:
        raw_lines = [
            _clean_text(ln)
            for ln in "".join(p.buf_all).splitlines()
            if len(_clean_text(ln)) >= 14
        ]
        paragraphs = raw_lines[:80]

    body_text = "\n".join(paragraphs)
    char_count = len(re.sub(r"\s", "", body_text))
    para_count = len(paragraphs)
    avg_para_len = round(char_count / max(1, para_count), 1)
    num_count = len(_NUM_RE.findall(body_text))
    nums_per_1k = round(num_count * 1000.0 / max(100, char_count), 1)
    has_faq = bool(_FAQ_RE.search(body_text))

    geo = analyze(body_text[:4000]) if char_count >= 50 else None
    lead = paragraphs[0][:120] if paragraphs else ""

    return {
        "file": filename,
        "url": url,
        "title": title[:80],
        "title_len": len(title),
        "has_meta_desc": bool(p.meta_desc),
        "char_count": char_count,
        "para_count": para_count,
        "avg_para_len": avg_para_len,
        "heading_count": len(p.headings),
        "list_item_count": len(p.list_items),
        "table_count": p.table_count,
        "has_jsonld": p.has_jsonld,
        "has_faq": has_faq,
        "numbers_per_1k": nums_per_1k,
        "gqs": geo.gqs if geo else 0.0,
        "geo_sub_scores": geo.sub_scores if geo else {},
        "sample_headings": p.headings[:6],
        "lead_snippet": lead,
    }


def build_channel_profile(channel_id: str) -> dict | None:
    """聚合指定渠道下所有本地 .html 样本，生成结构化特征画像并落盘。"""
    ensure_dirs()
    ch_dir = RAW_HTML_DIR / channel_id
    if not ch_dir.exists():
        return None
    html_files = sorted(ch_dir.glob("*.html"))
    if not html_files:
        return None

    manifest_by_file = {
        m.get("rel_path", ""): m
        for m in load_manifest()
        if m.get("channel_id") == channel_id
    }

    samples = []
    for fp in html_files:
        rel = f"raw_html/{channel_id}/{fp.name}"
        meta = manifest_by_file.get(rel, {})
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        feat = extract_html_features(text, url=meta.get("url", ""), filename=fp.name)
        if feat["char_count"] < 60:
            continue
        samples.append(feat)

    if not samples:
        return None

    n = len(samples)
    avg = lambda key: round(sum(s[key] for s in samples) / n, 1)
    ratio = lambda key: round(sum(1 for s in samples if s[key]) / n, 2)

    # 聚合 10 项 GEO 规则平均分
    rule_keys = list(samples[0]["geo_sub_scores"].keys())
    geo_avgs = {
        k: round(sum(s["geo_sub_scores"].get(k, 0.0) for s in samples) / n, 2)
        for k in rule_keys
    }

    sample_headings = []
    for s in samples:
        for h in s["sample_headings"]:
            if h not in sample_headings and len(sample_headings) < 8:
                sample_headings.append(h)

    sample_openings = [s["lead_snippet"] for s in samples if s["lead_snippet"]][:3]

    # 生成可供 LLM 直接遵循的实测特征指引
    guidance = [
        f"参考该渠道实采 {n} 篇代表性网页：平均正文约 {int(avg('char_count'))} 字，平均每段 {int(avg('avg_para_len'))} 字（共约 {int(avg('para_count'))} 段）",
        f"标题长度约 {int(avg('title_len'))} 字，正文平均包含 {avg('heading_count')} 个小标题、{avg('list_item_count')} 条列表项",
        f"量化信息密度约 {avg('numbers_per_1k')} 个数字/千字；表格出现率 {int(ratio('table_count') * 100)}%，结构化 JSON-LD 覆盖率 {int(ratio('has_jsonld') * 100)}%",
    ]
    if sample_headings:
        guidance.append("该站点常见小标题风格示例：" + " / ".join(f"「{h[:24]}」" for h in sample_headings[:4]))
    if sample_openings:
        guidance.append(f"该站点首段常见开篇节奏示例：「{sample_openings[0][:75]}…」")

    profile = {
        "channel_id": channel_id,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "sample_count": n,
        "stats": {
            "avg_chars": avg("char_count"),
            "avg_title_len": avg("title_len"),
            "avg_para_count": avg("para_count"),
            "avg_para_len": avg("avg_para_len"),
            "avg_headings": avg("heading_count"),
            "avg_list_items": avg("list_item_count"),
            "table_ratio": ratio("table_count"),
            "jsonld_ratio": ratio("has_jsonld"),
            "faq_ratio": ratio("has_faq"),
            "numbers_per_1k": avg("numbers_per_1k"),
            "avg_gqs": avg("gqs"),
        },
        "geo_avg_scores": geo_avgs,
        "sample_headings": sample_headings,
        "sample_openings": sample_openings,
        "prompt_guidance": guidance,
        "samples": [
            {
                "file": s["file"],
                "url": s["url"],
                "title": s["title"],
                "char_count": s["char_count"],
                "heading_count": s["heading_count"],
                "list_item_count": s["list_item_count"],
                "gqs": s["gqs"],
            }
            for s in samples
        ],
    }

    out_path = PROFILES_DIR / f"{channel_id}.json"
    out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    _PROFILE_CACHE[channel_id] = profile
    return profile


def build_all_profiles() -> dict[str, dict]:
    """扫描 raw_html/ 下全部渠道并重建特征画像。"""
    ensure_dirs()
    _PROFILE_CACHE.clear()
    out: dict[str, dict] = {}
    for ch_dir in sorted(RAW_HTML_DIR.iterdir()):
        if ch_dir.is_dir():
            p = build_channel_profile(ch_dir.name)
            if p:
                out[ch_dir.name] = p
    return out


def load_channel_profile(channel_id: str) -> dict | None:
    if channel_id in _PROFILE_CACHE:
        return _PROFILE_CACHE[channel_id]
    p = PROFILES_DIR / f"{channel_id}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        _PROFILE_CACHE[channel_id] = data
        return data
    except Exception:
        return None


def list_all_profiles() -> dict[str, dict]:
    ensure_dirs()
    out: dict[str, dict] = {}
    for fp in sorted(PROFILES_DIR.glob("*.json")):
        cid = fp.stem
        p = load_channel_profile(cid)
        if p:
            out[cid] = p
    return out


def format_profile_for_prompt(profile: dict | None) -> str:
    """将信息源实测特征画像格式化为可直接拼入 LLM Prompt 的精炼指令块。"""
    if not profile:
        return ""
    lines = profile.get("prompt_guidance") or []
    if not lines:
        return ""
    return (
        "【目标信息源网站实测范文特征（基于本地实采 .html 样本，请尽量贴合该站点的排版与表达节奏）】\n"
        + "\n".join(f"- {ln}" for ln in lines)
        + "\n\n"
    )


def fetch_url_to_channel(channel_id: str, url: str, timeout: int = 20) -> dict:
    """拉取公开可访问网页的 .html 存入 raw_html/<channel_id>/ 并刷新该渠道特征画像。"""
    ensure_dirs()
    ch_dir = RAW_HTML_DIR / channel_id
    ch_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        status = getattr(resp, "status", 200)
        final_url = resp.geturl()
    html = raw.decode("utf-8", errors="ignore")
    low = html[:4000].lower()
    if any(m in low for m in _ANTIBOT_MARKERS) and len(html) < 25000:
        raise ValueError("目标页面触发人机校验或访问受限，已停止抓取")

    digest = hashlib.sha256(final_url.encode("utf-8")).hexdigest()[:12]
    fname = f"{digest}.html"
    fp = ch_dir / fname
    fp.write_text(html, encoding="utf-8")

    manifest = load_manifest()
    rel = f"raw_html/{channel_id}/{fname}"
    manifest = [m for m in manifest if m.get("rel_path") != rel]
    manifest.append({
        "channel_id": channel_id,
        "url": final_url,
        "rel_path": rel,
        "bytes": len(raw),
        "http_status": status,
        "source": "live_fetch",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    })
    save_manifest(manifest)
    profile = build_channel_profile(channel_id)
    return {"file": rel, "bytes": len(raw), "profile": profile}
