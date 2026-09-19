"""
零依赖测试运行器。

环境中可能没有 pytest，本脚本用标准库实现最小的测试发现与执行，
保证任何环境下都能自检核心逻辑。

用法:
    python tests/run_all.py
    python tests/run_all.py -v
"""

import importlib.util
import inspect
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS_DIR))


class _Skip(Exception):
    pass


def _load_module(path: Path):
    """按文件路径加载测试模块（先注入 pytest 垫片，保证 import pytest 可用）"""
    import conftest  # noqa: F401  确保 shim 已注册

    name = path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _iter_tests(module):
    """收集 Test* 类中的 test_* 方法，以及模块级 test_* 函数"""
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if not obj.__name__.startswith("Test"):
            continue
        if obj.__module__ != module.__name__:
            continue
        for mname, method in inspect.getmembers(obj, inspect.isfunction):
            if mname.startswith("test_"):
                yield f"{obj.__name__}.{mname}", obj, method

    for fname, func in inspect.getmembers(module, inspect.isfunction):
        if fname.startswith("test_") and func.__module__ == module.__name__:
            yield fname, None, func


def _resolve_fixture(mod, name: str, cache: dict):
    """
    按名称解析 fixture，支持 fixture 之间的依赖（递归解析）。

    scope='module' 由调用方通过传入同一 cache 实现。
    """
    if name in cache:
        return cache[name]

    factory = getattr(mod, name, None)
    if not callable(factory):
        raise _Skip(f"缺少可用 fixture: {name}")

    # 若该 fixture 自身也声明了参数，递归解析其依赖
    sig = inspect.signature(factory)
    kwargs = {}
    for p in sig.parameters.values():
        if p.default is not inspect.Parameter.empty:
            continue
        kwargs[p.name] = _resolve_fixture(mod, p.name, cache)

    value = factory(**kwargs)
    cache[name] = value
    return value


def _call_with_fixtures(mod, instance, func, cache):
    """
    调用测试函数，按参数名自动注入 fixture。

    pytest 的 fixture 注入发生在测试方法参数上，本运行器模仿这一行为。
    """
    sig = inspect.signature(func)
    kwargs = {}
    for p in sig.parameters.values():
        if p.name == "self":
            continue
        if p.default is not inspect.Parameter.empty:
            continue
        kwargs[p.name] = _resolve_fixture(mod, p.name, cache)

    if instance is not None:
        func(instance, **kwargs)
    else:
        func(**kwargs)


def main() -> int:
    verbose = "-v" in sys.argv

    test_files = sorted(f for f in TESTS_DIR.glob("test_*.py"))
    if not test_files:
        print("未发现测试文件")
        return 1

    total = passed = failed = skipped = 0
    failures = []

    for path in test_files:
        print(f"\n=== {path.name} ===")
        try:
            module = _load_module(path)
        except Exception:
            print(f"  [加载失败] {path.name}")
            traceback.print_exc()
            failed += 1
            continue

        for name, cls, func in _iter_tests(module):
            total += 1
            label = f"{path.stem}::{name}"
            try:
                inst = cls() if cls is not None else None
                # 每个测试一份独立 fixture 缓存，避免状态串扰；
                # scope=module 的 fixture 由调用方按需自行缓存
                _call_with_fixtures(module, inst, func, {})
                passed += 1
                if verbose:
                    print(f"  PASS  {name}")
            except _Skip as e:
                skipped += 1
                print(f"  SKIP  {name}: {e}")
            except AssertionError as e:
                failed += 1
                failures.append((label, str(e) or "断言失败"))
                print(f"  FAIL  {name}: {e}")
            except Exception as e:
                failed += 1
                failures.append((label, f"{type(e).__name__}: {e}"))
                print(f"  ERROR {name}: {type(e).__name__}: {e}")
                if verbose:
                    traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"总计 {total}  通过 {passed}  失败 {failed}  跳过 {skipped}")
    if failures:
        print("\n失败明细:")
        for label, msg in failures:
            print(f"  - {label}: {msg}")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
