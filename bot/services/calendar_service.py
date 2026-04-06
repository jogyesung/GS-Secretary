"""Google Calendar API 서비스 - 일정 조회, 생성, 수정, 삭제"""

import asyncio
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build
from bot.utils.drive_auth import get_drive_credentials

KST = timezone(timedelta(hours=9))


def _to_kst(dt_str: str) -> str:
    """ISO 8601 시간 문자열을 KST로 변환"""
    if not dt_str or "T" not in dt_str:
        return dt_str  # 종일 일정은 그대로
    try:
        # +09:00, Z 등 다양한 형식 처리
        dt_str_clean = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_str_clean)
        dt_kst = dt.astimezone(KST)
        return dt_kst.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    except Exception:
        return dt_str


def _get_service():
    """Google Calendar API 서비스 객체 생성"""
    creds = get_drive_credentials()
    if not creds or not creds.valid:
        raise RuntimeError("Google 인증이 필요합니다. /drive_auth 명령을 사용하세요.")
    return build("calendar", "v3", credentials=creds)


async def list_calendars() -> list[dict]:
    """사용 가능한 캘린더 목록 조회"""
    loop = asyncio.get_event_loop()

    def _list():
        service = _get_service()
        result = service.calendarList().list().execute()
        calendars = []
        for cal in result.get("items", []):
            calendars.append({
                "id": cal["id"],
                "summary": cal.get("summary", ""),
                "primary": cal.get("primary", False),
            })
        return calendars

    return await loop.run_in_executor(None, _list)


async def list_events(days: int = 1, max_results: int = 20) -> list[dict]:
    """오늘부터 N일간의 일정 조회 (모든 캘린더 통합)"""
    loop = asyncio.get_event_loop()

    def _list():
        service = _get_service()
        now = datetime.utcnow()
        time_min = now.isoformat() + "Z"
        time_max = (now + timedelta(days=days)).isoformat() + "Z"

        # 모든 캘린더 가져오기
        cal_list = service.calendarList().list().execute()
        all_events = []

        for cal in cal_list.get("items", []):
            cal_id = cal["id"]
            cal_name = cal.get("summary", "")
            try:
                events_result = (
                    service.events()
                    .list(
                        calendarId=cal_id,
                        timeMin=time_min,
                        timeMax=time_max,
                        maxResults=max_results,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )
                for event in events_result.get("items", []):
                    start = event["start"].get("dateTime", event["start"].get("date", ""))
                    end = event["end"].get("dateTime", event["end"].get("date", ""))
                    all_events.append({
                        "id": event["id"],
                        "calendar": cal_name,
                        "calendar_id": cal_id,
                        "summary": event.get("summary", "(제목 없음)"),
                        "start": _to_kst(start),
                        "end": _to_kst(end),
                        "location": event.get("location", ""),
                        "description": event.get("description", ""),
                        "status": event.get("status", ""),
                    })
            except Exception:
                continue

        # 시작 시간순 정렬
        all_events.sort(key=lambda e: e["start"])
        return all_events

    return await loop.run_in_executor(None, _list)


async def create_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    all_day: bool = False,
) -> dict:
    """일정 생성

    Args:
        summary: 일정 제목
        start_time: 시작 시간 (ISO 8601 또는 YYYY-MM-DD)
        end_time: 종료 시간 (ISO 8601 또는 YYYY-MM-DD)
        description: 일정 설명
        location: 장소
        all_day: 종일 일정 여부
    """
    loop = asyncio.get_event_loop()

    def _create():
        service = _get_service()

        if all_day:
            event_body = {
                "summary": summary,
                "start": {"date": start_time[:10]},
                "end": {"date": end_time[:10]},
            }
        else:
            # 타임존 추가 (한국 기준)
            start = start_time if "+" in start_time or "Z" in start_time else start_time + "+09:00"
            end = end_time if "+" in end_time or "Z" in end_time else end_time + "+09:00"
            event_body = {
                "summary": summary,
                "start": {"dateTime": start, "timeZone": "Asia/Seoul"},
                "end": {"dateTime": end, "timeZone": "Asia/Seoul"},
            }

        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location

        event = service.events().insert(calendarId="primary", body=event_body).execute()
        return {
            "id": event["id"],
            "summary": event.get("summary", ""),
            "start": event["start"].get("dateTime", event["start"].get("date", "")),
            "link": event.get("htmlLink", ""),
        }

    return await loop.run_in_executor(None, _create)


async def update_event(
    event_id: str,
    summary: str = None,
    start_time: str = None,
    end_time: str = None,
    description: str = None,
    location: str = None,
) -> dict:
    """일정 수정"""
    loop = asyncio.get_event_loop()

    def _update():
        service = _get_service()
        # 기존 일정 가져오기
        event = service.events().get(calendarId="primary", eventId=event_id).execute()

        if summary is not None:
            event["summary"] = summary
        if description is not None:
            event["description"] = description
        if location is not None:
            event["location"] = location
        if start_time is not None:
            start = start_time if "+" in start_time or "Z" in start_time else start_time + "+09:00"
            event["start"] = {"dateTime": start, "timeZone": "Asia/Seoul"}
        if end_time is not None:
            end = end_time if "+" in end_time or "Z" in end_time else end_time + "+09:00"
            event["end"] = {"dateTime": end, "timeZone": "Asia/Seoul"}

        updated = service.events().update(
            calendarId="primary", eventId=event_id, body=event
        ).execute()
        return {
            "id": updated["id"],
            "summary": updated.get("summary", ""),
            "start": updated["start"].get("dateTime", updated["start"].get("date", "")),
        }

    return await loop.run_in_executor(None, _update)


async def delete_event(event_id: str) -> bool:
    """일정 삭제"""
    loop = asyncio.get_event_loop()

    def _delete():
        service = _get_service()
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return True

    return await loop.run_in_executor(None, _delete)
