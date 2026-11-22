"""Only reviewed, actionable index errors are displayed to students, including for legacy rows."""
from enum import StrEnum

from app.shared.upload_policy import MAX_SOURCE_ARCHIVE_MB


class IndexIssue(StrEnum):
    ARCHIVE_TYPE = "源码分析支持 ZIP，请将项目源码打包为 ZIP 后上传"
    ARCHIVE_SIZE = f"源码分析支持 {MAX_SOURCE_ARCHIVE_MB} MiB 以内的 ZIP，请仅打包项目源文件"
    ARCHIVE_UNREADABLE = "源码 ZIP 无法读取，请重新打包上传"
    ARCHIVE_DIRECTORY = "源码 ZIP 目录过大或格式不支持，请仅打包项目源文件"
    ARCHIVE_CONTENT = "源码 ZIP 没有可分析的 UTF-8 源文件，请检查项目目录、文件格式和大小"
    FILE_MISSING = "源文件不存在，请重新上传材料"
    FILE_UNREADABLE = "材料无法读取，请检查文件格式或加密状态；报告可另存为 PDF、DOCX 或文本后重试"
    NO_TEXT = "未提取到足够的正文，请上传含可复制文字的报告或含源码的 ZIP 后重试"
    EMBEDDING = "向量服务未能完成索引，请稍后重试或联系管理员检查模型配置"
    AUTH = "向量模型服务认证失败，请联系管理员检查供应商绑定"
    QUOTA = "向量模型服务调用受限，请稍后重试或联系管理员检查供应商配额"
    PROVIDER = "向量模型服务暂时不可用，请稍后重试，持续失败时请联系管理员"
    QUEUE = "自动索引未完成，请检查任务队列后重试"
    UNKNOWN = "材料索引未完成，请重试或联系管理员检查索引服务"


def public_index_error(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return IndexIssue(value).value
    except ValueError:
        # Historical records may contain raw paths, request URLs or entire provider response bodies.
        return IndexIssue.UNKNOWN.value
