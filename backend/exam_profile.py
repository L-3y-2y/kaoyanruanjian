"""Every shelf carries its own exam profile.

"Match the book" is not one sentence in a prompt: a 四级 learner must get
四级-length passages, 四级 question types and 四级 grading, and a 高考 learner
must not be handed 考研 long sentences. Both the tutor prompts (chat.py) and the
article/question generator (content.py) read the profile from here, so there is
exactly one place that decides what a shelf means.

qtypes are limited to the five values in `question.qtype`
(main_idea / detail / vocabulary / inference / subjective) — the profile picks a
subset and its length, not new enum values.
"""

PROFILES = {
    "ky": {
        "exam": "考研英语",
        "exam_en": "Chinese postgraduate entrance exam (KaoYan English I/II)",
        "level": (
            "词汇以考研大纲核心词为主；句子偏长，允许定语从句、分词结构、插入语；"
            "话题偏学术、社会评论与科普。难度对标考研英语一/二。"
        ),
        "practice_types": (
            "  * 完形/选词填空：180-240 词，恰好 10 空（__1__ 到 __10__），A-J 十个词选项。\n"
            "  * 阅读理解：350-420 词文章 + 5 道四选一（主旨/细节/推断/词义），文末附答案与解析。\n"
            "  * 翻译：英译中，5 个划线长难句或一小段，文末附参考译文。\n"
            "  * 写作：小作文（书信/通知，约 100 词）、大作文（图画/图表，160-200 词）。"
        ),
        "article": (150, 185),
        "cloze": {"words": (180, 240)},
        "reading": {
            "words": (350, 420),
            "qtypes": ("main_idea", "detail", "inference", "vocabulary", "subjective"),
        },
    },
    "cet4": {
        "exam": "大学英语四级",
        "exam_en": "CET-4 (College English Test Band 4)",
        "level": (
            "词汇以四级高频词为主；句子中等长度，避免考研式长难句和生僻学术词；"
            "话题偏校园、生活、文化、科普。难度对标四级真题。"
        ),
        "practice_types": (
            "  * 选词填空：180-230 词，10 空，A-J 词库（四级真题为 15 选 10，这里简化为 10 个选项）。\n"
            "  * 仔细阅读：280-330 词文章 + 4 道四选一（主旨/细节/推断/词义），文末附答案与解析。\n"
            "  * 翻译：汉译英段落，140-160 个汉字，题材为中国文化、社会、生活。\n"
            "  * 写作：120-180 词（书信/议论文/图表），不要考研式图画作文。"
        ),
        "article": (120, 150),
        "cloze": {"words": (180, 230)},
        "reading": {
            "words": (280, 330),
            "qtypes": ("main_idea", "detail", "inference", "vocabulary"),
        },
    },
    "cet6": {
        "exam": "大学英语六级",
        "exam_en": "CET-6 (College English Test Band 6)",
        "level": (
            "词汇以六级核心词为主；句子较长但表达清晰，可以出现抽象话题；"
            "话题偏社会、科技、文化评论。难度对标六级真题。"
        ),
        "practice_types": (
            "  * 选词填空：200-250 词，10 空，A-J 词库（真题 15 选 10 的简化版）。\n"
            "  * 仔细阅读：320-370 词文章 + 5 道四选一（主旨/细节/推断/词义/简答），文末附答案与解析。\n"
            "  * 翻译：汉译英段落，180-200 个汉字，题材为中国文化、历史、科技、社会。\n"
            "  * 写作：150-200 词议论文，要求观点清晰、论证具体。"
        ),
        "article": (130, 165),
        "cloze": {"words": (200, 250)},
        "reading": {
            "words": (320, 370),
            "qtypes": ("main_idea", "detail", "inference", "vocabulary", "subjective"),
        },
    },
    "gk": {
        "exam": "高考英语",
        "exam_en": "Chinese National College Entrance Examination (Gaokao English)",
        "level": (
            "词汇以高考大纲 3500 词为主；句子短、结构清晰，多用常见时态；"
            "话题偏校园生活、成长故事与日常科普。难度对标高考全国卷。"
        ),
        "practice_types": (
            "  * 完形填空：150-200 词，10 空，A-J 词库。\n"
            "  * 阅读理解：240-290 词文章 + 4 道四选一（主旨/细节/推断/词义），文末附答案与解析。\n"
            "  * 翻译：句子汉译英或语法填空（单句），不要成段长难句。\n"
            "  * 写作：应用文（书信/通知，约 80 词）、读后续写（约 150 词）。"
        ),
        "article": (100, 130),
        "cloze": {"words": (150, 200)},
        "reading": {
            "words": (240, 290),
            "qtypes": ("main_idea", "detail", "inference", "vocabulary"),
        },
    },
}

DEFAULT_CODE = "ky"


def profile_for(book):
    """Resolve a shelf row (dict with code/exam_type) to its exam profile."""
    if not book:
        return PROFILES[DEFAULT_CODE]
    code = str(book.get("code") or "").strip().lower()
    if code in PROFILES:
        return PROFILES[code]
    exam = str(book.get("exam_type") or "")
    if "四级" in exam:
        return PROFILES["cet4"]
    if "六级" in exam:
        return PROFILES["cet6"]
    if "高考" in exam:
        return PROFILES["gk"]
    return PROFILES[DEFAULT_CODE]


def word_range(profile, kind="daily"):
    """Passage length for an article: daily short text vs weekly reading."""
    if kind == "weekly":
        return profile["reading"]["words"]
    return profile["article"]


def qtypes_for(profile):
    return tuple(profile["reading"]["qtypes"])
