import pandas as pd
from io import BytesIO
from .config import COLUMN_MAPPING


def read_reviews(file) -> tuple[list[dict], pd.DataFrame]:
    """读取卖家精灵导出的评论 Excel，返回标准化评论列表和原始 DataFrame"""
    df = pd.read_excel(file)

    field_map = {}
    for actual_col in df.columns:
        for keyword, field_name in COLUMN_MAPPING.items():
            if keyword == actual_col:
                field_map[field_name] = actual_col
                break

    text_col = field_map.get("content_cn")
    if text_col is None or text_col not in df.columns:
        text_col = field_map.get("content")
    if text_col is None:
        raise ValueError("未找到评论内容列（内容(翻译) 或 内容），请确认文件是从卖家精灵导出的")

    title_col = field_map.get("title_cn")
    if title_col is None:
        title_col = field_map.get("title")

    rating_col = field_map.get("rating")
    variant_col = field_map.get("variant")
    date_col = field_map.get("review_date")

    reviews = []
    for idx, row in df.iterrows():
        text = str(row.get(text_col, "")).strip()
        if pd.isna(text) or text == "nan" or text == "" or text == "None":
            continue

        review = {
            "index": idx,
            "content": text,
            "title": str(row.get(title_col, "")).strip() if title_col and title_col in df.columns else "",
            "rating": int(row[rating_col]) if rating_col and rating_col in df.columns and pd.notna(row.get(rating_col)) else 0,
            "variant": str(row.get(variant_col, "")).strip() if variant_col and variant_col in df.columns else "",
            "review_date": str(row.get(date_col, "")).strip()[:10] if date_col and date_col in df.columns else "",
            "tags": [],            # [{"name": "xx", "evidence": "..."}, ...]
            "confidence": "",
            "reasoning": "",
            "is_uncertain": False,
            "manual_override": False,
        }
        reviews.append(review)

    if not reviews:
        raise ValueError("文件中没有检测到有效的评论数据")

    return reviews, df


def write_reviews(reviews: list[dict], original_df: pd.DataFrame,
                  tags: list[dict] = None) -> BytesIO:
    """将打标结果写回 Excel：每个标签两列（值列 + 证据列）"""
    df = original_df.copy()

    # 构建标签列表（如果未传入则从 reviews 中提取）
    if tags is None:
        tag_set = {}
        for r in reviews:
            for t in r.get("tags", []):
                if isinstance(t, dict):
                    name = t.get("name", "")
                    cat = t.get("category", "")
                    if name:
                        tag_set[name] = cat
        tags = [{"name": n, "category": c} for n, c in tag_set.items()]

    review_map = {r["index"]: r for r in reviews}

    # 为每个标签创建两列：值列 + 证据列
    tag_columns = {}
    for t in tags:
        tag_name = t["name"]
        cat = t.get("category", "")
        col_header = f"{cat}_{tag_name}" if cat else tag_name
        tag_columns[tag_name] = col_header

    # 初始化所有标签列
    for col_header in tag_columns.values():
        df[f"{col_header}"] = ""
        df[f"{col_header}_证据"] = ""

    # 辅助列
    confidences = []
    reasonings = []
    is_uncertains = []
    manual_overrides = []

    for idx in range(len(df)):
        r = review_map.get(idx)
        if r:
            # 填充标签列
            for tag_item in r.get("tags", []):
                if isinstance(tag_item, dict):
                    name = tag_item.get("name", "")
                    evidence = tag_item.get("evidence", "")
                else:
                    name = tag_item
                    evidence = ""
                if name and name in tag_columns:
                    col_header = tag_columns[name]
                    df.at[idx, col_header] = "1"
                    if evidence:
                        df.at[idx, f"{col_header}_证据"] = evidence

            confidences.append(r.get("confidence", ""))
            reasonings.append(r.get("reasoning", ""))
            is_uncertains.append("是" if r.get("is_uncertain") else "否")
            manual_overrides.append("是" if r.get("manual_override") else "否")
        else:
            confidences.append("")
            reasonings.append("")
            is_uncertains.append("")
            manual_overrides.append("")

    # 辅助列放在最后
    df["AI置信度"] = confidences
    df["AI判断理由"] = reasonings
    df["是否不确定"] = is_uncertains
    df["人工修改"] = manual_overrides

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return output
