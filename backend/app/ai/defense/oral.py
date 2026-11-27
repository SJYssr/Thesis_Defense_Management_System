"""Validate the utterance separately from its citation and grading data."""
import re
import unicodedata

# Technical terms such as HTTP, Redis and C++ can be spoken. File locations,
# code expressions and markup belong in a citation, never in the examiner's voice.
_WRITTEN = re.compile(
    r"[\n\r`#{}\\]|\*\*|__|(?:^|\s)[-*+]\s|^\d+[.)、](?!\d)\s*|<[/a-zA-Z][^>]*>|"
    r"[\[【][^\]】]*[\]】]|\w+/[\w.\-]+|"
    r"(?i:[\w.-]+\.(?:py|js|ts|tsx|jsx|vue|java|cpp|c|h|go|rs|rb|php|cs|sh|sql|json|toml|ya?ml|xml|md|txt|pdf|docx?|pptx?|zip))(?![A-Za-z0-9])|"
    r"[A-Za-z_]\w*\.[A-Za-z_]\w*|[a-zA-Z]\w*_\w+|[A-Za-z_]\w*\s*\(|=>|==|!=|"
    r"\b(?:return|def|function|import)\s+[a-zA-Z_]|(?:第\s*\d+\s*行|\bL\d+\b)|"
    r"所引片段|请先概述《|(?:请)?(?:根据|对照|结合|参照)(?:上述|以上|以下|本题|给出|给定)?(?:代码片段|源码片段|所给片段)|"
    r"^(?:问题|题目|追问|Question)\s*[:：]|^(?:第[一二三四五六七八九十\d]+题)[：:、]"
)
_EMPTY_REFERENCE = re.compile(r"^(?:请|请问)?(?:根据|结合|对照).{0,12}(?:文件|材料|源码|代码|报告|片段|资料)[。？！?！：:]*$")


def oral_question(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = unicodedata.normalize("NFKC", value.strip())
    return (5 <= len(text) <= 125 and bool(re.search(r"[\u3400-\u9fff]", text))
            and not _WRITTEN.search(text) and not _EMPTY_REFERENCE.fullmatch(text)
            and bool(re.search(r"[?？]|为何|怎样|怎么|如何|什么|哪些|哪[一处个种]|为什么|是否|能否|有何|多少|"
                               r"请(?:你|解释|介绍|说明|谈|比较|描述|分析|列举|举例)|讲讲|谈谈", text)))


def offers_choices(text: str) -> bool:
    """Detect forced-choice wording in a main question; a precise follow-up may still use it."""
    return bool(re.search(r"还是|对不对|是不是|会不会|是否", text)) and not re.search(r"为什么|为何|理由|依据|如何验证", text)


def question_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())
