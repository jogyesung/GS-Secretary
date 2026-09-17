"""아침 브리핑용 메일 우선순위 정리 - Haiku로 긴급도 상위 5건 추출, 실패 시 원본 순서"""

import logging
import re

from bot.config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

TOP_N = 5

RANK_SYSTEM = (
    "당신은 사모펀드 운용역의 비서예요. 읽지 않은 메일 목록에서 오늘 먼저 봐야 할 것을 골라요.\n"
    "우선순위 기준: 거래 상대방/LP/투자자 연락, 마감·기한이 있는 요청, 미팅 요청, 계약·송금·결제 관련.\n"
    "뉴스레터, 광고, 자동 알림은 뒤로 미뤄요."
)


def extract_sender_name(from_str: str) -> str:
    """이메일 From 헤더에서 발신자 이름만 추출 (없으면 이메일 주소 사용)"""
    if not from_str:
        return "알 수 없음"
    match = re.match(r'^"?([^"<]+)"?\s*<', from_str)
    if match:
        return match.group(1).strip()
    match = re.match(r"<(.+)>", from_str)
    if match:
        return match.group(1)
    return from_str.strip()


def format_raw_lines(emails: list[dict], limit: int = TOP_N) -> list[str]:
    lines = []
    for em in emails[:limit]:
        sender = extract_sender_name(em.get("from", ""))
        subject = em.get("subject", "(제목 없음)")
        lines.append(f"• [{sender}] {subject}")
    return lines


async def rank_unread_emails(emails: list[dict], user_id: int = 0) -> list[str]:
    """긴급도순 상위 5건을 '• [보낸이] 제목 — 한 줄 이유' 형식으로. LLM 불가/실패 시 원본 앞 5건."""
    if not emails:
        return []
    if not ANTHROPIC_API_KEY or len(emails) <= 3:
        return format_raw_lines(emails)

    rows = []
    for i, em in enumerate(emails, 1):
        sender = extract_sender_name(em.get("from", ""))
        subject = (em.get("subject") or "(제목 없음)").strip()
        snippet = (em.get("snippet") or "").strip().replace("\n", " ")[:160]
        rows.append(f"{i}. 보낸이: {sender} | 제목: {subject} | 내용: {snippet}")

    prompt = (
        f"아래 읽지 않은 메일 {len(emails)}건 중 긴급도가 높은 순으로 상위 {TOP_N}건만 골라주세요.\n"
        f"출력 형식은 한 줄에 하나씩 정확히 다음처럼만:\n"
        f"• [보낸이] 제목 — 한 줄 이유\n"
        f"서론, 번호, 마무리 문장 없이 위 형식의 줄만 출력하세요. 제목은 원문 그대로 쓰세요.\n\n"
        + "\n".join(rows)
    )

    try:
        from bot.services.claude_service import simple_completion

        answer = await simple_completion(prompt, system=RANK_SYSTEM, max_tokens=800, user_id=user_id)
    except Exception as e:
        logger.warning(f"메일 우선순위 정리 실패, 원본 순서로 대체: {e}")
        return format_raw_lines(emails)

    lines = []
    for line in (answer or "").splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^[-*•]\s*", "", line)
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if line.startswith("["):
            lines.append(f"• {line}")
        if len(lines) >= TOP_N:
            break
    return lines or format_raw_lines(emails)
