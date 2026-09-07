"""mood 前置通道集成测试 (T1): 节点注入 / 流式 section / α 预设 / 锚点写路径."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.core.config import _reset_settings, get_relationship_alpha, get_settings
from src.persistence.relationship_store import SqliteRelationshipStore


@pytest.fixture(autouse=True)
def reset_settings():
    _reset_settings()
    yield
    _reset_settings()


class FakePersonaStore:
    """内存版 persona store 桩."""

    def __init__(self) -> None:
        self.mood: dict[str, object] = {
            "valence": 0.0, "cause": None, "updated_at": None,
            "last_interaction_id": None,
        }

    async def get_mood(self, persona_id: str) -> dict[str, object]:
        return dict(self.mood)

    async def set_mood(self, persona_id: str, *, valence: float, cause: str | None,
                       interaction_id: str | None) -> bool:
        self.mood["valence"] = valence
        self.mood["cause"] = cause
        self.mood["updated_at"] = "2026-08-14T00:00:00+00:00"
        self.mood["last_interaction_id"] = interaction_id
        return True


class FakeMemoryStore:
    """内存版 memory store 桩 (锚点加载)."""

    def __init__(self, anchors: list[object] | None = None) -> None:
        self._anchors = anchors or []

    async def list_permanent(self, *a, **k) -> list:
        return []

    async def mark_accessed(self, *a, **k) -> None:
        return None

    async def list_ephemeral_by_subject(self, subject: str, limit: int = 1) -> list:
        return self._anchors[:limit]


class FakeRelationshipStore:
    async def get_relationship(self, *a, **k):
        return None


def _mock_stores(persona_store=None, memory_store=None, rel_store=None) -> dict:
    vector_store = MagicMock()
    vector_store.query = AsyncMock(return_value=[])
    return {
        "multi_forwarder": MagicMock(),
        "resolver": MagicMock(),
        "memory_store": memory_store or FakeMemoryStore(),
        "relationship_store": rel_store or FakeRelationshipStore(),
        "vector_store": vector_store,
        "persona_store": persona_store or FakePersonaStore(),
        "notification_store": None,
        "lorebook_store": None,
        "debug_bus": None,
        "agent_run_store": None,
        "identity_store": None,
        "space_policy_store": None,
    }


# ─── 1. 非流式: _prepare_context mood 注入 ──────────────────────────

async def test_prepare_context_injects_mood_section():
    """情绪分析 + mood 更新后, system 含人格状态段与 cause."""
    from src.core.graph.nodes._main_dialogue import _prepare_context

    stores = _mock_stores()
    state = {
        "source_user": "u1",
        "persona_id": "p1",
        "actor_id": "a1",
        "extracted_new": [{"role": "user", "content": "你这个废物"}],
        "messages": [{"role": "user", "content": "你这个废物"}],
        "current_speaker": "u1",
        "channel_type": "direct",
        "interaction_id": "i-1",
        "persona": "你是一个人格",
        "persona_name": "人格",
        "space_id": None,
        "tools": None,
    }

    emotion_result = {"emotion": "angry", "intensity": 0.9, "valence": -0.8, "summary": "被羞辱"}
    forwarder = MagicMock()
    forwarder.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    with patch("src.core.graph.nodes._compute_emotion", new=AsyncMock(return_value=emotion_result)):
        ctx = await _prepare_context(
            state, None, get_settings(),
            forwarder=forwarder,
            memory_store=stores["memory_store"],
            vector_store=stores["vector_store"],
            stores=stores,
        )

    system_text = ctx["messages"][0]["content"]
    assert "[人格状态]" in system_text
    assert "心情" in system_text
    assert "被羞辱" in system_text  # cause (public 脱敏文本) 注入
    assert ctx["mood_state"] is not None
    assert ctx["mood_state"]["valence"] < 0  # 负面冲击生效


async def test_prepare_context_uses_preinjected_emotion_mood():
    """API 层并行预处理已预注入 emotion_analysis/mood_state → 图内不重算 (不调情绪 LLM)."""
    from src.core.graph.nodes._main_dialogue import _prepare_context

    stores = _mock_stores()
    state = {
        "source_user": "u1",
        "persona_id": "p1",
        "actor_id": "a1",
        "extracted_new": [],
        "messages": [{"role": "user", "content": "hi"}],
        "current_speaker": "u1",
        "channel_type": "direct",
        "persona": "你是一个人格",
        "persona_name": "人格",
        "space_id": None,
        "tools": None,
        "emotion_analysis": {"emotion": "happy", "valence": 0.8, "summary": "收到礼物"},
        "mood_state": {"tier": "good", "cause": "收到礼物", "valence": 0.5},
    }
    with patch("src.core.graph.nodes._compute_emotion", new=AsyncMock()) as mock_emotion:
        ctx = await _prepare_context(
            state, None, get_settings(),
            forwarder=MagicMock(),
            memory_store=stores["memory_store"],
            vector_store=stores["vector_store"],
            stores=stores,
        )
    mock_emotion.assert_not_called()  # 不重复跑情绪 LLM
    system_text = ctx["messages"][0]["content"]
    assert "心情好" in system_text  # 预注入 mood tier → 中文注入
    assert "收到礼物" in system_text


async def test_prepare_context_mood_channel_failure_degrades():
    """情绪分析失败 → 降级: 不阻塞, 无状态段注入 (锚点段仍可注入)."""
    from src.core.graph.nodes._main_dialogue import _prepare_context

    stores = _mock_stores()
    state = {
        "source_user": "u1",
        "persona_id": "p1",
        "actor_id": "a1",
        "extracted_new": [],
        "messages": [{"role": "user", "content": "hi"}],
        "current_speaker": "u1",
        "channel_type": "direct",
        "persona": "你是一个人格",
        "persona_name": "人格",
        "space_id": None,
        "tools": None,
    }
    with patch("src.core.graph.nodes._compute_emotion", side_effect=RuntimeError("上游挂了")):
        ctx = await _prepare_context(
            state, None, get_settings(),
            forwarder=MagicMock(),
            memory_store=stores["memory_store"],
            vector_store=stores["vector_store"],
            stores=stores,
        )
    system_text = ctx["messages"][0]["content"]
    assert "（暂无特别的状态信息）" in system_text  # mood 段回退占位
    assert ctx["mood_state"] is None


# ─── 2. 流式: _build_stream_mood_section ────────────────────────────

async def test_stream_mood_section_includes_matrix_cause_and_anchor():
    """流式状态段 = 矩阵引导 + cause + 锚点."""
    from src.api.routes.forward.stream import _build_stream_mood_section

    anchor = SimpleNamespace(content="被当众羞辱，感到恼火")
    request = SimpleNamespace()  # _state 被 patch
    mood_state = {"tier": "bad", "cause": "被羞辱", "valence": -0.4}
    rel = SimpleNamespace(type="friend")

    with patch("src.api.deps._state", return_value=SimpleNamespace(memory_store=FakeMemoryStore([anchor]))):
        section = await _build_stream_mood_section(
            request, {"actor_id": "a1"}, mood_state, rel,
        )

    assert "[人格状态]" in section
    assert "friend" in section
    assert "心情差" in section
    assert "被羞辱" in section
    assert "对当前发言者的近期情绪" in section


async def test_stream_mood_section_no_anchor_when_actor_missing():
    from src.api.routes.forward.stream import _build_stream_mood_section

    mood_state = {"tier": "decent", "cause": None, "valence": 0.1}
    with patch("src.api.deps._state", return_value=SimpleNamespace(memory_store=FakeMemoryStore())):
        section = await _build_stream_mood_section(
            SimpleNamespace(), {"actor_id": None}, mood_state, SimpleNamespace(type="stranger"),
        )
    assert "对当前发言者的近期情绪" not in section
    assert "[人格状态]" in section


# ─── 3. α 预设应用 (节点级) ─────────────────────────────────────────

def _alpha_state() -> dict:
    return {
        "source_user": "u1",
        "persona_id": "p1",
        "actor_id": "a1",
        "extracted_new": [{"role": "user", "content": "消息"}],
        "current_speaker": "u1",
        "channel_type": "direct",
        "persona_definition": None,
        "space_id": None,
    }


async def test_relationship_node_applies_alpha_down_on_negative(tmp_path):
    """normal 预设 α_down=0.5: raw -0.3 → eff -0.15 落库."""
    from src.core.graph.nodes._relationship_analysis import relationship_analysis_node

    rel_store = SqliteRelationshipStore(str(tmp_path / "mem.db"))
    await rel_store.init_db()
    stores = _mock_stores(memory_store=FakeMemoryStore(), rel_store=rel_store)
    out = SimpleNamespace(favor_delta=-0.3, new_relationship_type=None, notes="被冒犯",
                          mood_anchor=None, reasoning="r")

    with patch("src.core.graph.nodes._relationship_analysis.run_agent_tracked",
              new=AsyncMock(return_value=out)):
        result = await relationship_analysis_node(_alpha_state(), {"configurable": stores})

    rel = await rel_store.get_relationship("p1", "u1")
    assert rel is not None
    assert rel.favor == pytest.approx(-0.15)  # -0.3 × 0.5
    assert result["relationship_delta"]["favor_delta"] == pytest.approx(-0.15)


async def test_relationship_node_uses_persona_alpha_preset(tmp_path):
    """人格引用 guarded (α_down=0.8): raw -0.2 → eff -0.16."""
    from src.core.graph.nodes._relationship_analysis import relationship_analysis_node

    rel_store = SqliteRelationshipStore(str(tmp_path / "mem.db"))
    await rel_store.init_db()
    stores = _mock_stores(memory_store=FakeMemoryStore(), rel_store=rel_store)
    persona_def = SimpleNamespace(relationship_alpha="guarded")
    out = SimpleNamespace(favor_delta=-0.2, new_relationship_type=None, notes="n",
                          mood_anchor=None, reasoning="r")

    state = _alpha_state()
    state["persona_definition"] = persona_def
    with patch("src.core.graph.nodes._relationship_analysis.run_agent_tracked",
              new=AsyncMock(return_value=out)):
        await relationship_analysis_node(state, {"configurable": stores})

    rel = await rel_store.get_relationship("p1", "u1")
    assert rel is not None
    assert rel.favor == pytest.approx(-0.16)  # -0.2 × 0.8


def test_get_relationship_alpha_fallback_normal():
    assert get_relationship_alpha(None).id == "normal"
    assert get_relationship_alpha("bogus").id == "normal"
    assert get_relationship_alpha("sensitive").alpha_up == 0.5
    assert get_relationship_alpha("sensitive").alpha_down == 0.8


def test_load_settings_passes_relationship_alpha(tmp_path, monkeypatch):
    """回归 (v0.4.1): load_settings 曾在最终 Settings 漏传 relationship_alpha,
    config.local.toml 里的自定义预设被解析后静默丢弃."""
    import src.core.config as config_mod

    cfg = tmp_path / "config.local.toml"
    cfg.write_text(
        "[relationship_alpha]\n"
        "[[relationship_alpha.presets]]\n"
        'id = "custom"\n'
        'label = "自定义"\n'
        "alpha_up = 0.3\n"
        "alpha_down = 0.6\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_mod, "LOCAL_CONFIG_PATH", cfg)
    _reset_settings()
    try:
        alphas = config_mod.load_settings().relationship_alpha
        assert alphas["custom"].alpha_up == 0.3
        assert alphas["custom"].alpha_down == 0.6
    finally:
        _reset_settings()


# ─── 4. 锚点写路径触发条件 ──────────────────────────────────────────

async def test_anchor_written_when_strong_negative(tmp_path):
    """favor_delta ≤ -0.15 且 mood_anchor 输出 → EPHEMERAL 记忆落库."""
    from src.core.graph.nodes._relationship_analysis import relationship_analysis_node
    from src.persistence.memory_store import SqliteMemoryStore

    rel_store = SqliteRelationshipStore(str(tmp_path / "mem.db"))
    await rel_store.init_db()
    mem_store = SqliteMemoryStore(str(tmp_path / "mem.db"))
    await mem_store.init_db()
    stores = _mock_stores(memory_store=mem_store, rel_store=rel_store)
    out = SimpleNamespace(favor_delta=-0.3, new_relationship_type="cold", notes="n",
                          mood_anchor="被羞辱，感到恼火", reasoning="r")

    with patch("src.core.graph.nodes._relationship_analysis.run_agent_tracked",
              new=AsyncMock(return_value=out)):
        await relationship_analysis_node(_alpha_state(), {"configurable": stores})

    anchors = await mem_store.list_ephemeral_by_subject("a1")
    assert len(anchors) == 1
    assert anchors[0].content == "被羞辱，感到恼火"


async def test_anchor_skipped_when_mild_negative(tmp_path):
    """favor_delta > -0.15 → 不写锚点."""
    from src.core.graph.nodes._relationship_analysis import relationship_analysis_node
    from src.persistence.memory_store import SqliteMemoryStore

    rel_store = SqliteRelationshipStore(str(tmp_path / "mem.db"))
    await rel_store.init_db()
    mem_store = SqliteMemoryStore(str(tmp_path / "mem.db"))
    await mem_store.init_db()
    stores = _mock_stores(memory_store=mem_store, rel_store=rel_store)
    out = SimpleNamespace(favor_delta=-0.1, new_relationship_type=None, notes="n",
                          mood_anchor="轻微不满", reasoning="r")

    with patch("src.core.graph.nodes._relationship_analysis.run_agent_tracked",
              new=AsyncMock(return_value=out)):
        await relationship_analysis_node(_alpha_state(), {"configurable": stores})

    assert await mem_store.list_ephemeral_by_subject("a1") == []
