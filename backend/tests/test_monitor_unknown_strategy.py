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
