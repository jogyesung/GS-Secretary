"""도구 스키마 ↔ 디스패치 일관성 + 확인 게이트"""

import ast
import inspect
import re

import pytest

from bot.services import claude_service as cs


def _dispatched_tool_names() -> set[str]:
    """_execute_tool 본문의 `tool_name == "..."` 비교에서 처리되는 이름 수집"""
    src = inspect.getsource(cs._execute_tool)
    return set(re.findall(r'tool_name == "([a-z_]+)"', src))


def test_every_registered_tool_has_a_handler():
    tools = cs._build_tools()
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names)), "도구 이름 중복"
    missing = set(names) - _dispatched_tool_names()
    assert not missing, f"핸들러 없는 도구: {missing}"


def test_tool_schemas_are_well_formed():
    for t in cs._build_tools():
        assert t["input_schema"]["type"] == "object"
        props = t["input_schema"].get("properties", {})
        for req in t["input_schema"].get("required", []):
            assert req in props, f"{t['name']}: required '{req}' not in properties"


def test_tool_order_is_deterministic():
    assert [t["name"] for t in cs._build_tools()] == [t["name"] for t in cs._build_tools()]


@pytest.mark.asyncio
async def test_confirm_tools_do_not_execute_without_confirmation(monkeypatch):
    called = {}

    async def fake_send(**kw):
        called["sent"] = True
        return {"id": "x"}

    import bot.services.gmail_service as gm
    monkeypatch.setattr(gm, "send_email", fake_send)

    result = await cs._execute_tool("gmail_send", {"to": "a@b.c", "subject": "s", "body": "b"}, user_id=1)
    assert "[확인 필요]" in result
    assert "sent" not in called
    action = cs.get_pending_action(1)
    assert action and action["tool"] == "gmail_send"

    wrong = cs.pop_pending_action(1, "nope")
    assert wrong is None
    ok = cs.pop_pending_action(1, action["token"])
    assert ok is not None

    out = await cs.execute_confirmed_action(1, ok)
    assert called.get("sent") is True
    assert "전송 완료" in out
    assert cs.get_pending_action(1) is None


def test_system_prompt_has_no_minute_clock():
    """분 단위 시각이 시스템 프롬프트에 들어가면 프롬프트 캐시가 매 분 깨진다"""
    prompt = cs._build_system_prompt()
    assert not re.search(r"현재 시각: \d{2}:\d{2}", prompt)
    assert "오늘:" in prompt
