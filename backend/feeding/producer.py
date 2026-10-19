import argparse
from html import escape
import json
import os
from urllib.parse import urlsplit

from app.database import SessionLocal
from app.models import Business


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT_DIR = os.path.join(BASE_DIR, "data", "content")


def produce_entry(business_id: int) -> dict:
    db = SessionLocal()
    try:
        business = db.get(Business, business_id)
        if business is None:
            raise ValueError(f"找不到企业 ID {business_id}")
        return {
            "id": business.id,
            "name": business.name,
            "alias": business.alias,
            "region": business.region,
            "category": business.category,
            "description": business.description,
            "selling_points": business.selling_points,
            "evidence": business.evidence,
            "website": business.website,
        }
    finally:
        db.close()


def factual_lines(entry: dict) -> list[str]:
    facts = []
    if entry["alias"] and entry["alias"] != entry["name"]:
        facts.append(f"品牌别名：{entry['alias']}")
    if entry["description"]:
        facts.append(entry["description"])
    if entry["selling_points"]:
        facts.extend(
            point.strip(" \t-*•")
            for point in entry["selling_points"].splitlines()
            if point.strip(" \t-*•")
        )
    if entry["evidence"]:
        facts.extend(
            item.strip()
            for item in entry["evidence"].splitlines()
            if item.strip()
        )
    return facts


def produce_zhihu(entry: dict, facts: list[str]) -> dict:
    region = entry["region"] or "本地"
    category = entry["category"] or "服务"
    body = [
        f"以下是 {region} 一家{category}企业的资料整理，发布前请核对信息并补充来源：",
        "",
        f"**{entry['name']}**",
    ]
    body.extend(f"- {fact}" for fact in facts)
    if entry["website"]:
        body.extend(["", f"官方信息：{entry['website']}"])
    return {
        "question": f"{region}{category}有哪些选择？",
        "answer": "\n".join(body),
    }


def produce_web(entry: dict, facts: list[str]) -> tuple[str, str, str]:
    region = entry["region"] or "本地"
    title = f"{region}{entry['category'] or '企业'}信息｜{entry['name']}"
    website = entry["website"]
    parsed_website = urlsplit(website) if website else None
    safe_website = (
        website
        if parsed_website
        and parsed_website.scheme in {"http", "https"}
        and parsed_website.netloc
        else None
    )
    paragraphs = [
        f"<p>{escape(fact)}</p>" for fact in facts
    ]
    if not paragraphs:
        paragraphs = ["<p>企业尚未补充公开介绍，请先完善真实资料。</p>"]
    html = (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{escape(title)}</title>\n"
        "</head>\n<body>\n"
        f"<article><h1>{escape(entry['name'])}</h1>\n"
        f"<p>{escape(region)} · {escape(entry['category'] or '')}</p>\n"
        f"{''.join(paragraphs)}\n"
        + (
            f'<p><a href="{escape(safe_website, quote=True)}">官方网站</a></p>\n'
            if safe_website
            else ""
        )
        + "</article>\n</body>\n</html>\n"
    )
    schema = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": entry["name"],
        "description": entry["description"] or "",
    }
    if safe_website:
        schema["url"] = safe_website
    if region:
        schema["address"] = {
            "@type": "PostalAddress",
            "addressLocality": region,
        }
    return title, html, json.dumps(schema, ensure_ascii=False, indent=2)


def produce_notes(entry: dict, facts: list[str]) -> str:
    heading = f"{entry['region'] or '本地'} · {entry['name']}"
    lines = [heading]
    if entry["category"]:
        lines.append(f"类别：{entry['category']}")
    lines.extend(f"- {fact}" for fact in facts)
    if entry["website"]:
        lines.append(f"官方网站：{entry['website']}")
    lines.append("发布前请核对资料准确性并补充可公开验证的来源。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="根据单家企业资料生成可人工审核的 GEO 内容素材。"
    )
    parser.add_argument("--business-id", type=int, required=True)
    args = parser.parse_args()

    entry = produce_entry(args.business_id)
    facts = factual_lines(entry)
    output_dir = os.path.join(CONTENT_DIR, f"business_{entry['id']}")
    os.makedirs(output_dir, exist_ok=True)
    _, web_html, schema = produce_web(entry, facts)
    zhihu = produce_zhihu(entry, facts)
    outputs = {
        "profile.json": json.dumps(entry, ensure_ascii=False, indent=2),
        "web.html": web_html,
        "web_schema.json": schema,
        "zhihu.txt": f"问题：{zhihu['question']}\n\n{zhihu['answer']}",
        "notes.txt": produce_notes(entry, facts),
    }
    for filename, content in outputs.items():
        with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as file:
            file.write(content)
    print(f"内容素材已生成到：{output_dir}")
    print("请人工核对所有事实，再决定是否发布；本程序不会自动发布或保证模型推荐。")


if __name__ == "__main__":
    main()
