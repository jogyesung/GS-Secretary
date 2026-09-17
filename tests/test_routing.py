import pytest

from bot.handlers.chat import needs_heavy_model


@pytest.mark.parametrize("msg", [
    "24년 2월 계약서 찾아줘",
    "프로젝트 알바트로스 표지 슬라이드 만들어줘",
    "이 내용으로 이메일 초안 써줘",
    "내일 회의 일정 잡아줘",
    "드라이브에서 IM 읽고 요약해줘",
    "이거 영어로 번역해줘",
])
def test_heavy_messages_go_to_sonnet(msg):
    assert needs_heavy_model(msg) is True


@pytest.mark.parametrize("msg", [
    "안녕",
    "그럼 화요일로 바꿔줘",
    "취소해줘",
    "오늘 코스피 어때?",
    "30분 뒤에 알려줘",
    "삼성전자 최근 공시 뭐야?",
    "더 자세히",
])
def test_light_messages_go_to_haiku_with_tools(msg):
    assert needs_heavy_model(msg) is False
