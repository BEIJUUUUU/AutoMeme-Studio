"""
pytest 兼容垫片（shim）。

若环境未安装 pytest，本模块提供最小的 fixture / skip 支持，
使测试文件在两种环境下都能运行：

    import pytest                     -> 有 pytest 时用真的
    from tests._pytest_shim import ... -> 无 pytest 时用垫片

实际做法是在 tests/conftest.py 中尝试导入 pytest，
失败则把自己注册为 "pytest" 模块，测试文件无需改动。
"""

import functools


class Skipped(Exception):
    pass


def skip(reason: str = ""):
    raise Skipped(reason)


class _Mark:
    """支持 @pytest.mark.xxx 形式的最小实现"""

    def __getattr__(self, name):
        def decorator(*args, **kwargs):
            def wrapper(func):
                return func
            return wrapper
        return decorator


mark = _Mark()


def fixture(*args, **kwargs):
    """
    最小 fixture 实现：直接把被装饰的函数标记为 fixture 工厂，
    run_all.py 会按参数名查找并调用它。
    """
    # 支持 @pytest.fixture 与 @pytest.fixture(scope="module") 两种写法
    if len(args) == 1 and callable(args[0]) and not kwargs:
        func = args[0]
        func._is_fixture = True
        return func

    def decorator(func):
        func._is_fixture = True
        return func
    return decorator


def main(args=None):
    """无 pytest 时调用 main 不应静默失败"""
    print("未安装 pytest；请改用: python tests/run_all.py")
    return 1


def raises(exc_type):
    """with pytest.raises(...) 的最小实现"""
    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, et, ev, tb):
            if et is None:
                raise AssertionError(f"期望抛出 {exc_type.__name__}，但没有异常")
            return issubclass(et, exc_type)
    return _Ctx()
