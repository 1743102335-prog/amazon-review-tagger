import concurrent.futures
from collections import defaultdict
from .deepseek_client import DeepSeekClient
from .prompts import build_tag_generation_prompt, build_tagging_prompt, build_retagging_prompt
from .utils import safe_json_parse, truncate_text
from .config import SAMPLE_SIZE, BATCH_SIZE, CONCURRENCY, MAX_CONTENT_LENGTH, \
    DEEPSEEK_MODEL_TAG_GEN, DEEPSEEK_MODEL_TAGGING


# ==================== 标签生成 ====================

def generate_tags(reviews: list[dict], client: DeepSeekClient) -> list[dict]:
    """使用 V4 Pro + 深度思考，生成带完整判据的标签体系"""
    sampled = _stratified_sample(reviews, SAMPLE_SIZE)

    lines = []
    for r in sampled:
        text = truncate_text(r["content"], MAX_CONTENT_LENGTH)
        stars = "★" * r["rating"]
        lines.append(f"[{stars}] {text}")
    sampled_text = "\n\n".join(lines)

    system_prompt, user_prompt = build_tag_generation_prompt(sampled_text)

    # 先用 V4 Pro，失败则降级到 V4 Flash
    response = None
    last_error = None
    for model_try in [DEEPSEEK_MODEL_TAG_GEN, DEEPSEEK_MODEL_TAGGING]:
        try:
            response = client.chat(system_prompt, user_prompt, model=model_try)
            break
        except Exception as e:
            last_error = e
            continue

    if response is None:
        raise RuntimeError(f"所有模型生成标签均失败: {last_error}")

    data = safe_json_parse(response)
    raw_tags = data.get("tags", [])

    tags = []
    for i, t in enumerate(raw_tags):
        if not isinstance(t, dict):
            continue
        name = t.get("name", "").strip()
        if not name:
            continue
        tags.append({
            "id": t.get("id", f"tag_{i:03d}"),
            "category": _safe_str(t.get("category"), "未分类"),
            "name": name,
            "description": _safe_str(t.get("description")),
            "criteria": _safe_str(t.get("criteria")),
            "trigger_keywords": _safe_list(t.get("trigger_keywords")),
            "positive_examples": _safe_list(t.get("positive_examples")),
            "negative_examples": _safe_list(t.get("negative_examples")),
        })

    # 如果 0 个标签，返回原始响应供调试
    if not tags:
        tags.append({
            "id": "tag_error",
            "category": "错误",
            "name": "标签生成失败",
            "description": f"API返回了0个标签。原始响应前500字: {response[:500]}",
            "criteria": "",
            "trigger_keywords": [],
            "positive_examples": [],
            "negative_examples": [],
        })

    return tags


# ==================== 四轮维度打标 ====================

# 四轮顺序：场景+人群 → 优点+动机 → 痛点+缺点 → 建议
DIMENSION_ROUNDS = [
    ("使用场景 + 目标人群", ["使用场景", "目标人群"]),
    ("产品优点 + 购买动机", ["产品优点", "购买动机"]),
    ("用户痛点 + 产品缺点", ["用户痛点", "产品缺点"]),
    ("使用建议", ["使用建议"]),
]

def _group_tags_by_rounds(tags: list[dict]) -> list[tuple[str, list[dict]]]:
    """将标签按四轮维度分组，返回 [(轮次名, 标签列表), ...]"""
    from collections import defaultdict
    cat_map = defaultdict(list)
    for t in tags:
        cat_map[t.get("category", "未分类")].append(t)

    rounds = []
    for round_name, categories in DIMENSION_ROUNDS:
        round_tags = []
        for cat in categories:
            if cat in cat_map:
                round_tags.extend(cat_map[cat])
        if round_tags:  # 只添加有标签的轮次
            rounds.append((round_name, round_tags))
    return rounds


