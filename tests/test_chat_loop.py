"""chat() 루프: tool_use 왕복 → 최종 텍스트, 히스토리는 텍스트 턴만 남고 캐시 마커가 붙는지"""

from types import SimpleNamespace

import pytest

from bot.services import claude_service as cs


class _Block(SimpleNamespace):
    pass


def _text(t):
    return _Block(type="text", text=t)


def _tool_use(name, inp, id_="tu1"):
    return _Block(type="tool_use", name=name, input=inp, id=id_)


def _resp(content, stop_reason, **usage):
    u = SimpleNamespace(
        input_tokens=usage.get("input", 10), output_tokens=usage.get("output", 5),
        cache_read_input_tokens=usage.get("cache_read", 0),
        cache_creation_input_tokens=usage.get("cache_creation", 0),
    )
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=u)


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


@pytest.fixture(autouse=True)
def _reset_state():
    cs._conversations.clear()
    cs._pending_actions.clear()
    cs._pending_files.clear()
    yield
    cs._conversations.clear()


@pytest.mark.asyncio
async def test_tool_round_trip_collapses_into_text_history(monkeypatch):
    fake = FakeClient([
        _resp([_tool_use("save_memo", {"content": "hello"})], "tool_use"),
        _resp([_text("저장했어요!")], "end_turn", cache_read=1200),
    ])
    monkeypatch.setattr(cs, "_get_client", lambda: fake)

    async def fake_exec(name, inp, user_id, confirmed=False):
        return f"메모 저장 완료 (ID: 7)"
    monkeypatch.setattr(cs, "_execute_tool", fake_exec)

    logged = {}

    async def fake_log(user_id, inp, out, tools, model=None, cache_read=0, cache_creation=0):
        logged.update(dict(inp=inp, out=out, tools=tools, model=model, cache_read=cache_read))
    monkeypatch.setattr(cs, "_log_api_usage", fake_log)

    async def noop(*a, **k):
        return None
    monkeypatch.setattr(cs, "_save_turn_to_db", noop)

    async def empty_hist(user_id):
        return []
    monkeypatch.setattr(cs, "_load_history_from_db", empty_hist)

    out = await cs.chat(42, "메모해줘: hello", heavy=False)
    assert out == "저장했어요!"

    # 두 번 호출: 첫 호출은 user 텍스트, 둘째는 tool_result가 마지막
    assert len(fake.messages.calls) == 2
    first, second = fake.messages.calls
    assert first["model"] == cs.HAIKU_MODEL
    assert "thinking" not in first  # Haiku는 adaptive thinking 없음
    assert first["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert second["messages"][-1]["content"][-1]["type"] == "tool_result"
    assert second["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    # 히스토리는 텍스트 2턴만, 도구 기록은 <tool_log>로 접힘
    hist = cs._conversations[42]
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert all(isinstance(m["content"], str) for m in hist)
    assert "<tool_log>" in hist[1]["content"] and "save_memo" in hist[1]["content"]
    assert "(현재 시각" in hist[0]["content"]

    assert logged["tools"] == 1 and logged["cache_read"] == 1200 and logged["model"] == cs.HAIKU_MODEL


@pytest.mark.asyncio
async def test_heavy_uses_sonnet_with_adaptive_thinking(monkeypatch):
    fake = FakeClient([_resp([_text("ok")], "end_turn")])
    monkeypatch.setattr(cs, "_get_client", lambda: fake)

    async def noop(*a, **k):
        return None
    monkeypatch.setattr(cs, "_save_turn_to_db", noop)
    monkeypatch.setattr(cs, "_log_api_usage", noop)

    async def empty_hist(user_id):
        return []
    monkeypatch.setattr(cs, "_load_history_from_db", empty_hist)

    await cs.chat(7, "계약서 요약해줘", heavy=True)
    call = fake.messages.calls[0]
    assert call["model"] == cs.SONNET_MODEL
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"]["effort"] == cs.CLAUDE_HEAVY_EFFORT


def test_normalize_history_never_starts_with_assistant_or_blocks():
    msgs = [
        {"role": "assistant", "content": "고아 assistant"},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "y"}]},
        {"role": "user", "content": "   "},
        {"role": "user", "content": "진짜 시작"},
        {"role": "assistant", "content": "답"},
    ]
    out = cs._normalize_history(msgs)
    assert out[0] == {"role": "user", "content": "진짜 시작"}
    assert len(out) == 2


def test_normalize_history_trims_to_max_and_keeps_user_first():
    msgs = []
    for i in range(cs.MAX_HISTORY + 5):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"})
    out = cs._normalize_history(msgs)
    assert len(out) <= cs.MAX_HISTORY
    assert out[0]["role"] == "user"


def test_strip_tool_log_removes_leaked_block():
    assert cs._strip_tool_log("답변\n\n<tool_log>\n- x\n</tool_log>") == "답변"
