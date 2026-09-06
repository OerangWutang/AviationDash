from __future__ import annotations

import importlib
from pathlib import Path


def test_historical_migrations_do_not_import_mutable_runtime_modules():
    versions = (
        Path(__file__).parents[1]
        / "src"
        / "atlas_argus"
        / "db"
        / "migrations"
        / "versions"
    )
    offenders = []
    for path in sorted(versions.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "from atlas_argus." in source or "import atlas_argus." in source:
            offenders.append(path.name)
    assert offenders == []


def test_ingestion_migration_does_not_import_mutable_runtime_guards():
    module = importlib.import_module(
        "atlas_argus.db.migrations.versions.0017_source_ingestion"
    )
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "from atlas_argus.db.guards import" not in source


def test_case_constraint_downgrade_restores_predecessor_trigger(monkeypatch):
    module = importlib.import_module(
        "atlas_argus.db.migrations.versions.0021_case_domain_constraints"
    )
    executed: list[str] = []
    monkeypatch.setattr(module.op, "drop_constraint", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.op, "execute", executed.append)

    module.downgrade()

    assert module.PREDECESSOR_CLAIM_VERIFICATION_FIELD_GUARD_SQL in executed
    predecessor = executed[-1]
    assert "NEW.quote_verification IS DISTINCT" in predecessor
    assert "NEW.quote IS DISTINCT" not in predecessor
