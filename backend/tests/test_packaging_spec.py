"""tsp.spec 打包不变量 — 运行时动态发现的模块必须随冻结产物发布 (issue #459)。

app/extensions/loader.py 用 importlib.import_module + pkgutil.iter_modules 在
运行时发现 app/custom/*, 两条链路对 PyInstaller 静态分析都不可见; tsp.spec
未声明时冻结产物里扩展静默消失(桌面版 AI 对话助手 405, 且无任何日志)。
锁定双声明契约: 
  - hiddenimports(collect_submodules) 进 PYZ —— 保 import_module 可导入; 
  - datas 落盘到 app/custom —— frozen 下 app.__path__ 解析到 _internal/app, 
    保 pkgutil.iter_modules 可枚举(与 fuyao 插件同一处理方式)。
"""

import re
from pathlib import Path
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_TEXT = (REPO_ROOT / "packaging" / "tsp.spec").read_text(encoding="utf-8")
# 只断言代码行, 避免被注释里的字样误判为已声明
SPEC_CODE = "\n".join(
    line for line in SPEC_TEXT.splitlines() if not line.lstrip().startswith("#")
)


def test_spec_declares_custom_extensions():
    """app/custom/* 双声明: PYZ 可导入 + 磁盘可枚举, 缺一冻结后扩展静默消失。"""
    assert 'collect_submodules("app.custom")' in SPEC_CODE
    assert '"app/custom"' in SPEC_CODE


def test_spec_carries_strategy_prompts_as_files():
    assert '(str(ROOT / "backend" / "app" / "strategy" / "prompts"), "app/strategy/prompts")' in SPEC_CODE


def test_spec_dynamically_declares_active_builtin_plugins():
    """所有带 plugin.yaml 的内置插件必须由 spec 同时收集清单与动态入口。"""
    assert (REPO_ROOT / "backend" / "app" / "plugins" / "fuyao" / "plugin.yaml").is_file()
    assert "for _pdir in sorted(_plugins_root.iterdir())" in SPEC_CODE
    assert 'datas += [(str(_manifest), f"app/plugins/{_pdir.name}")]' in SPEC_CODE
    assert 'hiddenimports += [f"app.plugins.{_pdir.name}.provider"]' in SPEC_CODE
    assert 'for _runtime_asset in ("bridge.mjs", "package.json", "package-lock.json")' in SPEC_CODE
    assert '_node_modules = _pdir / "node_modules"' in SPEC_CODE


def test_spec_excludes_disabled_tdxquant_plugin():
    """tdxquant 已通过 plugin.yaml.disabled 下线，不能重新被动态收集进桌面包。"""
    assert "tdxquant 已通过 plugin.yaml.disabled 下线, 不打包" in SPEC_TEXT
    assert "app.plugins.tdxquant.provider" not in SPEC_CODE
    assert "app.plugins.tdxquant.client" not in SPEC_CODE
    assert "app.plugins.tdxquant.probe" not in SPEC_CODE
    assert '"plugins" / "tdxquant" / "plugin.yaml"' not in SPEC_CODE
    assert "for _pdir in sorted(_plugins_root.iterdir())" in SPEC_CODE


def test_installer_launches_desktop_only_once():
    """重复 postinstall 条目会同时拉起两个桌面后端。"""
    text = (REPO_ROOT / "packaging" / "tsp.iss").read_text(encoding="utf-8")
    section = text.split("[Run]", 1)[1].split("[UninstallRun]", 1)[0]
    launches = [
        line for line in section.splitlines()
        if line.startswith("Filename:") and "{#MyAppExeName}" in line
    ]
    assert len(launches) == 1


def test_installer_deploys_dotnet_config_beside_executable():
    """.NET 只读取 EXE 同目录的配置，放进 _internal 无法解除组件来源限制。"""
    installer = REPO_ROOT / "packaging" / "tsp.iss"
    text = installer.read_text(encoding="utf-8")
    exe_name = re.search(r'#define MyAppExeName\s+"([^"]+)"', text)
    assert exe_name is not None
    config_name = f"{exe_name.group(1)}.config"
    section = text.split("[Files]", 1)[1].split("[Icons]", 1)[0]
    entries = [
        line for line in section.splitlines()
        if line.startswith(f'Source: "{config_name}";')
    ]
    assert len(entries) == 1, "Windows 安装包必须显式携带 EXE 配置文件"
    assert 'DestDir: "{app}";' in entries[0]
    assert "ignoreversion" in entries[0]

    config = ElementTree.parse(installer.parent / config_name)
    assert config.getroot().tag == "configuration"
    setting = config.find("./runtime/loadFromRemoteSources")
    assert setting is not None
    assert setting.get("enabled") == "true"
