"""Unit tests for analysis-mode session persistence.

Covers:
- create_session with mode="analysis"
- list_analysis_sessions filtering
- Schema v4 migration (session_mode column)
- Analysis session CRUD round-trips
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scrum_agent.sessions import (
    CURRENT_SCHEMA_VERSION,
    SessionStore,
    make_session_id,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> SessionStore:
    with SessionStore(tmp_path / "sessions.db") as s:
        yield s


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_current_version_is_4(self):
        assert CURRENT_SCHEMA_VERSION == 5

    def test_new_db_has_session_mode_column(self, store: SessionStore):
        """A freshly created DB should have the session_mode column."""
        sid = make_session_id()
        store.create_session(sid, mode="analysis")
        row = store._conn.execute("SELECT session_mode FROM sessions_meta WHERE session_id = ?", (sid,)).fetchone()
        assert row is not None
        assert row[0] == "analysis"


# ---------------------------------------------------------------------------
# create_session with mode
# ---------------------------------------------------------------------------


class TestCreateSessionWithMode:
    def test_default_mode_is_planning(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid)
        row = store._conn.execute("SELECT session_mode FROM sessions_meta WHERE session_id = ?", (sid,)).fetchone()
        assert row[0] == "planning"

    def test_analysis_mode(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, project_name="Team Analysis", mode="analysis")
        row = store._conn.execute("SELECT session_mode FROM sessions_meta WHERE session_id = ?", (sid,)).fetchone()
        assert row[0] == "analysis"

    def test_duplicate_session_id_ignored(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, mode="analysis")
        # Creating again with same ID should not raise
        store.create_session(sid, mode="planning")
        # Original mode should be preserved (INSERT OR IGNORE)
        row = store._conn.execute("SELECT session_mode FROM sessions_meta WHERE session_id = ?", (sid,)).fetchone()
        assert row[0] == "analysis"

    def test_create_with_project_name(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, project_name="Platform Team", mode="analysis")
        meta = store.get_session(sid)
        assert meta is not None
        assert meta["project_name"] == "Platform Team"


# ---------------------------------------------------------------------------
# list_analysis_sessions
# ---------------------------------------------------------------------------


class TestListAnalysisSessions:
    def test_empty_store(self, store: SessionStore):
        result = store.list_analysis_sessions()
        assert result == []

    def test_only_returns_analysis_sessions(self, store: SessionStore):
        sid_plan = make_session_id()
        sid_ana1 = make_session_id()
        sid_ana2 = make_session_id()

        store.create_session(sid_plan, project_name="Plan Project", mode="planning")
        store.create_session(sid_ana1, project_name="Analysis 1", mode="analysis")
        store.create_session(sid_ana2, project_name="Analysis 2", mode="analysis")

        result = store.list_analysis_sessions()
        assert len(result) == 2
        session_ids = {r["session_id"] for r in result}
        assert sid_ana1 in session_ids
        assert sid_ana2 in session_ids
        assert sid_plan not in session_ids

    def test_ordered_by_last_modified_desc(self, store: SessionStore):
        sid1 = make_session_id()
        sid2 = make_session_id()

        store.create_session(sid1, project_name="First", mode="analysis")
        store.create_session(sid2, project_name="Second", mode="analysis")

        # Update sid1 to make it more recent via save_state (requires messages key)
        store.save_state(sid1, {"messages": []})

        result = store.list_analysis_sessions()
        assert len(result) == 2
        # sid1 should be first (most recently modified)
        assert result[0]["session_id"] == sid1

    def test_returns_expected_keys(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, project_name="Test", mode="analysis")

        result = store.list_analysis_sessions()
        assert len(result) == 1
        row = result[0]
        assert "session_id" in row
        assert "project_name" in row
        assert "created_at" in row
        assert "last_modified" in row
        assert "last_node_completed" in row

    def test_no_planning_sessions_returned(self, store: SessionStore):
        """Even with many planning sessions, list_analysis_sessions returns empty."""
        for i in range(5):
            store.create_session(make_session_id(), project_name=f"Plan {i}", mode="planning")

        result = store.list_analysis_sessions()
        assert result == []


# ---------------------------------------------------------------------------
# Analysis session state round-trip
# ---------------------------------------------------------------------------


class TestAnalysisSessionStateRoundTrip:
    def test_save_and_load_analysis_state(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, project_name="Platform", mode="analysis")

        # save_state persists the full graph state
        store.save_state(sid, {"messages": [], "instructions": "Team velocity is 23.5"})

        meta = store.get_session(sid)
        assert meta is not None
        # State should be saved as JSON
        assert meta["session_state_raw"] != ""

    def test_update_preserves_mode(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, mode="analysis")
        store.save_state(sid, {"messages": []})

        row = store._conn.execute("SELECT session_mode FROM sessions_meta WHERE session_id = ?", (sid,)).fetchone()
        assert row[0] == "analysis"

    def test_delete_analysis_session(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, mode="analysis")

        store.delete_session(sid)
        meta = store.get_session(sid)
        assert meta is None

    def test_delete_does_not_affect_other_modes(self, store: SessionStore):
        sid_ana = make_session_id()
        sid_plan = make_session_id()
        store.create_session(sid_ana, mode="analysis")
        store.create_session(sid_plan, mode="planning")

        store.delete_session(sid_ana)

        assert store.get_session(sid_plan) is not None
        assert store.get_session(sid_ana) is None


# ---------------------------------------------------------------------------
# Migration from v3 to v4
# ---------------------------------------------------------------------------


class TestMigrationV3ToV4:
    def test_migration_adds_session_mode_column(self, tmp_path: Path):
        """Simulate a v3 database and verify migration adds session_mode."""
        import sqlite3

        db_path = tmp_path / "v3.db"
        conn = sqlite3.connect(str(db_path))
        # Create v3 schema (no session_mode column)
        conn.execute(
            """CREATE TABLE sessions_meta (
                session_id TEXT PRIMARY KEY,
                project_name TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                last_modified TEXT DEFAULT '',
                last_node_completed TEXT DEFAULT '',
                session_state TEXT DEFAULT ''
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_info (
                schema_version INT NOT NULL
            )"""
        )
        conn.execute("INSERT INTO schema_info (schema_version) VALUES (3)")
        # Also create team_profiles table (v3 requirement)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS team_profiles (
                team_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                examples_json TEXT DEFAULT '{}',
                updated_at TEXT NOT NULL
            )"""
        )
        # Insert a pre-existing session (should get default mode='planning')
        conn.execute("INSERT INTO sessions_meta (session_id, project_name) VALUES ('old-session', 'OldProject')")
        conn.commit()
        conn.close()

        # Open with SessionStore — should trigger migration
        with SessionStore(db_path) as store:
            # Old session should have default mode
            row = store._conn.execute(
                "SELECT session_mode FROM sessions_meta WHERE session_id = 'old-session'"
            ).fetchone()
            assert row is not None
            assert row[0] == "planning"

            # Should be able to create analysis sessions
            store.create_session("new-ana", mode="analysis")
            result = store.list_analysis_sessions()
            assert len(result) == 1

    def test_existing_sessions_default_to_planning(self, tmp_path: Path):
        """Pre-existing sessions in a v3 DB should default to 'planning' mode."""
        import sqlite3

        db_path = tmp_path / "v3b.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE sessions_meta (
                session_id TEXT PRIMARY KEY,
                project_name TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                last_modified TEXT DEFAULT '',
                last_node_completed TEXT DEFAULT '',
                session_state TEXT DEFAULT ''
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_info (
                schema_version INT NOT NULL
            )"""
        )
        conn.execute("INSERT INTO schema_info (schema_version) VALUES (3)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS team_profiles (
                team_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                examples_json TEXT DEFAULT '{}',
                updated_at TEXT NOT NULL
            )"""
        )
        for i in range(3):
            conn.execute(f"INSERT INTO sessions_meta (session_id, project_name) VALUES ('sess-{i}', 'Proj{i}')")
        conn.commit()
        conn.close()

        with SessionStore(db_path) as store:
            # None should show up as analysis sessions
            analysis = store.list_analysis_sessions()
            assert len(analysis) == 0
