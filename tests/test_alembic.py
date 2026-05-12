# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Static checks for the Alembic configuration.

Real "apply migration to a live DB" tests live in
``tests/integration/test_migration.py`` (testcontainers Postgres). These
unit tests run in every ``pytest`` invocation and verify:

- ``alembic.ini`` parses
- the script_location resolves
- the initial revision is the head and depends on nothing
- the revision script imports and exposes ``upgrade`` / ``downgrade``
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


@pytest.fixture(scope="module")
def alembic_cfg() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    # Make sure relative ``script_location = migrations`` resolves
    # regardless of pytest's cwd.
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return cfg


@pytest.fixture(scope="module")
def script_dir(alembic_cfg: Config) -> ScriptDirectory:
    return ScriptDirectory.from_config(alembic_cfg)


class TestConfig:
    def test_ini_exists(self) -> None:
        assert ALEMBIC_INI.is_file()

    def test_script_dir_resolves(self, script_dir: ScriptDirectory) -> None:
        assert Path(script_dir.dir).is_dir()


class TestRevisionGraph:
    def test_exactly_one_head(self, script_dir: ScriptDirectory) -> None:
        heads = script_dir.get_heads()
        assert len(heads) == 1, f"expected single head, got {heads}"

    def test_initial_revision_is_root(self, script_dir: ScriptDirectory) -> None:
        revs = list(script_dir.walk_revisions())
        # Oldest first when reversed.
        root = revs[-1]
        assert root.down_revision is None
        assert root.revision == "0001_initial_schema"

    def test_no_branches(self, script_dir: ScriptDirectory) -> None:
        for rev in script_dir.walk_revisions():
            # branch_labels can be set deliberately; what we don't want is
            # multiple ``down_revisions`` pointing to one revision (merge
            # commit in migration history).
            assert isinstance(rev.down_revision, (str, type(None))), (
                f"branch in migration graph at {rev.revision}"
            )


class TestInitialRevisionModule:
    @pytest.fixture
    def module(self) -> object:
        # Path-based import — versions/ is not a package because the
        # filename starts with a digit. Alembic loads them via importlib
        # at runtime; we mirror that mechanism for the test.
        target = REPO_ROOT / "migrations" / "versions" / "20260512_0830_0001_initial_schema.py"
        assert target.is_file()
        spec = importlib.util.spec_from_file_location("callendulla_migrations.initial", target)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_revision_id_matches_filename(self, module: object) -> None:
        assert module.revision == "0001_initial_schema"

    def test_down_revision_is_none(self, module: object) -> None:
        assert module.down_revision is None

    def test_upgrade_and_downgrade_callable(self, module: object) -> None:
        assert callable(module.upgrade)
        assert callable(module.downgrade)


class TestSchemaMatchesModels:
    """Hand-written migration must cover every table the models declare."""

    def test_initial_creates_every_table(self) -> None:
        # Parse the migration file as text — robust to import order and
        # avoids running upgrade() against a fake connection.
        target = REPO_ROOT / "migrations" / "versions" / "20260512_0830_0001_initial_schema.py"
        body = target.read_text()
        # Every table from callendulla.db.Base.metadata must appear in
        # an op.create_table() call. If you add a model, you add the
        # migration.
        from callendulla.db import Base  # noqa: PLC0415

        for tablename in Base.metadata.tables:
            marker = f'op.create_table(\n        "{tablename}"'
            assert marker in body, (
                f"table {tablename!r} declared in models but missing from initial migration"
            )


class TestMigrationAppliesOnSQLite:
    """Smoke-test that ``alembic upgrade`` actually executes.

    Postgres-only features (JSONB) are guarded by ``with_variant`` so the
    migration runs on SQLite too — enough to catch typos, mismatched
    column names, and stale references. Full Postgres-targeted apply
    lives in ``tests/integration/`` via testcontainers.
    """

    def test_upgrade_and_downgrade_cycle(self, tmp_path: Path, alembic_cfg: Config) -> None:
        from alembic import command  # noqa: PLC0415
        from sqlalchemy import create_engine, text  # noqa: PLC0415

        db_file = tmp_path / "smoke.db"
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")

        command.upgrade(alembic_cfg, "head")

        engine = create_engine(f"sqlite:///{db_file}")
        try:
            with engine.connect() as conn:
                rows = list(conn.execute(text("SELECT version_num FROM alembic_version")))
            assert rows == [("0001_initial_schema",)]

            # Downgrade back to empty and verify the version row is gone.
            command.downgrade(alembic_cfg, "base")
            with engine.connect() as conn:
                rows = list(conn.execute(text("SELECT version_num FROM alembic_version")))
            assert rows == []
        finally:
            engine.dispose()
