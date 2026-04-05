"""메시지 포맷팅 유틸리티"""

from datetime import datetime


def format_file_size(size_bytes: int) -> str:
    """바이트를 읽기 쉬운 크기로 변환"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def format_datetime(dt: datetime) -> str:
    """날짜/시간을 한국어 형식으로 포맷"""
    return dt.strftime("%Y년 %m월 %d일 %H:%M")


def truncate(text: str, max_length: int = 50) -> str:
    """텍스트를 지정된 길이로 자르기"""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def escape_md(text: str) -> str:
    """MarkdownV2 특수문자 이스케이프"""
    special_chars = r"_*[]()~`>#+-=|{}.!"
    for char in special_chars:
        text = text.replace(char, f"\\{char}")
    return text
