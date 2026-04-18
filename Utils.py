import time
import asyncio
import functools
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def retry_async(max_retries=3, delay=1, backoff=2, exceptions=(Exception,)):
    """异步重试装饰器"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            _delay = delay
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"重试 {func.__name__} (尝试 {attempt+1}/{max_retries}): {e}")
                    await asyncio.sleep(_delay)
                    _delay *= backoff
            return None
        return wrapper
    return decorator

def get_timestamp():
    return datetime.utcnow().isoformat()

def parse_duration(duration_str: str) -> timedelta:
    """解析如 '24h', '7d' 的字符串为 timedelta"""
    unit = duration_str[-1]
    value = int(duration_str[:-1])
    if unit == 'h':
        return timedelta(hours=value)
    elif unit == 'd':
        return timedelta(days=value)
    else:
        raise ValueError(f"Unknown duration unit: {unit}")
