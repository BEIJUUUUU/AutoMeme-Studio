"""
梗库与配置文件的完整性测试。

确保分发的 meme_library.json 与 config.example.json 结构正确，
且所有音频路径都能解析到真实文件（缺失时给出清晰提示）。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def library():
    with open(PROJECT_ROOT / "meme_library.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def example_config():
    path = PROJECT_ROOT / "config.example.json"
    if not path.exists():
        pytest.skip("config.example.json 不存在")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestMemeLibrary:

    def test_is_non_empty_list(self, library):
        assert isinstance(library, list)
        assert len(library) > 0

    def test_required_fields_present(self, library):
        required = {"id", "title", "filename", "triggers", "vibe"}
        for m in library:
            missing = required - set(m.keys())
            assert not missing, f"《{m.get('title')}》缺少字段 {missing}"

    def test_ids_are_sequential(self, library):
        """id 必须从 1 连续编号，不能有断层（删除音效后应自动重排）"""
        ids = [m["id"] for m in library]
        assert ids == list(range(1, len(ids) + 1)), f"id 不连续: {ids}"

    def test_titles_unique(self, library):
        titles = [m["title"] for m in library]
        assert len(titles) == len(set(titles)), "存在重复的音效标题"

    def test_filename_is_non_empty(self, library):
        for m in library:
            assert m["filename"].strip(), f"《{m['title']}》的文件名为空"

    def test_triggers_are_descriptive(self, library):
        """触发场景描述不能太短，否则漏斗和模型都无法判断"""
        for m in library:
            assert len(m["triggers"]) >= 4, f"《{m['title']}》的触发描述过短"


class TestExampleConfig:
    """config.example.json 是开源分发的模板，不能含真实密钥"""

    def test_has_required_sections(self, example_config):
        for key in ("api", "behavior", "audio"):
            assert key in example_config, f"缺少配置段 {key}"

    def test_no_real_api_key(self, example_config):
        key = example_config["api"].get("api_key", "")
        assert "YOUR_API_KEY" in key or key == "", \
            "示例配置不得包含真实 API Key"

    def test_no_private_ip(self, example_config):
        """示例配置不应带作者的个人局域网地址"""
        base = example_config["api"].get("base_url", "")
        assert "192.168." not in base, "示例配置不应包含私有局域网地址"

    def test_example_has_no_dead_keys(self, example_config):
        """示例配置不应包含代码从未读取的键（会导致用户困惑）"""
        code = ""
        for f in list(PROJECT_ROOT.glob("core/*.py")) + \
                 [PROJECT_ROOT / "gui.py", PROJECT_ROOT / "main.py"]:
            code += f.read_text(encoding="utf-8")

        dead = []
        for section, values in example_config.items():
            if not isinstance(values, dict):
                continue
            for k in values:
                if k not in code:
                    dead.append(f"{section}.{k}")
        assert not dead, f"示例配置包含无用键: {dead}"

    def test_api_section_shape(self, example_config):
        api = example_config["api"]
        for k in ("base_url", "model"):
            assert k in api and api[k]


class TestProjectLayout:

    def test_license_exists(self):
        assert (PROJECT_ROOT / "LICENSE").exists(), "缺少 LICENSE 文件"

    def test_gitignore_protects_config(self):
        gi = PROJECT_ROOT / ".gitignore"
        assert gi.exists()
        content = gi.read_text(encoding="utf-8")
        assert "config.json" in content, "config.json 必须被 gitignore 保护，避免泄露密钥"

    def test_readme_exists(self):
        assert (PROJECT_ROOT / "README.md").exists()

    def test_requirements_lists_core_deps(self):
        req = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for dep in ("requests", "numpy", "pyqt5", "pygame"):
            assert dep in req, f"requirements.txt 缺少核心依赖 {dep}"


class TestVersionConsistency:
    """版本号必须单一来源，防止 GUI / 打包元数据各写一个导致漂移"""

    def test_core_defines_version(self):
        from core import __version__
        assert __version__, "core/__init__.py 必须定义 __version__"
        assert __version__.count(".") == 2, f"版本号应为 x.y.z 格式，实际 {__version__}"

    def test_gui_reads_version_instead_of_hardcoding(self):
        """gui.py 应从 core 读取版本，而不是写死字符串"""
        src = (PROJECT_ROOT / "gui.py").read_text(encoding="utf-8")
        assert "__version__" in src, "gui.py 应引用 core.__version__"
        # 不应出现独立的 vX.Y.Z 字面量
        import re
        hardcoded = re.findall(r'"v\d+\.\d+\.\d+"', src)
        assert not hardcoded, f"gui.py 存在硬编码版本号: {hardcoded}"

    def test_pyproject_uses_dynamic_version(self):
        """pyproject 应动态读取版本，避免与代码不同步"""
        py = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "dynamic" in py and "version" in py, \
            "pyproject.toml 应使用 dynamic version"
        assert "core.__version__" in py, \
            "pyproject.toml 的版本应从 core.__version__ 读取"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
