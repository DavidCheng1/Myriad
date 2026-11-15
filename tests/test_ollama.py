"""Phase 9: Ollama adapter, model configuration and provider errors - no real model needed."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from myriad.config import ConfigError, build_provider, load_model_config
from myriad.engine.task_engine import TaskEngine
from myriad.interface.session import ChatSession
from myriad.llm.base import ProviderError
from myriad.llm.ollama import OllamaProvider, decision_schema
from myriad.models import ChatMessage, FailureReason, TaskStatus

from conftest import AGENTS_DIR, ANSWER_DONE

CONFIG_PATH = AGENTS_DIR.parent / "config" / "models.yaml"
MESSAGES = [ChatMessage("system", "sys"), ChatMessage("user", "Hi", origin="user")]


class FakeTransport:
    """Stands in for the HTTP call: returns queued responses or raises queued errors."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict, float]] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> dict:
        self.requests.append((url, payload, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response  # type: ignore[return-value]


def ok(text: str = ANSWER_DONE, **extra: object) -> dict:
    return {"message": {"role": "assistant", "content": text}, "done": True, "done_reason": "stop",
            "prompt_eval_count": 120, "eval_count": 15, **extra}


def http_error(code: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/api/chat", code, "error", {}, io.BytesIO(json.dumps(body).encode()))


# --- adapter ----------------------------------------------------------------


def test_request_format() -> None:
    transport = FakeTransport(ok())
    OllamaProvider("qwen3:8b", context_window=8192, timeout=30, transport=transport).complete(
        MESSAGES, 400, temperature=0.3
    )
    url, payload, timeout = transport.requests[0]
    assert url == "http://127.0.0.1:11434/api/chat" and timeout == 30
    assert payload["model"] == "qwen3:8b" and payload["stream"] is False and payload["think"] is False
    assert payload["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "Hi"}]
    assert payload["options"] == {"num_ctx": 8192, "num_predict": 400, "temperature": 0.3}
    assert payload["format"] == decision_schema()


def test_schema_uses_any_of_without_discriminator() -> None:
    text = json.dumps(decision_schema())
    assert '"anyOf"' in text and '"oneOf"' not in text and '"discriminator"' not in text
    assert all(kind in text for kind in ("answer", "ask_user", "delegate", "tool_call"))


def test_real_token_counts() -> None:
    completion = OllamaProvider("m", transport=FakeTransport(ok())).complete(MESSAGES, 400)
    assert (completion.input_tokens, completion.output_tokens) == (120, 15)
    assert not (completion.estimated or completion.simulated or completion.truncated)


def test_missing_counts_fall_back_to_estimates() -> None:
    data = ok()
    del data["prompt_eval_count"]
    assert OllamaProvider("m", transport=FakeTransport(data)).complete(MESSAGES, 400).estimated


def test_length_stop_marks_truncation() -> None:
    transport = FakeTransport(ok(done_reason="length"))
    assert OllamaProvider("m", transport=transport).complete(MESSAGES, 400).truncated


@pytest.mark.parametrize(("error", "fragment"), [
    (urllib.error.URLError(ConnectionRefusedError(10061, "refused")), "not reachable"),
    (TimeoutError("timed out"), "did not answer within"),
    (urllib.error.URLError(TimeoutError("timed out")), "did not answer within"),
    (http_error(404, {"error": 'model "qwen3:8b" not found, try pulling it first'}), "ollama pull qwen3:8b"),
    (http_error(500, {"error": "llama runner process has terminated"}), "HTTP 500"),
])
def test_transport_errors_become_provider_errors(error: Exception, fragment: str) -> None:
    with pytest.raises(ProviderError) as info:
        OllamaProvider("qwen3:8b", transport=FakeTransport(error)).complete(MESSAGES, 10)
    assert fragment in str(info.value)


def test_unexpected_response_is_a_provider_error() -> None:
    with pytest.raises(ProviderError, match="unexpected response"):
        OllamaProvider("m", transport=FakeTransport({"error": "?"})).complete(MESSAGES, 10)


# --- through the engine -----------------------------------------------------


def test_provider_error_fails_the_task_in_a_controlled_way(atlas) -> None:
    refused = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
    session = ChatSession(TaskEngine({"atlas": atlas}, OllamaProvider("m", transport=FakeTransport(refused))))
    reply = session.send("Hello")
    assert reply.task.failure_reason is FailureReason.PROVIDER_ERROR
    assert "not reachable" in reply.text and session.engine.tracker.records == ()
    assert not reply.simulated


def test_truncated_answer_goes_through_the_correction_path(atlas) -> None:
    transport = FakeTransport(ok('{"type": "answer", "outc', done_reason="length"), ok())
    session = ChatSession(TaskEngine({"atlas": atlas}, OllamaProvider("m", transport=transport)))
    reply = session.send("Hello")
    assert reply.task.status is TaskStatus.DONE and reply.task.corrections == 1
    assert "max_output_tokens" in transport.requests[1][1]["messages"][-1]["content"]


def test_real_counts_are_booked(atlas) -> None:
    session = ChatSession(TaskEngine({"atlas": atlas}, OllamaProvider("m", transport=FakeTransport(ok()))))
    reply = session.send("Hello")
    assert reply.tree_tokens == 135 and not reply.estimated


# --- configuration ----------------------------------------------------------


def test_model_config_maps_the_reasoning_tier(atlas, sage) -> None:
    config = load_model_config(CONFIG_PATH)
    assert config.default_provider == "mock"
    provider = build_provider("ollama", config, {"atlas": atlas, "sage": sage})
    assert isinstance(provider, OllamaProvider)
    assert (provider.model, provider.context_window, provider.timeout) == ("qwen3:8b", 8192, 180)
    assert build_provider("mock", config, {"atlas": atlas}).simulated


def test_mixed_tiers_are_rejected(atlas, sage) -> None:
    fast_sage = sage.model_copy(update={"llm": sage.llm.model_copy(update={"tier": "fast"})})
    with pytest.raises(ConfigError, match="share one llm.tier"):
        build_provider("ollama", load_model_config(CONFIG_PATH), {"atlas": atlas, "sage": fast_sage})


def test_invalid_config_is_reported(tmp_path) -> None:
    bad = tmp_path / "models.yaml"
    bad.write_text("tiers: {reasoning: {model: x}}", encoding="utf-8")
    with pytest.raises(ConfigError, match="context_window"):
        load_model_config(bad)
