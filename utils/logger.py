"""
小蜗 — 统一日志配置模块
提供应用级别的日志管理，替代散落的 print() 语句
"""

import logging
import sys


def get_logger(name: str = "xiaowo", level: int = None) -> logging.Logger:
    """
    获取命名 Logger 实例。

    Args:
        name: Logger 名称，建议使用模块名，如 "xiaowo.db", "xiaowo.router"
        level: 日志级别，默认 INFO

    Returns:
        配置好的 Logger 实例
    """
    logger = logging.getLogger(name)

    # 只在根 logger 上添加 handler（避免重复日志）
    if not logger.handlers and not logging.getLogger("xiaowo").handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "[%(levelname)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root = logging.getLogger("xiaowo")
        root.addHandler(handler)
        root.setLevel(level or logging.INFO)

    return logger
