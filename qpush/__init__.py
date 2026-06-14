"""quick-pusher:一个多仓库同步提交并推送的命令行工具。"""

__version__ = "0.1.0"

from .cli import main

__all__ = ["main", "__version__"]
