from types import SimpleNamespace
from unittest.mock import MagicMock

from app.api import monitor_rules as monitor_rules_api
from app.strategy import monitor_rules


def _strategy_rule(rule_id: str, strategy_id: str) -> dict:
    return monitor_rules.normalize({
        "id": rule_id,
        "name": rule_id,
        "type": "strategy",
        "scope": "all",
        "strategy_id": strategy_id,
        "direction": "entry",
        "conditions": [],
        "enabled": True,
    })


def test_disable_missing_strategy_rules_is_idempotent(tmp_path):
    monitor_rules.save_one(tmp_path, _strategy_rule("exists", "exists_v1"))
    monitor_rules.save_one(tmp_path, _strategy_rule("missing", "missing_v1"))

    changed = monitor_rules.disable_missing_strategy_rules(tmp_path, {"exists_v1"})

    assert [r["id"] for r in changed] == ["missing"]
    saved = {r["id"]: r for r in monitor_rules.load_all(tmp_path)}
    assert saved["exists"]["enabled"] is True
    assert saved["missing"]["enabled"] is False
    assert monitor_rules.disable_missing_strategy_rules(tmp_path, {"exists_v1"}) == []


def test_list_rules_marks_missing_strategy_for_action(tmp_path):
    monitor_rules.save_one(tmp_path, _strategy_rule("missing", "missing_v1"))
    repo = MagicMock()
    repo.store.data_dir = tmp_path
    repo.resolve_asset_type.return_value = "stock"
    strategy_engine = SimpleNamespace(has=lambda sid: False)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=repo,
        strategy_engine=strategy_engine,
        sector_monitor_service=None,
        capabilities=None,
    )))

    listed = monitor_rules_api.list_rules(request)
    rule = next(r for r in listed["rules"] if r["id"] == "missing")

    assert "关联策略已不存在" in rule["runtime_warning"]

def _sector_rule(rule_id: str, keys: list[str]) -> dict:
    return monitor_rules.normalize({
        "id": rule_id,
        "name": rule_id,
        "type": "sector",
        "scope": "sector",
        "sector_kind": "industry",
        "sector_targets": [
            {"key": key, "kind": "industry", "name": key, "available": True}
            for key in keys
        ],
        "conditions": [],
        "enabled": True,
    })


def test_disable_missing_sector_rules_only_when_all_targets_are_gone(tmp_path):
    monitor_rules.save_one(tmp_path, _sector_rule("all_gone", ["old:a", "old:b"]))
    monitor_rules.save_one(tmp_path, _sector_rule("partial", ["old:a", "live:c"]))

    changed = monitor_rules.disable_missing_sector_rules(
        tmp_path,
        lambda targets: [str(t["key"]) for t in targets if str(t["key"]).startswith("old:")],
    )

    assert [r["id"] for r in changed] == ["all_gone"]
    saved = {r["id"]: r for r in monitor_rules.load_all(tmp_path)}
    assert saved["all_gone"]["enabled"] is False
    assert saved["partial"]["enabled"] is True
