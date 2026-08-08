"""Unit tests for AgentSpec registry, AgentRunStore, and run_agent_tracked."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from src.core.agents.spec import AGENT_SPECS, AgentSpec, get_spec
from src.core.agents.tracking import run_agent_tracked
from src.persistence.agent_run_store import AgentRunStore

# ── AgentSpec registry ──────────────────────────────────────────────


class TestAgentSpec:
    def test_all_specs_registered(self):
        expected = {
            "prompt_cleaning", "expressor", "proxy_thinking",
            "memory_analysis", "relationship_analysis",
            "vision_description",
        }
        assert set(AGENT_SPECS.keys()) == expected

    def test_get_spec_returns_frozen(self):
        spec = get_spec("memory_analysis")
        assert spec.name == "memory_analysis"
        assert spec.runner_type == "react"
        assert spec.max_iterations == 4
        assert spec.timeout_seconds == 60
        with pytest.raises(AttributeError):
            spec.name = "changed"

    def test_get_spec_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown agent spec"):
            get_spec("nonexistent")

    def test_all_specs_have_required_fields(self):
        for name, spec in AGENT_SPECS.items():
            assert spec.name == name
            assert spec.purpose
            assert spec.model_role in ("MAIN", "ASSIST")
            assert spec.runner_type in ("simple", "react")
            assert spec.timeout_seconds > 0
            assert spec.max_iterations >= 1


# ── AgentRunStore ───────────────────────────────────────────────────


@pytest.fixture
async def run_store(tmp_path: Path):
    db = str(tmp_path / "test_agent_runs.db")
    store = AgentRunStore(db)
    await store.connect()
    yield store
    await store.close()


class TestAgentRunStore:
    async def test_create_and_get(self, run_store: AgentRunStore):
        await run_store.create_run("run-001", "req-abc", "memory_analysis")
        record = await run_store.get_by_id("run-001")
        assert record is not None
        assert record.run_id == "run-001"
        assert record.agent_name == "memory_analysis"
        assert record.status == "running"
        assert record.finished_at is None

    async def test_finish_run(self, run_store: AgentRunStore):
        await run_store.create_run("run-002", None, "expressor")
        await run_store.finish_run(
            "run-002", status="ok", usage={"total_tokens": 100},
        )
        record = await run_store.get_by_id("run-002")
        assert record.status == "ok"
        assert record.finished_at is not None
        assert record.usage == {"total_tokens": 100}

    async def test_finish_run_with_error(self, run_store: AgentRunStore):
        await run_store.create_run("run-003", "req-xyz", "proxy_thinking")
        await run_store.finish_run("run-003", status="timeout", error="timed out")
        record = await run_store.get_by_id("run-003")
        assert record.status == "timeout"
        assert record.error == "timed out"

    async def test_list_recent_with_filters(self, run_store: AgentRunStore):
        for i in range(5):
            name = "memory_analysis" if i % 2 == 0 else "expressor"
            await run_store.create_run(f"run-{i:03d}", None, name)
            status = "ok" if i < 3 else "failed"
            await run_store.finish_run(f"run-{i:03d}", status=status)

        # filter by agent_name
        mem_runs = await run_store.list_recent(agent_name="memory_analysis")
        assert all(r.agent_name == "memory_analysis" for r in mem_runs)
        assert len(mem_runs) == 3

        # filter by status
        failed = await run_store.list_recent(status="failed")
        assert all(r.status == "failed" for r in failed)
        assert len(failed) == 2

    async def test_count(self, run_store: AgentRunStore):
        await run_store.create_run("r1", None, "a")
        await run_store.create_run("r2", None, "a")
        await run_store.create_run("r3", None, "b")
        assert await run_store.count() == 3
        assert await run_store.count(agent_name="a") == 2
        assert await run_store.count(agent_name="b") == 1

    async def test_get_nonexistent_returns_none(self, run_store: AgentRunStore):
        assert await run_store.get_by_id("no-such-id") is None

    async def test_cleanup(self, run_store: AgentRunStore):
        for i in range(3):
            await run_store.create_run(f"r{i}", None, "x")
        assert await run_store.count() == 3
        deleted = await run_store.cleanup(retention_days=0, max_records=1)
        assert deleted >= 2
        assert await run_store.count() <= 1


# ── run_agent_tracked ───────────────────────────────────────────────


class TestRunAgentTracked:
    async def test_success(self, run_store: AgentRunStore):
        async def fake_agent():
            return "result"

        result = await run_agent_tracked(
            "expressor", fake_agent(), store=run_store,
        )
        assert result == "result"
        record = await run_store.list_recent(limit=1)
        assert len(record) == 1
        assert record[0].status == "ok"

    async def test_timeout(self, run_store: AgentRunStore):
        """Timeout is caught internally and recorded; does not re-raise."""
        async def slow_agent():
            await asyncio.sleep(100)
            return "never"

        # Override timeout to 0.1s so the test is fast
        from src.core.agents import spec as _spec
        original = _spec.AGENT_SPECS["expressor"]
        short = AgentSpec(
            name="expressor", purpose="t", model_role="ASSIST",
            runner_type="simple", timeout_seconds=0.1,
        )
        _spec.AGENT_SPECS["expressor"] = short
        try:
            result = await run_agent_tracked(
                "expressor", slow_agent(), store=run_store,
            )
            assert result is None  # timeout returns None
        finally:
            _spec.AGENT_SPECS["expressor"] = original

        record = await run_store.list_recent(limit=1)
        assert record[0].status == "timeout"

    async def test_failure(self, run_store: AgentRunStore):
        async def failing_agent():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await run_agent_tracked(
                "expressor", failing_agent(), store=run_store,
            )
        record = await run_store.list_recent(limit=1)
        assert record[0].status == "failed"
        assert "boom" in (record[0].error or "")

    async def test_cancellation(self, run_store: AgentRunStore):
        async def cancellable():
            await asyncio.sleep(100)

        # Use a spec with long timeout so the cancel fires before timeout
        coro = cancellable()
        task = asyncio.create_task(
            run_agent_tracked("proxy_thinking", coro, store=run_store)
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        record = await run_store.list_recent(limit=1)
        assert record[0].status == "cancelled"

    async def test_no_store_still_works(self):
        async def fake():
            return 42

        result = await run_agent_tracked("expressor", fake())
        assert result == 42
