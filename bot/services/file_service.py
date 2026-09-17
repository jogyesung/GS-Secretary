"""로컬 PC 파일 관리 서비스"""

import os
import re
import shutil
from pathlib import Path
from bot.config import ALLOWED_PATHS


def _is_path_allowed(path: Path) -> bool:
    """경로가 허용된 디렉토리 내에 있는지 확인 (path traversal 방지)"""
    resolved = path.resolve()
    return any(
        resolved == allowed.resolve() or str(resolved).startswith(str(allowed.resolve()) + os.sep)
        for allowed in ALLOWED_PATHS
    )


def list_directory(dir_path: str) -> list[dict]:
    """디렉토리 내용 목록 조회"""
    path = Path(dir_path)
    if not _is_path_allowed(path):
        raise PermissionError(f"접근이 허용되지 않은 경로입니다: {dir_path}")
    if not path.is_dir():
        raise FileNotFoundError(f"디렉토리를 찾을 수 없습니다: {dir_path}")

    items = []
    for item in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        stat = item.stat()
        items.append({
            "name": item.name,
            "path": str(item),
            "is_dir": item.is_dir(),
            "size": stat.st_size if item.is_file() else 0,
            "modified": stat.st_mtime,
        })
    return items


def get_allowed_roots() -> list[dict]:
    """허용된 루트 디렉토리 목록"""
    roots = []
    for p in ALLOWED_PATHS:
        if p.exists():
            roots.append({"name": p.name, "path": str(p)})
    return roots


def get_file_path(file_path: str) -> Path:
    """파일 경로를 검증하고 Path 객체 반환"""
    path = Path(file_path)
    if not _is_path_allowed(path):
        raise PermissionError(f"접근이 허용되지 않은 경로입니다: {file_path}")
    if not path.is_file():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")
    return path


def safe_filename(filename: str | None, default: str = "file") -> str:
    """텔레그램/Drive에서 온 파일명에서 디렉토리 성분 제거 (..\\ 같은 traversal 방지)"""
    # Windows 클라이언트가 보낸 '..\\..\\x' 도 리눅스 서버에서 잘라내야 하므로 양쪽 구분자 모두 처리
    name = re.split(r"[\\/]", str(filename or ""))[-1].strip()
    name = name.replace("\x00", "")
    if name in ("", ".", ".."):
        return default
    return name


def save_file(dest_dir: str, filename: str, content: bytes) -> str:
    """파일을 로컬에 저장 (디렉토리와 최종 경로 모두 허용 범위 검증)"""
    dir_path = Path(dest_dir)
    if not _is_path_allowed(dir_path):
        raise PermissionError(f"접근이 허용되지 않은 경로입니다: {dest_dir}")

    file_path = dir_path / safe_filename(filename)
    if not _is_path_allowed(file_path):
        raise PermissionError(f"접근이 허용되지 않은 경로입니다: {file_path}")

    dir_path.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)
    return str(file_path)


def delete_file(file_path: str) -> bool:
    """파일 삭제"""
    path = Path(file_path)
    if not _is_path_allowed(path):
        raise PermissionError(f"접근이 허용되지 않은 경로입니다: {file_path}")
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def move_file(src: str, dest: str) -> str:
    """파일 이동"""
    src_path = Path(src)
    dest_path = Path(dest)
    if not _is_path_allowed(src_path) or not _is_path_allowed(dest_path):
        raise PermissionError("접근이 허용되지 않은 경로입니다.")
    if not src_path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {src}")

    shutil.move(str(src_path), str(dest_path))
    return str(dest_path)
