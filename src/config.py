# API 配置
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL_TAG_GEN = "deepseek-v4-pro"       # 标签生成：V4 Pro + 深度思考
DEEPSEEK_MODEL_TAGGING = "deepseek-v4-flash"     # 批量打标：V4 Flash，快+准
API_TIMEOUT = 120
MAX_RETRIES = 3

# 批处理配置
BATCH_SIZE = 30       # 每批评论数
CONCURRENCY = 4       # 并发批次数
SAMPLE_SIZE = 100     # 生成标签时采样的评论数

# 置信度阈值
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# 评论文本截断长度
MAX_CONTENT_LENGTH = 800

# 卖家精灵列名映射（优先匹配中文列名）
COLUMN_MAPPING = {
    "ASIN": "asin",
    "标题": "title",
    "标题(翻译)": "title_cn",
    "内容": "content",
    "内容(翻译)": "content_cn",
    "VP评论": "is_vp",
    "Vine Voice评论": "is_vine",
    "型号": "variant",
    "星级": "rating",
    "赞同数": "helpful_votes",
    "评论链接": "review_url",
    "评论人": "reviewer",
    "所属国家": "country",
    "评论时间": "review_date",
    "图片数量": "image_count",
    "是否有视频": "has_video",
}
