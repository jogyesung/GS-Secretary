"""Gmail API 서비스 - 이메일 목록, 읽기, 전송, 검색"""

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from googleapiclient.discovery import build
from bot.utils.drive_auth import get_drive_credentials

KST = timezone(timedelta(hours=9))


def _get_service():
    """Gmail API 서비스 객체 생성"""
    creds = get_drive_credentials()
    if not creds or not creds.valid:
        raise RuntimeError("Google 인증이 필요합니다. /drive_auth 명령을 사용하세요.")
    return build("gmail", "v1", credentials=creds)


def _parse_date_to_kst(date_str: str) -> str:
    """이메일 날짜 문자열을 KST로 변환"""
    if not date_str:
        return ""
    try:
        # RFC 2822 형식 파싱
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        dt_kst = dt.astimezone(KST)
        return dt_kst.strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        return date_str


def _get_header(headers: list[dict], name: str) -> str:
    """헤더 목록에서 특정 헤더 값 추출"""
    for header in headers:
        if header["name"].lower() == name.lower():
            return header["value"]
    return ""


def _decode_body(data: str) -> str:
    """base64url 인코딩된 이메일 본문 디코딩"""
    if not data:
        return ""
    try:
        decoded = base64.urlsafe_b64decode(data)
        return decoded.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_body(payload: dict) -> str:
    """이메일 payload에서 본문 텍스트 추출 (multipart 처리)"""
    mime_type = payload.get("mimeType", "")

    # 단일 파트
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return _decode_body(data)

    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        return _decode_body(data)

    # multipart 처리
    parts = payload.get("parts", [])
    text_content = ""
    html_content = ""

    for part in parts:
        part_mime = part.get("mimeType", "")
        if part_mime == "text/plain":
            data = part.get("body", {}).get("data", "")
            text_content = _decode_body(data)
        elif part_mime == "text/html":
            data = part.get("body", {}).get("data", "")
            html_content = _decode_body(data)
        elif part_mime.startswith("multipart/"):
            # 재귀적으로 중첩된 multipart 처리
            nested = _extract_body(part)
            if nested:
                text_content = text_content or nested

    # text/plain 우선, 없으면 html
    return text_content if text_content else html_content


async def list_emails(max_results: int = 10, query: str = "is:unread") -> list[dict]:
    """이메일 목록 조회"""
    loop = asyncio.get_event_loop()

    def _list():
        service = _get_service()
        results = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        messages = results.get("messages", [])
        if not messages:
            return []

        email_list = []
        for msg_info in messages:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_info["id"], format="metadata",
                     metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            headers = msg.get("payload", {}).get("headers", [])
            email_list.append({
                "id": msg["id"],
                "thread_id": msg.get("threadId", ""),
                "subject": _get_header(headers, "Subject") or "(제목 없음)",
                "from": _get_header(headers, "From"),
                "date": _parse_date_to_kst(_get_header(headers, "Date")),
                "snippet": msg.get("snippet", ""),
                "label_ids": msg.get("labelIds", []),
            })

        return email_list

    return await loop.run_in_executor(None, _list)


async def read_email(message_id: str) -> dict:
    """이메일 전체 내용 읽기"""
    loop = asyncio.get_event_loop()

    def _read():
        service = _get_service()
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        headers = msg.get("payload", {}).get("headers", [])
        body = _extract_body(msg.get("payload", {}))

        return {
            "id": msg["id"],
            "thread_id": msg.get("threadId", ""),
            "subject": _get_header(headers, "Subject") or "(제목 없음)",
            "from": _get_header(headers, "From"),
            "to": _get_header(headers, "To"),
            "date": _parse_date_to_kst(_get_header(headers, "Date")),
            "body": body,
            "snippet": msg.get("snippet", ""),
            "label_ids": msg.get("labelIds", []),
        }

    return await loop.run_in_executor(None, _read)


async def send_email(to: str, subject: str, body: str) -> dict:
    """이메일 전송"""
    loop = asyncio.get_event_loop()

    def _send():
        service = _get_service()
        message = MIMEText(body, "plain", "utf-8")
        message["to"] = to
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        return {
            "id": sent["id"],
            "thread_id": sent.get("threadId", ""),
            "label_ids": sent.get("labelIds", []),
        }

    return await loop.run_in_executor(None, _send)


async def search_emails(query: str, max_results: int = 10) -> list[dict]:
    """Gmail 검색 쿼리로 이메일 검색"""
    return await list_emails(max_results=max_results, query=query)
