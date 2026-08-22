from datetime import datetime


def get_local_now() -> datetime:
    """取得統一的本地時間 (無時區偏移混用問題)"""
    return datetime.now()


def format_iso(dt: datetime | None = None) -> str:
    """格式化為標準 ISO 格式字串"""
    d = dt or get_local_now()
    return d.strftime("%Y-%m-%d %H:%M:%S")


def get_today_str() -> str:
    """取得今日日期字串 YYYY-MM-DD"""
    return get_local_now().strftime("%Y-%m-%d")
