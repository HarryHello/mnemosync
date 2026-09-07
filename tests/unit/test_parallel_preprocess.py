"""并行预处理单元测试 (技术债 T1): _run_parallel_preprocess 三路 gather.

覆盖: 清洗 ∥ 情绪+mood ∥ Vision 的并发调用、主候选一次解析复用、
图片支持时跳过转写、情绪通道降级传播、异常传播语义.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.api.routes.forward.dispatch import _run_parallel_preprocess
from src.core.constants import DEFAULT_PERSONA_ID
from src.core.models.resolver import ModelType
from src.infra.llm_service.models import ResolvedCandidate


def _candidate(*, modalities: list[str] | None = None) -> ResolvedCandidate:
    cand = ResolvedCandidate(
        role=ModelType.MAIN,
        priority=1,
        service_id="svc",
        base_url="http://upstream",
        api_key="k",
        model="m",
    )
    if modalities is not None:
        cand.input_modalities = modalities
    return cand


def _request() -> MagicMock:
    req = MagicMock()
    req.state = SimpleNamespace()
    return req


def _system_msg(text: str) -> object:
    return SimpleNamespace(role="system", content=text)


EMOTION = {"valence": -0.6, "category": "anger", "summary": "被当众批评"}
MOOD = {"valence": -0.3, "tier": "low", "cause": "被当众批评", "changed": True}


class TestRunParallelPreprocess:
    """_run_parallel_preprocess 行为契约."""

    async def test_happy_path_three_way_parallel(self) -> None:
        """三路并行全部执行: 清洗+情绪+mood+Vision+主候选一次解析."""
        http_request = _request()
        cand = _candidate(modalities=["text"])
        persona_store = SimpleNamespace()

        with (
            patch(
                "src.api.routes.forward._accessors._get_multi_forwarder", return_value=AsyncMock()
            ),
            patch("src.api.routes.forward.identity._resolve_main_candidate", return_value=cand),
            patch(
                "src.api.routes.forward.dispatch._prepare_prompt",
                return_value=("persona+clean", {"clean_prompt": "keep"}),
            ),
            patch("src.core.memory.mood.run_emotion_mood_channel", return_value=(EMOTION, MOOD)),
            patch(
                "src.api.routes.forward.stream._describe_images_if_needed",
                return_value="【图片：一只猫】",
            ),
            patch("src.api.deps._state", return_value=SimpleNamespace(persona_store=persona_store)),
        ):
            persona2, cleaning, emotion, mood, vision, main_cand = await _run_parallel_preprocess(
                request_messages=[_system_msg("角色设定…")],
                persona="persona",
                http_request=http_request,
                source_frontend="test",
                messages_dict=[
                    {"role": "user", "content": "你看这张图"},
                    {"role": "user", "content": "最后一句"},
                ],
                interaction_id="i-1",
            )

        assert persona2 == "persona+clean"
        assert cleaning == {"clean_prompt": "keep"}
        assert emotion == EMOTION
        assert mood == MOOD
        assert vision == "【图片：一只猫】"
        assert main_cand is cand

    async def test_emotion_channel_receives_last_user_message(self) -> None:
        """情绪通道输入 = 最后一条 user 消息 (extracted_new 等价物)."""
        http_request = _request()
        cand = _candidate()
        emotion_mock = AsyncMock(return_value=(EMOTION, MOOD))

        with (
            patch(
                "src.api.routes.forward._accessors._get_multi_forwarder", return_value=AsyncMock()
            ),
            patch("src.api.routes.forward.identity._resolve_main_candidate", return_value=cand),
            patch("src.api.routes.forward.dispatch._prepare_prompt", return_value=("p", None)),
            patch("src.core.memory.mood.run_emotion_mood_channel", emotion_mock),
            patch("src.api.routes.forward.stream._describe_images_if_needed", return_value=""),
            patch("src.api.deps._state", return_value=SimpleNamespace(persona_store=None)),
        ):
            await _run_parallel_preprocess(
                request_messages=[],
                persona="p",
                http_request=http_request,
                source_frontend=None,
                messages_dict=[
                    {"role": "user", "content": "你好"},
                    {"role": "user", "content": "这是最后一句"},
                ],
                interaction_id="i-2",
            )

        args, kwargs = emotion_mock.await_args
        assert args[2] == DEFAULT_PERSONA_ID  # persona_id 是第 3 个位置参数
        assert kwargs["interaction_id"] == "i-2"
        assert kwargs["extracted"] == [{"role": "user", "content": "这是最后一句"}]

    async def test_model_supports_images_skips_vision(self) -> None:
        """目标模型支持图片 → 不调 Vision 转写 (图片直接透传), vision 为空."""
        http_request = _request()
        cand = _candidate(modalities=["text", "image"])
        vision_mock = AsyncMock(return_value="不应被调用")

        with (
            patch(
                "src.api.routes.forward._accessors._get_multi_forwarder", return_value=AsyncMock()
            ),
            patch("src.api.routes.forward.identity._resolve_main_candidate", return_value=cand),
            patch("src.api.routes.forward.dispatch._prepare_prompt", return_value=("p", None)),
            patch("src.core.memory.mood.run_emotion_mood_channel", return_value=(EMOTION, MOOD)),
            patch("src.api.routes.forward.stream._describe_images_if_needed", vision_mock),
            patch("src.api.deps._state", return_value=SimpleNamespace(persona_store=None)),
        ):
            _, _, _, _, vision, main_cand = await _run_parallel_preprocess(
                request_messages=[],
                persona="p",
                http_request=http_request,
                source_frontend=None,
                messages_dict=[{"role": "user", "content": "看图"}],
                interaction_id=None,
            )

        vision_mock.assert_not_awaited()
        assert vision == ""
        assert main_cand is cand

    async def test_no_candidate_still_runs_parallel(self) -> None:
        """主候选解析失败 (None) 不阻塞并行: 视作不支持图片, Vision 照常."""
        http_request = _request()
        vision_mock = AsyncMock(return_value="描述")

        with (
            patch(
                "src.api.routes.forward._accessors._get_multi_forwarder", return_value=AsyncMock()
            ),
            patch("src.api.routes.forward.identity._resolve_main_candidate", return_value=None),
            patch("src.api.routes.forward.dispatch._prepare_prompt", return_value=("p", None)),
            patch("src.core.memory.mood.run_emotion_mood_channel", return_value=({}, None)),
            patch("src.api.routes.forward.stream._describe_images_if_needed", vision_mock),
            patch("src.api.deps._state", return_value=SimpleNamespace(persona_store=None)),
        ):
            _, cleaning, emotion, mood, vision, main_cand = await _run_parallel_preprocess(
                request_messages=[],
                persona="p",
                http_request=http_request,
                source_frontend=None,
                messages_dict=[],
                interaction_id=None,
            )

        vision_mock.assert_awaited_once()
        assert vision == "描述"
        assert cleaning is None
        assert emotion == {}
        assert mood is None
        assert main_cand is None

    async def test_emotion_degrade_propagates_empty(self) -> None:
        """情绪通道降级语义: 返回空 dict/None 时其余结果不受影响."""
        http_request = _request()

        with (
            patch(
                "src.api.routes.forward._accessors._get_multi_forwarder", return_value=AsyncMock()
            ),
            patch(
                "src.api.routes.forward.identity._resolve_main_candidate", return_value=_candidate()
            ),
            patch(
                "src.api.routes.forward.dispatch._prepare_prompt",
                return_value=("p2", {"clean_prompt": "x"}),
            ),
            patch("src.core.memory.mood.run_emotion_mood_channel", return_value=({}, None)),
            patch("src.api.routes.forward.stream._describe_images_if_needed", return_value=""),
            patch("src.api.deps._state", return_value=SimpleNamespace(persona_store=None)),
        ):
            persona2, cleaning, emotion, mood, _, _ = await _run_parallel_preprocess(
                request_messages=[],
                persona="p",
                http_request=http_request,
                source_frontend=None,
                messages_dict=[],
                interaction_id=None,
            )

        assert persona2 == "p2"
        assert cleaning == {"clean_prompt": "x"}
        assert emotion == {}
        assert mood is None

    async def test_unexpected_channel_error_propagates(self) -> None:
        """非预期异常 (gather 默认语义): 直接上抛, 不静默吞掉."""
        http_request = _request()

        with (
            patch(
                "src.api.routes.forward._accessors._get_multi_forwarder", return_value=AsyncMock()
            ),
            patch(
                "src.api.routes.forward.identity._resolve_main_candidate", return_value=_candidate()
            ),
            patch(
                "src.api.routes.forward.dispatch._prepare_prompt",
                side_effect=RuntimeError("clean boom"),
            ),
            patch("src.core.memory.mood.run_emotion_mood_channel", return_value=(EMOTION, MOOD)),
            patch("src.api.routes.forward.stream._describe_images_if_needed", return_value=""),
            patch("src.api.deps._state", return_value=SimpleNamespace(persona_store=None)),
        ):
            with pytest.raises(RuntimeError, match="clean boom"):
                await _run_parallel_preprocess(
                    request_messages=[],
                    persona="p",
                    http_request=http_request,
                    source_frontend=None,
                    messages_dict=[],
                    interaction_id=None,
                )
