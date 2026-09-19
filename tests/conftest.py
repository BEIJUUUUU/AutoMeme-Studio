"""
测试环境引导。

在导入任何测试模块之前，确保 `import pytest` 可用：
  - 已安装 pytest   -> 直接使用
  - 未安装 pytest   -> 注册本目录的 shim 作为替代

这样测试文件可以统一写 `import pytest`，无需关心环境。
"""

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

try:
    import pytest  # noqa: F401
except ImportError:
    import _pytest_shim
    sys.modules["pytest"] = _pytest_shim
