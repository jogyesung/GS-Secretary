"""Google Drive API 서비스 - 파일 목록, 업로드, 다운로드, 검색 등"""

import asyncio
import io
from pathlib import Path
from functools import partial
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from bot.utils.drive_auth import get_drive_credentials


def _get_service():
    """Google Drive API 서비스 객체 생성"""
    creds = get_drive_credentials()
    if not creds or not creds.valid:
        raise RuntimeError("Google Drive 인증이 필요합니다. /drive_auth 명령을 사용하세요.")
    return build("drive", "v3", credentials=creds)


async def list_files(folder_id: str = "root", page_size: int = 20) -> list[dict]:
    """폴더 내 파일/폴더 목록 조회"""
    loop = asyncio.get_event_loop()

    def _list():
        service = _get_service()
        query = f"'{folder_id}' in parents and trashed = false"
        results = (
            service.files()
            .list(
                q=query,
                pageSize=page_size,
                fields="files(id, name, mimeType, size, modifiedTime)",
                orderBy="folder,name",
            )
            .execute()
        )
        return results.get("files", [])

    return await loop.run_in_executor(None, _list)


async def search_files(query_text: str, page_size: int = 20) -> list[dict]:
    """파일 검색"""
    loop = asyncio.get_event_loop()

    def _search():
        service = _get_service()
        query = f"name contains '{query_text}' and trashed = false"
        results = (
            service.files()
            .list(
                q=query,
                pageSize=page_size,
                fields="files(id, name, mimeType, size, modifiedTime)",
            )
            .execute()
        )
        return results.get("files", [])

    return await loop.run_in_executor(None, _search)


async def upload_file(file_path: str, folder_id: str = "root", file_name: str = None) -> dict:
    """파일 업로드"""
    loop = asyncio.get_event_loop()

    def _upload():
        service = _get_service()
        name = file_name or Path(file_path).name
        file_metadata = {"name": name, "parents": [folder_id]}
        media = MediaFileUpload(file_path, resumable=True)
        file = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id, name, webViewLink")
            .execute()
        )
        return file

    return await loop.run_in_executor(None, _upload)


async def download_file(file_id: str, dest_path: str) -> str:
    """파일 다운로드"""
    loop = asyncio.get_event_loop()

    def _download():
        service = _get_service()
        request = service.files().get_media(fileId=file_id)
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return dest_path

    return await loop.run_in_executor(None, _download)


async def create_folder(name: str, parent_id: str = "root") -> dict:
    """폴더 생성"""
    loop = asyncio.get_event_loop()

    def _create():
        service = _get_service()
        file_metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = service.files().create(body=file_metadata, fields="id, name").execute()
        return folder

    return await loop.run_in_executor(None, _create)


async def delete_file(file_id: str) -> bool:
    """파일/폴더 삭제 (휴지통으로 이동)"""
    loop = asyncio.get_event_loop()

    def _delete():
        service = _get_service()
        service.files().update(fileId=file_id, body={"trashed": True}).execute()
        return True

    return await loop.run_in_executor(None, _delete)


async def get_share_link(file_id: str) -> str:
    """공유 링크 생성"""
    loop = asyncio.get_event_loop()

    def _share():
        service = _get_service()
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
        file = service.files().get(fileId=file_id, fields="webViewLink").execute()
        return file.get("webViewLink", "")

    return await loop.run_in_executor(None, _share)


async def get_file_info(file_id: str) -> dict:
    """파일 정보 조회"""
    loop = asyncio.get_event_loop()

    def _info():
        service = _get_service()
        return (
            service.files()
            .get(fileId=file_id, fields="id, name, mimeType, size, modifiedTime, webViewLink")
            .execute()
        )

    return await loop.run_in_executor(None, _info)
