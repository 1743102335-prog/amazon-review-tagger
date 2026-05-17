import streamlit as st
import pandas as pd
import time
import copy

from src.deepseek_client import DeepSeekClient
from src.excel_handler import read_reviews, write_reviews
from src.tag_engine import generate_tags, tag_reviews_batch
from src.config import BATCH_SIZE, CONCURRENCY


# ==================== 页面设置 ====================
st.set_page_config(
    page_title="亚马逊评论打标签工具",
    page_icon="🏷️",
    layout="wide",
)

# ==================== 初始化 session_state ====================
def init_session():
    defaults = {
        "step": 0,
        "api_key": "",
        "api_valid": False,
        "raw_df": None,
        "reviews": [],
        "tags": [],
        "tags_confirmed": False,
        "tagging_done": False,
        "tagging_started": False,
        "stop_tagging": False,
        "retag_state": None,
        "retag_round": 0,
        "retag_last_count": 0,
        "manual_tags": [],
        "deleted_tags": set(),
        "merge_actions": [],
        "uncertain_resolved": False,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


init_session()

# ==================== 辅助函数 ====================

def _get_tag_names(review: dict) -> list[str]:
    """从 review 中提取标签名列表（兼容新旧格式）"""
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

def _get_tag_evidence(review: dict, tag_name: str) -> str:
    """获取某个标签的证据文本"""
    for t in review.get("tags", []):
        if isinstance(t, dict) and t.get("name") == tag_name:
            return t.get("evidence", "")
    return ""


def get_client():
    """获取 DeepSeek 客户端"""
    key = st.session_state.get("api_key", "")
    if not key:
        return None
    return DeepSeekClient(key)


def check_api_key():
    """验证 API Key"""
    key = st.session_state.get("api_key", "")
    if not key or len(key) < 10:
        st.session_state["api_valid"] = False
        return
    try:
        client = DeepSeekClient(key)
        st.session_state["api_valid"] = client.validate_key()
    except Exception:
        st.session_state["api_valid"] = False


def go_to_step(s):
    st.session_state["step"] = s
    st.rerun()


def reset_all():
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    init_session()
    st.rerun()


# ==================== 侧边栏 ====================

def render_sidebar():
    with st.sidebar:
        st.title("🏷️ 亚马逊评论打标签")

        # API Key
        st.subheader("🔑 DeepSeek API Key")
        api_key = st.text_input(
            "输入 API Key",
            value=st.session_state.get("api_key", ""),
            type="password",
            placeholder="sk-...",
            key="sidebar_api_key",
        )
        if api_key != st.session_state.get("api_key", ""):
            st.session_state["api_key"] = api_key
            st.session_state["api_valid"] = False

        col1, col2 = st.columns(2)
        with col1:
            if st.button("验证 Key", use_container_width=True):
                with st.spinner("验证中..."):
                    check_api_key()
        with col2:
            status = "🟢 有效" if st.session_state.get("api_valid") else "⚪ 未验证"
            st.caption(status)

        st.divider()

        # 步骤指示器
        st.subheader("📋 流程步骤")
        step_names = [
            "0. 上传文件",
            "1. 生成标签",
            "2. 审阅标签",
            "3. 自动打标签",
            "4. 不确定项审阅",
            "5. 导出结果",
        ]
        current_step = st.session_state.get("step", 0)
        for i, name in enumerate(step_names):
            if i < current_step:
                st.success(f"✅ {name}")
            elif i == current_step:
                st.info(f"🔵 {name}")
            else:
                st.text(f"⚪ {name}")

        st.divider()

        # 统计信息
        reviews = st.session_state.get("reviews", [])
        tags = st.session_state.get("tags", [])
        if reviews:
            st.subheader("📊 统计")
            st.text(f"评论总数: {len(reviews)}")
            tagged = sum(1 for r in reviews if _get_tag_names(r))
            uncertain = sum(1 for r in reviews if r.get("is_uncertain"))
            st.text(f"已打标: {tagged}")
            st.text(f"不确定: {uncertain}")
            st.text(f"标签数: {len(tags)}")

        st.divider()

        # 重置按钮
        if st.button("🔄 重新开始", use_container_width=True):
            reset_all()


# ==================== Step 0: 上传文件 ====================

def render_step_0():
    st.title("📤 上传亚马逊评论数据")
    st.caption("支持从卖家精灵导出的评论 Excel 文件（.xlsx 格式）")

    uploaded = st.file_uploader(
        "选择 Excel 文件",
        type=["xlsx"],
        help="请上传从卖家精灵下载的评论数据文件",
    )

    if uploaded:
        try:
            reviews, df = read_reviews(uploaded)
            st.session_state["reviews"] = reviews
            st.session_state["raw_df"] = df

            # 预览
            st.success(f"成功读取 {len(reviews)} 条评论")

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("评论总数", len(reviews))
            with col2:
                rating_counts = {}
                for r in reviews:
                    s = r["rating"]
                    rating_counts[s] = rating_counts.get(s, 0) + 1
                avg_rating = sum(r["rating"] for r in reviews) / len(reviews) if reviews else 0
                st.metric("平均星级", f"{avg_rating:.1f}")
            with col3:
                st.metric("文件列数", len(df.columns))

            # 星级分布
            st.subheader("星级分布")
            rating_data = {}
            for r in reviews:
                s = r["rating"]
                rating_data[s] = rating_data.get(s, 0) + 1
            chart_data = pd.DataFrame({
                "星级": [f"{k}星" for k in sorted(rating_data.keys())],
                "数量": [rating_data[k] for k in sorted(rating_data.keys())],
            })
            st.bar_chart(chart_data.set_index("星级"))

            # 评论预览表格
            st.subheader("数据预览（前10条）")
            preview_data = []
            for r in reviews[:10]:
                preview_data.append({
                    "星级": r["rating"],
                    "型号": r.get("variant", ""),
                    "标题": r.get("title", "")[:40],
                    "内容": r["content"][:100] + ("..." if len(r["content"]) > 100 else ""),
                    "日期": r.get("review_date", ""),
                })
            st.dataframe(preview_data, use_container_width=True)

        except Exception as e:
            st.error(f"读取文件失败: {e}")
            return

    # 下一步按钮
    if st.session_state.get("reviews"):
        api_valid = st.session_state.get("api_valid", False)
        if not api_valid:
            st.warning("请先在侧边栏输入并验证 DeepSeek API Key")
        if st.button("下一步 → 生成标签", type="primary", disabled=not api_valid):
            go_to_step(1)


# ==================== Step 1: 生成标签 ====================

def render_step_1():
    st.title("🤖 AI 自动生成标签体系")
    st.caption("DeepSeek 将分析评论内容，自动生成一套标签体系供你审阅")

    reviews = st.session_state.get("reviews", [])
    if not reviews:
        st.error("没有评论数据，请先返回上传文件")
        if st.button("← 返回上传"):
            go_to_step(0)
        return

    st.info(f"📊 AI 将从 {min(100, len(reviews))} 条分层采样评论中分析生成标签")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← 返回上一步"):
            go_to_step(0)
    with col2:
        start_btn = st.button("🚀 开始生成标签", type="primary", use_container_width=True)

    if start_btn:
        with st.spinner("AI 正在分析评论内容，生成标签体系...（约需 30-60 秒）"):
            try:
                client = get_client()
                if not client:
                    st.error("请先在侧边栏输入 API Key")
                    return
                tags = generate_tags(reviews, client)
                st.session_state["tags"] = tags
            except Exception as e:
                st.error(f"生成标签失败: {e}")
                return

        st.success(f"✅ 成功生成 {len(st.session_state['tags'])} 个标签")

    # 展示已生成的标签
    tags = st.session_state.get("tags", [])
    if tags:
        st.subheader("生成的标签体系")
        # 按类别分组
        from collections import defaultdict
        by_category = defaultdict(list)
        for t in tags:
            by_category[t["category"]].append(t)

        for cat, cat_tags in by_category.items():
            st.markdown(f"### {cat}（{len(cat_tags)}个标签）")
            for t in cat_tags:
                with st.expander(f"**{t['name']}** — {t.get('description', '')[:60]}", expanded=False):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.caption("判定规则")
                        st.text(t.get("criteria", "无"))
                        st.caption("触发关键词")
                        st.text("、".join(t.get("trigger_keywords", [])) or "无")
                    with c2:
                        st.caption("正例")
                        for ex in t.get("positive_examples", []):
                            st.text(f"✅ {ex}")
                        st.caption("反例")
                        for ex in t.get("negative_examples", []):
                            st.text(f"❌ {ex}")

        if st.button("下一步 → 审阅编辑标签", type="primary"):
            go_to_step(2)


# ==================== Step 2: 审阅编辑标签 ====================

def render_step_2():
    st.title("✏️ 审阅与编辑标签")
    st.caption("你可以修改、删除或新增标签，确认后进入自动打标签阶段")

    tags = st.session_state.get("tags", [])
    if not tags:
        st.error("没有标签数据，请先生成标签")
        if st.button("← 返回生成标签"):
            go_to_step(1)
        return

    # 转为可编辑表格
    edit_data = []
    for t in tags:
        edit_data.append({
            "id": t["id"],
            "类别": t["category"],
            "标签名": t["name"],
            "描述": t.get("description", ""),
            "判定规则": t.get("criteria", ""),
            "触发词": "、".join(t.get("trigger_keywords", [])),
        })

    edited_df = st.data_editor(
        pd.DataFrame(edit_data),
        num_rows="dynamic",
        key="tag_editor",
        use_container_width=True,
        column_config={
            "id": st.column_config.TextColumn("ID", disabled=True),
            "类别": st.column_config.TextColumn("类别", help="如：使用场景、用户痛点等"),
            "标签名": st.column_config.TextColumn("标签名", help="2-8字"),
            "描述": st.column_config.TextColumn("描述", help="一句话说明"),
            "判定规则": st.column_config.TextColumn("判定规则", help="明确判定标准"),
            "触发词": st.column_config.TextColumn("触发词", help="触发关键词"),
        },
    )

    # 手动添加新标签（含完整判据）
    with st.expander("➕ 手动添加新标签（含完整判据）", expanded=False):
        col_a, col_b = st.columns(2)
        with col_a:
            new_cat = st.text_input("类别", placeholder="使用场景/用户痛点/产品优点/产品缺点/目标人群/购买动机/使用建议", key="new_cat")
            new_name = st.text_input("标签名", placeholder="2-8字", key="new_name")
        with col_b:
            new_desc = st.text_area("描述", placeholder="一句话说明标签含义", key="new_desc", height=68)

        new_criteria = st.text_area("判定规则", placeholder="明确描述什么情况下该打这个标签，什么情况不该打", key="new_criteria")
        new_keywords = st.text_input("触发关键词（用顿号分隔）", placeholder="关键词1、关键词2、关键词3", key="new_keywords")

        col_c, col_d = st.columns(2)
        with col_c:
            new_pos = st.text_area("正例（每行一个）", placeholder="露营时用它拉装备太方便了\n带去野营了一次很实用", key="new_pos", height=80)
        with col_d:
            new_neg = st.text_area("反例（每行一个）", placeholder="去超市买菜用着不错\n在家里收纳很方便", key="new_neg", height=80)

        if st.button("✅ 添加此标签", use_container_width=True) and new_name:
            st.session_state["manual_tags"] = st.session_state.get("manual_tags", [])
            st.session_state["manual_tags"].append({
                "id": f"tag_manual_{len(st.session_state['manual_tags']):03d}",
                "category": new_cat or "未分类",
                "name": new_name,
                "description": new_desc or "",
                "criteria": new_criteria or "",
                "trigger_keywords": [k.strip() for k in new_keywords.split("、") if k.strip()],
                "positive_examples": [e.strip() for e in new_pos.split("\n") if e.strip()],
                "negative_examples": [e.strip() for e in new_neg.split("\n") if e.strip()],
            })
            st.success(f"标签「{new_name}」已添加")
            st.rerun()

    # 合并手动标签到编辑数据
    for mt in st.session_state.get("manual_tags", []):
        edit_data.append({
            "id": mt["id"],
            "类别": mt["category"],
            "标签名": mt["name"],
            "描述": mt.get("description", ""),
            "判定规则": mt.get("criteria", ""),
            "触发词": "、".join(mt.get("trigger_keywords", [])),
        })

    # -------- 删除/合并标签 --------
    with st.expander("🗑️ 删除或合并标签", expanded=False):
        all_tag_names = [row["标签名"] for _, row in edited_df.iterrows()]

        # 批量删除标签
        st.markdown("**批量删除标签**")
        tags_to_delete = st.multiselect("选择要删除的标签（可多选）", all_tag_names, key="del_tags")
        if st.button("🗑️ 删除选中标签", key="del_btn") and tags_to_delete:
            st.session_state["deleted_tags"] = st.session_state.get("deleted_tags", set())
            for t in tags_to_delete:
                st.session_state["deleted_tags"].add(t)
            st.success(f"已删除 {len(tags_to_delete)} 个标签：{'、'.join(tags_to_delete)}")
            st.rerun()

        st.divider()

        # 批量合并标签
        st.markdown("**批量合并标签**")
        available = [n for n in all_tag_names if n not in st.session_state.get("deleted_tags", set())]
        tags_to_merge = st.multiselect("选择要合并的标签（可多选，≥2个）", available, key="merge_tags")
        new_merged_name = st.text_input("合并后的新标签名", placeholder="输入合并后的新标签名", key="merged_name")

        if st.button("🔀 合并选中标签", key="merge_btn") and len(tags_to_merge) >= 2 and new_merged_name:
            st.session_state["merge_actions"] = st.session_state.get("merge_actions", [])
            st.session_state["merge_actions"].append((tags_to_merge, new_merged_name))
            st.session_state["deleted_tags"] = st.session_state.get("deleted_tags", set())
            for t in tags_to_merge:
                st.session_state["deleted_tags"].add(t)

            # 聚合所有被合并标签的信息
            merged_criteria = []
            merged_keywords = set()
            merged_pos = []
            merged_neg = []
            merged_cat = "未分类"
            for tname in tags_to_merge:
                old = next((t for t in tags if t["name"] == tname), {})
                if old.get("category"):
                    merged_cat = old["category"]
                if old.get("criteria"):
                    merged_criteria.append(old["criteria"])
                merged_keywords.update(old.get("trigger_keywords", []))
                merged_pos.extend(old.get("positive_examples", []))
                merged_neg.extend(old.get("negative_examples", []))

            st.session_state["manual_tags"] = st.session_state.get("manual_tags", [])
            st.session_state["manual_tags"].append({
                "id": f"tag_merged_{len(st.session_state['merge_actions']):03d}",
                "category": merged_cat,
                "name": new_merged_name,
                "description": f"由 {'、'.join(tags_to_merge)} 合并",
                "criteria": "；".join(merged_criteria),
                "trigger_keywords": list(merged_keywords),
                "positive_examples": merged_pos,
                "negative_examples": merged_neg,
            })
            st.success(f"{len(tags_to_merge)} 个标签已合并为「{new_merged_name}」")
            st.rerun()

    # 过滤被删除的标签
    deleted = st.session_state.get("deleted_tags", set())
    if deleted:
        edit_data = [row for row in edit_data if row["标签名"] not in deleted]

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← 返回上一步"):
            st.session_state["deleted_tags"] = set()
            st.session_state["merge_actions"] = []
            go_to_step(1)
    with col2:
        if st.button("✅ 确认标签，开始打标签", type="primary", use_container_width=True):
            # 保存编辑后的标签（含手动标签）
            new_tags = []
            seen_names = set()
            for _, row in edited_df.iterrows():
                tag_id = str(row["id"])
                name = str(row["标签名"]).strip()
                if name in seen_names:
                    continue
                seen_names.add(name)
                old = next((t for t in tags if t["id"] == tag_id), {})
                new_tags.append({
                    "id": tag_id,
                    "category": str(row["类别"]).strip(),
                    "name": name,
                    "description": str(row["描述"]).strip(),
                    "criteria": str(row.get("判定规则", "")).strip(),
                    "trigger_keywords": [k.strip() for k in str(row.get("触发词", "")).split("、") if k.strip()],
                    "positive_examples": old.get("positive_examples", []),
                    "negative_examples": old.get("negative_examples", []),
                })
            st.session_state["tags"] = new_tags
            st.session_state["tags_confirmed"] = True
            st.success(f"标签已确认，共 {len(new_tags)} 个标签")
            go_to_step(3)


# ==================== Step 3: 自动打标签 ====================

def _estimate_time(review_count: int) -> str:
    """估算处理时间。按维度分组，每批需处理多组标签"""
    batches = max(1, (review_count + BATCH_SIZE - 1) // BATCH_SIZE)
    tags = st.session_state.get("tags", [])
    num_categories = len(set(t.get("category", "") for t in tags)) if tags else 5
    seconds_per_batch = max(3, num_categories * 3 // CONCURRENCY)
    seconds_low = batches * seconds_per_batch // 2
    seconds_high = batches * seconds_per_batch
    if seconds_low < 60:
        return f"{seconds_low}~{seconds_high} 秒"
    else:
        return f"{seconds_low // 60}~{seconds_high // 60} 分钟"


def render_step_3():
    st.title("🏷️ AI 自动打标签")
    st.caption(f"V4 Pro 精准打标，每批{BATCH_SIZE}条，{CONCURRENCY}路并发，质量优先")

    reviews = st.session_state.get("reviews", [])
    tags = st.session_state.get("tags", [])

    def should_stop():
        return st.session_state.get("stop_tagging", False)

    if not reviews or not tags:
        st.error("缺少评论或标签数据")
        if st.button("← 返回"):
            go_to_step(0)
        return

    total_reviews = len(reviews)
    total_batches = max(1, (total_reviews + BATCH_SIZE - 1) // BATCH_SIZE)

    # -------- 未开始：预估 + 开始按钮 --------
    if not st.session_state.get("tagging_started") and not st.session_state.get("tagging_done"):
        st.info(
            f"📊 待处理 **{total_reviews}** 条评论 | "
            f"标签数 **{len(tags)}** 个 | "
            f"批次 **{total_batches}** 批\n\n"
            f"⚡ {CONCURRENCY}路并发加速 | 模型 **deepseek-chat(V3)**\n\n"
            f"⏱️ 预计耗时：**{_estimate_time(total_reviews)}**"
        )

        col1, col2 = st.columns(2)
        with col1:
            if st.button("← 返回审阅标签"):
                go_to_step(2)
        with col2:
            if st.button("🚀 开始自动打标签", type="primary", use_container_width=True):
                st.session_state["tagging_started"] = True
                st.session_state["stop_tagging"] = False
                st.rerun()
        return

    # -------- 正在处理 --------
    if st.session_state.get("tagging_started") and not st.session_state.get("tagging_done"):
        client = get_client()
        if not client:
            st.error("请先在侧边栏输入 API Key")
            st.session_state["tagging_started"] = False
            return

        progress_bar = st.progress(0, "准备中...")
        status_text = st.empty()
        stop_col, _ = st.columns([1, 3])
        with stop_col:
            if st.button("⏹️ 停止打标签", type="secondary"):
                st.session_state["stop_tagging"] = True
                st.rerun()

        def update_progress(done, _total, round_label="", round_idx=0, total_rounds=0):
            pct = min(done / max(total_reviews, 1), 1.0)
            round_info = f"第{round_idx}/{total_rounds}轮「{round_label}」" if round_label else ""
            progress_bar.progress(pct, f"{round_info} 已处理: {min(done, total_reviews)}/{total_reviews}")
            tagged_count = sum(1 for r in reviews if _get_tag_names(r))
            uncertain_count = sum(1 for r in reviews if not _get_tag_names(r) and r.get("is_uncertain"))
            status_text.text(
                f"{round_info} | "
                f"进度: {min(done, total_reviews)}/{total_reviews} | "
                f"已打标: {tagged_count} | "
                f"未打标: {uncertain_count}"
            )

        try:
            from src.tag_engine import tag_reviews_batch as do_batch
            do_batch(reviews, tags, client,
                     progress_callback=update_progress,
                     stop_check=should_stop)
        except Exception as e:
            st.error(f"打标签过程出错: {e}")
            st.session_state["tagging_started"] = False
            return

        progress_bar.progress(1.0, "完成！")
        status_text.empty()
        st.session_state["tagging_done"] = True
        st.rerun()

    # -------- 完成/停止 --------
    done_count = sum(1 for r in reviews if r.get("confidence") or r.get("is_uncertain"))
    tagged = sum(1 for r in reviews if _get_tag_names(r))
    uncertain = sum(1 for r in reviews if r.get("is_uncertain"))
    high_conf = sum(1 for r in reviews if r.get("confidence") == "high")
    medium_conf = sum(1 for r in reviews if r.get("confidence") == "medium")
    low_conf = sum(1 for r in reviews if r.get("confidence") == "low")

    if st.session_state.get("stop_tagging"):
        st.warning(f"⏹️ 已停止。已处理约 {done_count}/{total_reviews} 条")

    st.success("✅ 打标签完成！")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("已打标", f"{tagged} ({tagged*100//max(total_reviews,1)}%)")
    col2.metric("高置信度", high_conf)
    col3.metric("中置信度", medium_conf)
    col4.metric("不确定", f"{uncertain} ({uncertain*100//max(total_reviews,1)}%)")

    # 预览
    st.subheader("打标结果预览")
    tagged_reviews = [r for r in reviews if _get_tag_names(r)]
    if tagged_reviews:
        preview = []
        for r in tagged_reviews[:20]:
            preview.append({
                "星级": r["rating"],
                "评论": r["content"][:80] + "...",
                "标签": ", ".join(_get_tag_names(r)),
                "置信度": r.get("confidence", ""),
            })
        st.dataframe(preview, use_container_width=True)

    # -------- 补标（可无限次执行，直到无未打标评论或用户满意） --------
    retag_state = st.session_state.get("retag_state", None)
    retag_round = st.session_state.get("retag_round", 0)

    if uncertain > 0 and done_count >= total_reviews and not st.session_state.get("stop_tagging"):
        st.divider()
        st.subheader("🔄 未打标评论补标")
        if retag_round > 0:
            st.info(
                f"第 **{retag_round}** 轮补标后仍有 **{uncertain}** 条未打标。"
                "每次补标会用更激进的标准，你可以无限次补标直到满意。"
            )
        else:
            st.info(
                f"还有 **{uncertain}** 条未打标。点击补标用更激进的标准重新分析。"
                "可以无限次重复，直到全部打标或你满意为止。"
            )

        if retag_state != "running":
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button(f"🔄 第{retag_round + 1}次补标（{uncertain}条）", type="primary", key=f"retag_btn_{retag_round}"):
                    st.session_state["retag_state"] = "running"
                    st.session_state["retag_round"] = retag_round + 1
                    st.rerun()
            with col_b:
                if retag_round > 0:
                    st.caption(f"已完成 {retag_round} 轮补标，继续点击可再次补标")

        if retag_state == "running":
            progress_bar = st.progress(0, "补标中...")
            status_text = st.empty()

            def retag_progress(done, _total, round_label="", round_idx=0, total_rounds=0):
                pct = min(done / max(uncertain, 1), 1.0)
                round_info = f"第{round_idx}/{total_rounds}轮「{round_label}」" if round_label else ""
                progress_bar.progress(pct, f"补标 {round_info}: {min(done, uncertain)}/{uncertain}")
                new_tagged = sum(1 for r in reviews if _get_tag_names(r))
                status_text.text(f"补标中... 总共已打标: {new_tagged}/{total_reviews} ({new_tagged*100//max(total_reviews,1)}%)")

            client = get_client()
            from src.tag_engine import retag_uncertain
            new_count = retag_uncertain(reviews, tags, client,
                                        progress_callback=retag_progress,
                                        stop_check=should_stop)

            st.session_state["retag_state"] = None  # 重置，允许再次补标
            st.session_state["retag_last_count"] = new_count
            st.rerun()

        # 显示上轮结果 + 预览
        last_count = st.session_state.get("retag_last_count", 0)
        if last_count > 0 and retag_round > 0:
            st.success(f"第{retag_round}轮补标新增 **{last_count}** 条 | "
                      f"累计已打标: **{tagged}**/{total_reviews} ({tagged*100//max(total_reviews,1)}%)")

            # 展示本轮新打标的评论预览
            st.subheader(f"📋 第{retag_round}轮补标结果预览")
            # 找本轮新打标的（有标签但之前是uncertain的）
            tagged_reviews = [r for r in reviews if _get_tag_names(r)]
            if tagged_reviews:
                preview = []
                for r in tagged_reviews[-20:]:  # 最后20条新打标的
                    tag_names = _get_tag_names(r)
                    preview.append({
                        "星级": r["rating"],
                        "评论": r["content"][:60] + "..." if len(r["content"]) > 60 else r["content"],
                        "标签": ", ".join(tag_names),
                        "置信度": r.get("confidence", ""),
                    })
                st.dataframe(preview, use_container_width=True)

    # -------- 操作按钮 --------
    st.divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("← 返回审阅标签"):
            st.session_state["tagging_started"] = False
            st.session_state["tagging_done"] = False
            st.session_state["stop_tagging"] = False
            st.session_state["retag_state"] = None
            st.session_state["retag_round"] = 0
            st.session_state["manual_tags"] = []
            for r in reviews:
                r["tags"] = []
                r["confidence"] = ""
                r["is_uncertain"] = False
            go_to_step(2)
    with col2:
        if done_count < total_reviews:
            if st.button("📥 导出当前结果"):
                go_to_step(5)
            st.caption(f"将导出已处理的约 {done_count} 条")
    with col3:
        if st.button("下一步 → 审阅不确定项", type="primary"):
            st.session_state["retag_state"] = None
            st.session_state["retag_round"] = 0
            go_to_step(4)


# ==================== Step 4: 不确定项审阅 ====================

def render_step_4():
    st.title("❓ 不确定项审阅")
    st.caption("以下评论 AI 无法确认标签，请手动处理")

    reviews = st.session_state.get("reviews", [])
    tags = st.session_state.get("tags", [])
    tag_names = [t["name"] for t in tags]

    uncertain_reviews = [r for r in reviews if r.get("is_uncertain")]

    if not uncertain_reviews:
        st.success("🎉 没有不确定项，所有评论都已打标签！")
        if st.button("下一步 → 导出结果", type="primary"):
            go_to_step(5)
        return

    st.info(f"共 {len(uncertain_reviews)} 条不确定评论需要处理")

    # 逐条展示不确定评论
    for i, r in enumerate(uncertain_reviews):
        with st.expander(
            f"#{i+1} [{r['rating']}★] {r['content'][:60]}...",
            expanded=(i < 5),
        ):
            # 显示评论内容
            st.text_area(
                "评论内容",
                value=r["content"],
                height=100,
                disabled=True,
                key=f"content_{r['index']}",
            )
            st.caption(f"AI 判断理由: {r.get('reasoning', '无')} | 置信度: {r.get('confidence', '')}")

            # 手动选标签
            current_tag_names = _get_tag_names(r)
            selected = st.multiselect(
                "选择标签",
                options=tag_names,
                default=current_tag_names,
                key=f"tag_select_{r['index']}",
            )
            if selected != current_tag_names:
                r["tags"] = [{"name": n, "evidence": ""} for n in selected]
                r["is_uncertain"] = (len(selected) == 0)
                r["manual_override"] = True
                r["confidence"] = "medium" if selected else "low"

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← 返回上一步"):
            go_to_step(3)
    with col2:
        remaining = sum(1 for r in reviews if r.get("is_uncertain"))
        if remaining == 0:
            if st.button("✅ 全部处理完成，导出结果", type="primary"):
                go_to_step(5)
        else:
            st.warning(f"还有 {remaining} 条未处理，请完成后再导出")
            if st.button("✅ 完成审阅，导出结果", type="primary"):
                go_to_step(5)


# ==================== Step 5: 导出结果 ====================

def render_step_5():
    st.title("📥 导出结果")
    st.caption("审阅最终结果，然后下载带标签的完整 Excel 文件")

    reviews = st.session_state.get("reviews", [])
    tags = st.session_state.get("tags", [])
    raw_df = st.session_state.get("raw_df")

    if reviews is None or raw_df is None:
        st.error("没有数据")
        return

    # 统计摘要
    total = len(reviews)
    tagged = sum(1 for r in reviews if r.get("tags"))
    uncertain = sum(1 for r in reviews if r.get("is_uncertain"))

    st.subheader("📊 最终统计")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总评论", total)
    col2.metric("已打标", tagged)
    col3.metric("不确定", uncertain)
    col4.metric("标签数", len(tags))

    # 标签分布
    if tags:
        st.subheader("📈 标签分布")
        tag_count = {}
        for t in tags:
            tag_count[t["name"]] = 0
        for r in reviews:
            for t_name in _get_tag_names(r):
                tag_count[t_name] = tag_count.get(t_name, 0) + 1

        sorted_tags = sorted(tag_count.items(), key=lambda x: x[1], reverse=True)
        if sorted_tags:
            chart_data = pd.DataFrame(sorted_tags, columns=["标签", "评论数"])
            st.bar_chart(chart_data.set_index("标签"))

    # 最终结果预览
    st.subheader("📋 最终结果预览（前30条）")
    preview_data = []
    for r in reviews[:30]:
        preview_data.append({
            "星级": r["rating"],
            "评论": r["content"][:60] + "...",
            "标签": ", ".join(_get_tag_names(r)),
            "置信度": r.get("confidence", ""),
            "不确定": "是" if r.get("is_uncertain") else "",
            "人工修改": "是" if r.get("manual_override") else "",
        })
    st.dataframe(preview_data, use_container_width=True)

    # 生成下载文件
    output = write_reviews(reviews, raw_df, tags=tags)
    st.download_button(
        label="💾 下载完整标签结果 Excel",
        data=output,
        file_name="亚马逊评论_打标签结果.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← 返回审阅不确定项"):
            go_to_step(4)
    with col2:
        if st.button("🔄 重新开始（处理新文件）"):
            reset_all()


# ==================== 主流程 ====================

def main():
    render_sidebar()

    step = st.session_state.get("step", 0)
    renderers = {
        0: render_step_0,
        1: render_step_1,
        2: render_step_2,
        3: render_step_3,
        4: render_step_4,
        5: render_step_5,
    }
    renderers.get(step, render_step_0)()


if __name__ == "__main__":
    main()