def _group_tags_by_category(tags: list[dict]) -> dict[str, list[dict]]:
    """将标签按 category 分组"""
    groups = defaultdict(list)
    for t in tags:
        groups[t.get("category", "未分类")].append(t)
    return dict(groups)


def _fuzzy_match_tag(ai_name: str, tag_dict: dict[str, dict]) -> str | None:
    """模糊匹配 AI 返回的标签名到标准标签体系"""
    ai = ai_name.strip()
    if not ai:
        return None
    if ai in tag_dict:
        return ai
    ai_lower = ai.lower()
    for name in tag_dict:
        if name.lower() == ai_lower:
            return name
    for name in tag_dict:
        if name in ai or ai in name:
            return name
    ai_chars = set(ai)
    best, best_overlap = None, 0
    for name in tag_dict:
        overlap = len(ai_chars & set(name))
        if overlap >= 2 and overlap > best_overlap:
            best, best_overlap = name, overlap
    if best:
        return best
    return None


def _parse_batch_results(batch: list[dict], results: list, tag_dict: dict) -> None:
    """将 AI 返回结果解析到评论对象上（累加模式，多次调用合并结果）"""
    result_map = {}
    for item in results:
        if isinstance(item, dict):
            idx = item.get("review_index")
            if idx is not None:
                result_map[idx] = item

    for r in batch:
        result = result_map.get(r["index"])
        if not result or not isinstance(result, dict):
            continue

        tags_raw = result.get("tags", [])
        confidence = result.get("confidence", "medium")
        reasoning = result.get("reasoning", "")

        # 合并标签（去重）
        existing_names = {t["name"] for t in r.get("tags", [])}
        if isinstance(tags_raw, list):
            for item in tags_raw:
                if isinstance(item, dict):
                    ai_name = item.get("name", "").strip()
                    evidence = item.get("evidence", "").strip()
                    matched = _fuzzy_match_tag(ai_name, tag_dict)
                    if matched and matched not in existing_names:
                        existing_names.add(matched)
                        r["tags"].append({"name": matched, "evidence": evidence})
                elif isinstance(item, str):
                    matched = _fuzzy_match_tag(item, tag_dict)
                    if matched and matched not in existing_names:
                        existing_names.add(matched)
                        r["tags"].append({"name": matched, "evidence": ""})

        # 置信度取最保守的（最低的）
        conf_order = {"low": 0, "medium": 1, "high": 2}
        current_conf = r.get("confidence", "high")
        if conf_order.get(confidence, 1) < conf_order.get(current_conf, 2):
            r["confidence"] = confidence

        if not r.get("reasoning"):
            r["reasoning"] = reasoning
        elif reasoning:
            r["reasoning"] += "; " + reasoning


def _process_one_dimension(batch, tags, client, model):
    """处理一个维度（一组同类别标签）的请求"""
    system_prompt, user_prompt = build_tagging_prompt(batch, tags)
    try:
        response = client.chat(system_prompt, user_prompt, model=model)
        data = safe_json_parse(response)
        return data.get("results", [])
    except Exception:
        return []


def _process_one_round(reviews: list[dict], tags: list[dict],
                       client: DeepSeekClient, model: str,
                       round_label: str, round_idx: int, total_rounds: int,
                       progress_callback=None, stop_check=None) -> None:
    """处理一轮打标：所有评论 x 该轮标签"""
    total = len(reviews)
    tag_dict = {t["name"]: t for t in tags}

    all_batches = []
    for batch_start in range(0, total, BATCH_SIZE):
        batch = reviews[batch_start:batch_start + BATCH_SIZE]
        all_batches.append((batch_start, batch))

    def process_one_batch(batch, _tags):
        system_prompt, user_prompt = build_tagging_prompt(batch, _tags)
        try:
            response = client.chat(system_prompt, user_prompt, model=model)
            data = safe_json_parse(response)
            return data.get("results", [])
        except Exception:
            return []

    batch_idx = 0
    while batch_idx < len(all_batches):
        if stop_check and stop_check():
            return

        round_batches = all_batches[batch_idx:batch_idx + CONCURRENCY]
        batch_idx += len(round_batches)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(round_batches)) as executor:
            futures = {}
            for bs, batch in round_batches:
                for r in batch:
                    r.setdefault("tags", [])
                future = executor.submit(process_one_batch, batch, tags)
                futures[future] = batch

            for future in concurrent.futures.as_completed(futures):
                batch = futures[future]
                try:
                    results = future.result()
                    _parse_batch_results(batch, results, tag_dict)
                except Exception:
                    pass

        if progress_callback:
            global_done = sum(1 for r in reviews if r.get("confidence") or r.get("is_uncertain"))
            progress_callback(min(global_done, total), total,
                            round_label, round_idx, total_rounds)

    # 本轮完成，未打标的暂时不动（等所有轮次结束后再标记）


