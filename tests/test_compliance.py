"""发布合规性测试：直接用 AstrBot 自身的加载/校验逻辑检查本插件仓库。

这些测试是「能否被 AstrBot 正确安装与展示」的可执行证据：

* ``metadata.yaml`` 必须通过 ``_PluginUpdater.inspect_plugin_directory``；
* 插件目录名必须等于 ``metadata.name`` 且可作为模块名导入；
* ``_conf_schema.json`` 必须能被 ``PluginManager._load_plugin_config_schema`` 解析；
* ``.astrbot-plugin/i18n/*.json`` 必须能被 ``PluginManager._load_plugin_i18n`` 加载，
  且 ``views.<view>.title`` 等键可被面板解析；
* ``views/<view>/index.html`` 必须能被 ``PluginPageService.discover_plugin_pages`` 发现；
* ``logo.png`` / ``README.md`` / ``CHANGELOG.md`` / ``LICENSE`` 等发布资源齐备；
* 仓库不得包含明文登录态、绝对路径或缓存产物。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import bili_constants as C

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _astrbot_updater():
    from astrbot.core.star.updater import _PluginUpdater

    return _PluginUpdater


def _plugin_manager_cls():
    from astrbot.core.star.star_manager import PluginManager

    return PluginManager


def _flatten_keys(node: object, prefix: str = "") -> set[str]:
    """把嵌套的 i18n 字典摊平成 ``a.b.c`` 键集合。"""
    flat: set[str] = set()
    if not isinstance(node, dict):
        return flat
    for key, value in node.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat |= _flatten_keys(value, full)
        else:
            flat.add(full)
    return flat


class TestMetadataGate:
    def test_inspect_plugin_directory_passes(self):
        inspected = _astrbot_updater().inspect_plugin_directory(ROOT)
        assert inspected["metadata_entry"] == "metadata.yaml"
        metadata = inspected["metadata"]
        assert metadata["name"] == C.PLUGIN_NAME
        assert str(metadata["version"]).lstrip("v") == C.PLUGIN_VERSION
        assert metadata["desc"] and metadata["author"]

    def test_metadata_is_utf8_and_small(self):
        raw = (ROOT / "metadata.yaml").read_bytes()
        assert len(raw) < 1024 * 1024
        raw.decode("utf-8")

    def test_dir_name_equals_metadata_name(self):
        """AstrBot 用 metadata.name 作为导入名与目录名，两者必须一致。"""
        assert ROOT.name == C.PLUGIN_NAME

    def test_metadata_name_is_importable(self):
        manager = _plugin_manager_cls()
        manager._validate_importable_name(C.PLUGIN_NAME)
        assert manager._normalize_plugin_dir_name(C.PLUGIN_NAME) == C.PLUGIN_NAME

    def test_validate_plugin_metadata_accepts_ours(self):
        import yaml

        metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
        _astrbot_updater().validate_plugin_metadata(metadata, "metadata.yaml")

    def test_metadata_declares_required_release_fields(self):
        import yaml

        metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
        for field in ("name", "desc", "short_desc", "version", "author", "repo", "support_platforms"):
            assert metadata.get(field), f"metadata.yaml 缺少 {field}"
        assert metadata["repo"].startswith("https://github.com/")
        assert metadata["support_platforms"] == ["aiocqhttp"]

    def test_astrbot_version_specifier_is_pep440(self):
        import yaml

        metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
        manager = _plugin_manager_cls()
        # 返回 (是否合法, 错误信息)
        ok, error = manager._validate_astrbot_version_specifier(metadata["astrbot_version"])
        assert ok is True, error


class TestConfigSchemaGate:
    def test_schema_loads_through_astrbot(self):
        manager = _plugin_manager_cls()
        schema = manager._load_plugin_config_schema(str(ROOT / "_conf_schema.json"))
        assert isinstance(schema, dict) and schema

    def test_schema_is_utf8_json_object(self):
        raw = (ROOT / "_conf_schema.json").read_bytes()
        assert len(raw) < 1024 * 1024
        assert isinstance(json.loads(raw.decode("utf-8")), dict)

    def test_schema_groups_and_items_well_formed(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        allowed_types = {"int", "float", "bool", "string", "text", "list", "dict", "object"}
        for group, spec in schema.items():
            assert spec["type"] == "object", group
            assert spec.get("description"), group
            for key, item in spec["items"].items():
                assert item["type"] in allowed_types, (group, key)
                assert item.get("description"), (group, key)
                assert "default" in item, (group, key)
                assert item.get("hint"), (group, key)

    def test_schema_matches_runtime_defaults(self):
        """schema 默认值必须与 bili_constants 中的运行时默认值一致。"""
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        sub = schema["subscription"]["items"]
        assert sub["poll_interval_seconds"]["default"] == C.DEFAULT_POLL_INTERVAL_SECONDS
        assert sub["timeout_seconds"]["default"] == C.DEFAULT_TIMEOUT_SECONDS
        assert sub["dynamic_limit"]["default"] == C.DEFAULT_DYNAMIC_LIMIT
        assert sub["uid_concurrency"]["default"] == C.DEFAULT_UID_CONCURRENCY
        assert sub["empty_retry_count"]["default"] == C.DEFAULT_EMPTY_RETRY_COUNT
        assert sub["min_request_interval_seconds"]["default"] == C.DEFAULT_MIN_REQUEST_INTERVAL_SECONDS
        download = schema["video_download"]["items"]
        assert download["quality"]["default"] == C.DEFAULT_QUALITY_LABEL
        assert download["max_size_mb"]["default"] == C.DEFAULT_VIDEO_MAX_SIZE_MB


class TestI18nGate:
    @pytest.fixture(scope="class")
    def i18n(self) -> dict:
        return _plugin_manager_cls()._load_plugin_i18n(str(ROOT))

    def test_both_locales_load(self, i18n):
        assert set(i18n) == {"zh-CN", "en-US"}

    def test_metadata_display_name_translated(self, i18n):
        for locale, data in i18n.items():
            assert data["metadata"]["display_name"], locale
            assert data["metadata"]["desc"], locale

    def test_view_title_resolvable_like_dashboard(self, i18n):
        from astrbot.dashboard.services.plugin_page_service import PluginPageService

        for locale, data in i18n.items():
            title = PluginPageService.get_by_path(data, "views.overview.title")
            assert isinstance(title, str) and title, locale

    def test_locale_key_sets_are_identical(self, i18n):
        def flatten(data: dict, prefix: str = "") -> set[str]:
            keys: set[str] = set()
            for key, value in data.items():
                path = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    keys |= flatten(value, path)
                else:
                    keys.add(path)
            return keys

        zh, en = flatten(i18n["zh-CN"]), flatten(i18n["en-US"])
        assert zh == en


class TestViewsGate:
    @pytest.fixture
    def service(self):
        from astrbot.core.star.star import StarMetadata
        from astrbot.dashboard.services.plugin_page_service import PluginPageService

        metadata = StarMetadata(
            name=C.PLUGIN_NAME,
            author=C.PLUGIN_AUTHOR,
            desc="test",
            version=C.PLUGIN_VERSION,
            root_dir_name=ROOT.name,
            reserved=False,
            i18n=_plugin_manager_cls()._load_plugin_i18n(str(ROOT)),
        )
        manager = SimpleNamespace(
            plugin_store_path=str(ROOT.parent),
            reserved_plugin_path=str(ROOT.parent),
            context=SimpleNamespace(get_all_stars=lambda: [metadata]),
        )
        return PluginPageService(manager), metadata

    async def test_view_is_discovered(self, service):
        page_service, metadata = service
        pages = await page_service.discover_plugin_pages(metadata)
        assert [page.name for page in pages] == ["overview"]
        assert pages[0].entry_file == "index.html"

    async def test_view_entry_file_is_served(self, service):
        page_service, metadata = service
        root = await page_service.resolve_plugin_pages_root(metadata)
        content = (root / "overview" / "index.html").read_text(encoding="utf-8")
        assert "AstrBotPluginView" in content
        assert "/api/plugin/page/bridge-sdk.js" in content

    async def test_initial_context_uses_our_i18n(self, service, monkeypatch):
        page_service, metadata = service
        ctx = page_service.build_initial_context(
            asset_token="",
            locale="zh-CN",
            theme=None,
        )
        assert ctx is None  # 无 token 时不应构造上下文

        # 直接复用面板的取值逻辑验证 title 解析结果
        from astrbot.dashboard.services.plugin_page_service import PluginPageService

        assert (
            PluginPageService.get_by_path(
                metadata.i18n["zh-CN"],
                "views.overview.title",
            )
            == "B站插件概览"
        )

    def test_view_html_has_no_remote_assets(self):
        """视图页面不得引用外部 CDN（面板会注入 bridge，且离线环境需可用）。"""
        html = (ROOT / "views" / "overview" / "index.html").read_text(encoding="utf-8")
        assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', html)
        assert "<script" in html and "<style>" in html

    def test_view_i18n_keys_are_translated(self):
        """视图里 t("key") 用到的键，两种语言都必须存在，否则界面会出现裸键名。"""
        html = (ROOT / "views" / "overview" / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r"""\bt\(\s*['"]([^'"]+)['"]""", html))
        assert used, "插件视图未使用 i18n"

        for locale in ("zh-CN", "en-US"):
            data = json.loads(
                (ROOT / ".astrbot-plugin" / "i18n" / f"{locale}.json").read_text(encoding="utf-8"),
            )
            missing = sorted(key for key in used if key not in _flatten_keys(data))
            assert not missing, f"{locale} 缺少文案：{missing}"

    def test_view_data_fields_exist_in_health_snapshot(self, plugin_factory):
        """视图读取的字段必须真实存在于健康快照，避免整页显示 undefined。"""
        snapshot = plugin_factory({}).health_snapshot()
        for path in (
            "plugin",
            "display_name",
            "version",
            "uptime_seconds",
            "healthy",
            "sessions",
            "subscriptions",
            "schema_version",
            "cookie_configured",
            "cookie_masked",
            "features.parse",
            "features.video_info",
            "features.download",
            "features.llm_tool",
            "poll.interval_seconds",
            "poll.concurrency",
            "poll.dynamic_limit",
            "poll.min_request_interval_seconds",
            "stats.http_requests",
            "stats.http_failures",
            "stats.dynamic_pushes",
            "stats.live_pushes",
            "stats.poll_cycles",
            "stats.downloads",
            "stats.download_bytes",
            "stats.send_failures",
        ):
            node: object = snapshot
            for part in path.split("."):
                assert isinstance(node, dict) and part in node, f"健康快照缺少字段：{path}"
                node = node[part]

    def test_view_script_passes_node_smoke_test(self):
        """用 node 对内联脚本做语法校验，并以真实语言包 + 样例快照渲染一次。"""
        node_bin = shutil.which("node")
        if not node_bin:
            pytest.skip("当前环境未安装 node，跳过插件页面渲染冒烟")
        result = subprocess.run(
            [node_bin, str(ROOT / "scripts" / "check_view.mjs")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


class TestReleaseAssets:
    def test_logo_png_is_valid(self):
        from PIL import Image

        with Image.open(ROOT / "logo.png") as image:
            assert image.format == "PNG"
            width, height = image.size
        assert width == height >= 256
        assert (ROOT / "logo.png").stat().st_size < 512 * 1024

    def test_required_docs_exist(self):
        for name in ("README.md", "CHANGELOG.md", "LICENSE", "requirements.txt"):
            path = ROOT / name
            assert path.is_file() and path.stat().st_size > 0, name

    def test_requirements_are_pinned_ranges(self):
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        assert "aiohttp" in text
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            assert re.match(r"^[A-Za-z0-9_.\-\[\]]+\s*[<>=!~]", line), line

    def test_pyproject_declares_coverage_gate(self):
        import tomllib

        with (ROOT / "pyproject.toml").open("rb") as fp:
            data = tomllib.load(fp)
        cov = data["tool"]["coverage"]["report"]
        assert cov["fail_under"] >= 95
        assert data["tool"]["coverage"]["run"]["branch"] is True
        assert "tests" in data["tool"]["pytest"]["ini_options"]["testpaths"]


class TestRepositoryHygiene:
    FORBIDDEN_PATTERNS = (
        re.compile(r"SESSDATA=[A-Za-z0-9%*_\-]{10,}"),
        re.compile(r"bili_jct=[0-9a-f]{16,}"),
        re.compile(r"[A-Za-z]:\\\\Users\\\\"),
        re.compile(r"/home/[a-z]+/"),
    )

    def test_no_plaintext_credentials_or_absolute_paths(self):
        offenders: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if rel.parts[0] in {".git", ".venv", "data", "downloads", "__pycache__", ".pytest_cache"}:
                continue
            if "tests" in rel.parts and path.name != "test_compliance.py":
                continue
            if path.suffix.lower() not in {".py", ".json", ".yaml", ".yml", ".md", ".toml", ".txt", ".html", ".cfg"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in self.FORBIDDEN_PATTERNS:
                if pattern.search(text):
                    offenders.append(f"{rel}: {pattern.pattern}")
        assert not offenders, offenders

    def test_gitignore_covers_runtime_artifacts(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for token in (".venv", "__pycache__", ".coverage", "coverage.xml", "data/", "downloads/"):
            assert token in text, token

    def test_ci_and_badge_assets_exist(self):
        for rel in (
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".pre-commit-config.yaml",
            ".gitattributes",
            "scripts/check_view.mjs",
            "docs/badges/coverage.svg",
            "requirements-dev.txt",
            "pyproject.toml",
            "SECURITY.md",
            "CONTRIBUTING.md",
        ):
            path = ROOT / rel
            assert path.is_file() and path.stat().st_size > 0, rel

    def test_plugin_imports_as_package_like_astrbot_does(self, tmp_path):
        """按 AstrBot 的加载方式（包目录 + 相对导入）导入插件，验证 `main.py` 的相对导入分支。"""
        package = tmp_path / C.PLUGIN_NAME
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        for name in (
            "main.py",
            "bili_constants.py",
            "bili_helpers.py",
            "bili_logic.py",
            "bili_models.py",
        ):
            shutil.copy2(ROOT / name, package / name)
        for asset in ("metadata.yaml", "_conf_schema.json", "logo.png"):
            shutil.copy2(ROOT / asset, package / asset)

        probe = (
            "import importlib, sys\n"
            f"sys.path.insert(0, r'{tmp_path}')\n"
            f"module = importlib.import_module('{C.PLUGIN_NAME}.main')\n"
            "assert module.BiliAllPlugin.__module__ == "
            f"'{C.PLUGIN_NAME}.main', module.BiliAllPlugin.__module__\n"
            "assert module.Subscription.__module__ == "
            f"'{C.PLUGIN_NAME}.bili_models', module.Subscription.__module__\n"
            "print('package-import-ok')\n"
        )
        result = subprocess.run(
            [__import__("sys").executable, "-c", probe],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "package-import-ok" in result.stdout

    def test_workflows_are_valid_yaml(self):
        import yaml

        for rel in (".github/workflows/ci.yml", ".github/workflows/release.yml"):
            data = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
            assert isinstance(data, dict) and "jobs" in data, rel

    def test_no_runtime_artifacts_are_tracked(self):
        """运行期产物不得出现在版本库里（用 git 索引判断，无 .git 时跳过）。"""
        if not (ROOT / ".git").exists():
            pytest.skip("非 git 工作区")
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        tracked = result.stdout.splitlines()
        banned = ("__pycache__/", ".pytest_cache/", ".venv/", "data/", "downloads/")
        assert not [f for f in tracked if f.startswith(banned)], "运行期产物被提交"