# ==================== 公开接口 ====================

def tag_reviews_batch(reviews: list[dict], tags: list[dict],
                      client: DeepSeekClient,
                      progress_callback=None, stop_check=None) -> list[dict]:
    """四轮维度打标：每轮只用对应维度的标签，合并所有轮次结果"""
    rounds = _group_tags_by_rounds(tags)
    model = DEEPSEEK_MODEL_TAGGING

    for round_idx, (round_name, round_tags) in enumerate(rounds):
        if stop_check and stop_check():
            break
        _process_one_round(reviews, round_tags, client, model,
                          round_name, round_idx + 1, len(rounds),
                          progress_callback, stop_check)

    # 全部轮次结束后统一标记未打标
    for r in reviews:
        if not _get_tag_names(r):
            r["is_uncertain"] = True
            r["confidence"] = "low"

    if progress_callback:
        total = len(reviews)
        progress_callback(total, total, "完成", len(rounds), len(rounds))

    return reviews


def retag_uncertain(reviews: list[dict], tags: list[dict],
                    client: DeepSeekClient,
                    progress_callback=None, stop_check=None) -> int:
    """二阶段补标：对未打标评论按四轮重新处理"""
    untagged = [r for r in reviews if not _get_tag_names(r)]
    if not untagged:
        return 0

    rounds = _group_tags_by_rounds(tags)
    model = DEEPSEEK_MODEL_TAGGING

    for round_idx, (round_name, round_tags) in enumerate(rounds):
        if stop_check and stop_check():
            break
        _process_one_round(untagged, round_tags, client, model,
                          round_name, round_idx + 1, len(rounds),
                          progress_callback, stop_check)

    for r in untagged:
        if not _get_tag_names(r):
            r["is_uncertain"] = True
            r["confidence"] = "low"

    return sum(1 for r in untagged if not r.get("is_uncertain", True))


# ==================== 工具函数 ====================

def _safe_str(val, default=""):
    if isinstance(val, str):
        return val.strip()
    return default


def _safe_list(val):
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [x.strip() for x in val.split("\n") if x.strip()]
    return []


def _get_tag_names(review: dict) -> list[str]:
    tags = review.get("tags", [])
    if not tags:
        return []
    result = []
    for t in tags:
        if isinstance(t, dict):
            result.append(t.get("name", ""))
        elif isinstance(t, str):
            result.append(t)
    return [n for n in result if n]


def _stratified_sample(reviews: list[dict], max_count: int) -> list[dict]:
    from collections import defaultdict
    import random
    by_rating = defaultdict(list)
    for r in reviews:
        by_rating[r["rating"]].append(r)

    per_rating = max(1, max_count // max(len(by_rating), 1))
    sampled = []
    for rating in sorted(by_rating.keys()):
        group = by_rating[rating]
        random.shuffle(group)
        sampled.extend(group[:per_rating])

    if len(sampled) < max_count:
        remaining = [r for r in reviews if r not in sampled]
        random.shuffle(remaining)
        needed = max_count - len(sampled)
        sampled.extend(remaining[:needed])

    return sampled[:max_count]
