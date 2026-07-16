"""저축은행·캐피탈 상장사 사업보고서 + 증권사 리포트 일괄 수집 → Google Drive 저장

DART 전체 상장사 목록에서 회사명 키워드(기본: 저축은행, 캐피탈)로 대상을 골라
각 회사의 최근 정기보고서(사업보고서 우선, 없으면 반기/분기) PDF와
증권사 리포트 PDF를 내려받아 Google Drive에 업로드해요.

비용: 전부 무료 (DART 무료 API + 공개 웹페이지 + Drive API)

준비물:
- .env에 DART_API_KEY (opendart.fss.or.kr에서 무료 발급, 카리나 공시 알림과 공용)
- Google 인증 (credentials.json + data/token.json, 없으면 auth_local.py 1회 실행)

사용법:
  python scripts/collect_sector_filings.py                # 기본: 저축은행, 캐피탈
  python scripts/collect_sector_filings.py 저축은행 캐피탈 카드   # 키워드 직접 지정
  python scripts/collect_sector_filings.py --no-drive     # PC 저장만, Drive 업로드 생략

저장 위치:
- PC: data/filings/YYYY-MM-DD/회사명_보고서명.pdf
- Drive: '사업보고서/YYYY-MM-DD/' 폴더 (자동 생성)
"""

import asyncio
import logging
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from bot.config import BASE_DIR, DART_API_KEY
from bot.services.stock_research_service import (
    KST,
    HEADERS,
    _safe_filename,
    fetch_broker_reports,
    upload_pdfs_to_drive,
)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("collect_sector_filings")

DEFAULT_KEYWORDS = ["저축은행", "캐피탈"]
DRIVE_FOLDER = "사업보고서"
FILINGS_DIR = Path(BASE_DIR) / "data" / "filings"

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
DART_PDF_URL = "https://dart.fss.or.kr/pdf/download/pdf.do?rcp_no={rcept_no}&dcm_no={dcm_no}"

# 정기보고서 종류: 사업보고서 우선, 없으면 반기 → 분기
PERIODIC_REPORT_TYPES = [("A001", "사업보고서"), ("A002", "반기보고서"), ("A003", "분기보고서")]
LOOKBACK_DAYS = 450  # 사업보고서는 연 1회라 15개월 조회

# 뷰어 페이지에서 PDF 다운로드 파라미터 추출: openPdfDownload('rcept_no', 'dcm_no')
_PDF_PARAM_RE = re.compile(r"openPdfDownload\('(\d+)'\s*,\s*'(\d+)'\)")


def get_listed_corps_by_keywords(keywords: list[str]) -> list[dict]:
    """DART 전체 기업 목록에서 이름에 키워드가 포함된 '상장사'만 추출"""
    from bot.services.dart_service import _ensure_corp_code_cache

    cache = asyncio.run(_ensure_corp_code_cache())
    if not cache:
        raise RuntimeError("DART 기업코드 캐시를 받지 못했어요. DART_API_KEY를 확인해주세요.")

    with zipfile.ZipFile(cache) as zf:
        xml_data = zf.read(zf.namelist()[0])
    root = ET.fromstring(xml_data)

    corps = []
    for el in root.iter("list"):
        name = (el.findtext("corp_name") or "").strip()
        stock = (el.findtext("stock_code") or "").strip()
        code = (el.findtext("corp_code") or "").strip()
        if stock and any(kw in name for kw in keywords):
            corps.append({"corp_name": name, "corp_code": code, "stock_code": stock})
    return sorted(corps, key=lambda c: c["corp_name"])


def get_latest_periodic_filing(client: httpx.Client, corp_code: str) -> dict | None:
    """최근 정기보고서 1건 조회 (사업보고서 → 반기 → 분기 순으로 폴백)"""
    now = datetime.now(KST)
    for detail_ty, label in PERIODIC_REPORT_TYPES:
        params = {
            "crtfc_key": DART_API_KEY,
            "corp_code": corp_code,
            "bgn_de": (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d"),
            "end_de": now.strftime("%Y%m%d"),
            "pblntf_detail_ty": detail_ty,
            "page_count": "5",
        }
        try:
            resp = client.get(DART_LIST_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"DART 목록 조회 실패 ({corp_code}/{label}): {e}")
            continue
        if data.get("status") != "000":
            continue
        items = data.get("list", [])
        if items:
            item = items[0]  # 최신순 정렬 기본
            return {
                "rcept_no": item.get("rcept_no", ""),
                "report_nm": item.get("report_nm", label),
                "rcept_dt": item.get("rcept_dt", ""),
                "viewer_url": DART_VIEWER_URL.format(rcept_no=item.get("rcept_no", "")),
            }
    return None


def download_filing_pdf(client: httpx.Client, filing: dict, corp_name: str, dest_dir: Path) -> Path | None:
    """공시 뷰어에서 PDF 다운로드. 실패 시 DART 원본파일(zip)로 폴백."""
    rcept_no = filing["rcept_no"]
    base_name = _safe_filename(f"{corp_name}_{filing['report_nm']}_{filing['rcept_dt']}")

    # 1) 뷰어 페이지에서 dcm_no 추출 → PDF 다운로드
    try:
        page = client.get(filing["viewer_url"])
        page.raise_for_status()
        m = _PDF_PARAM_RE.search(page.text)
        if m:
            pdf_resp = client.get(DART_PDF_URL.format(rcept_no=m.group(1), dcm_no=m.group(2)))
            pdf_resp.raise_for_status()
            if pdf_resp.content[:5] == b"%PDF-":
                path = dest_dir / f"{base_name}.pdf"
                dest_dir.mkdir(parents=True, exist_ok=True)
                path.write_bytes(pdf_resp.content)
                return path
    except Exception as e:
        logger.warning(f"PDF 다운로드 실패 ({corp_name}): {e}")

    # 2) 폴백: DART 공시원본파일 API (XML 원본 zip)
    try:
        resp = client.get(DART_DOCUMENT_URL, params={"crtfc_key": DART_API_KEY, "rcept_no": rcept_no})
        resp.raise_for_status()
        if resp.content[:2] == b"PK":
            path = dest_dir / f"{base_name}_원본.zip"
            dest_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(resp.content)
            return path
        logger.warning(f"원본파일 응답이 zip이 아님 ({corp_name}): {resp.text[:150]}")
    except Exception as e:
        logger.warning(f"원본파일 다운로드 실패 ({corp_name}): {e}")
    return None


def download_broker_report_pdfs(client: httpx.Client, corp: dict, dest_dir: Path) -> list[Path]:
    """해당 종목의 최근 증권사 리포트 PDF 다운로드 (있는 경우만)"""
    saved = []
    try:
        reports = asyncio.run(fetch_broker_reports(corp["stock_code"], days=30, limit=5))
    except Exception as e:
        logger.warning(f"증권사 리포트 조회 실패 ({corp['corp_name']}): {e}")
        return saved

    for r in reports:
        if not r.get("pdf_url"):
            continue
        fname = _safe_filename(f"{corp['corp_name']}_리포트_{r['broker']}_{r['title']}.pdf")
        path = dest_dir / fname
        if path.exists():
            saved.append(path)
            continue
        try:
            resp = client.get(r["pdf_url"])
            resp.raise_for_status()
            dest_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(resp.content)
            saved.append(path)
        except Exception as e:
            logger.warning(f"리포트 PDF 실패 ({fname}): {e}")
    return saved


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keywords = args or DEFAULT_KEYWORDS
    no_drive = "--no-drive" in sys.argv

    if not DART_API_KEY:
        print("⚠️ DART_API_KEY가 없어요. opendart.fss.or.kr에서 무료 발급 후 .env에 넣어주세요.")
        return

    logger.info(f"대상 키워드: {', '.join(keywords)}")
    corps = get_listed_corps_by_keywords(keywords)
    if not corps:
        print("조건에 맞는 상장사를 찾지 못했어요.")
        return
    logger.info(f"상장사 {len(corps)}곳: " + ", ".join(c["corp_name"] for c in corps))

    dest_dir = FILINGS_DIR / datetime.now(KST).strftime("%Y-%m-%d")
    summary = [f"# 저축은행·캐피탈 상장사 자료 수집 ({datetime.now(KST).strftime('%Y-%m-%d %H:%M')})\n"]
    all_paths: list[Path] = []

    with httpx.Client(timeout=60, follow_redirects=True, headers=HEADERS) as client:
        for corp in corps:
            line = f"## {corp['corp_name']} ({corp['stock_code']})"
            logger.info(f"수집 중: {corp['corp_name']}")

            filing = get_latest_periodic_filing(client, corp["corp_code"])
            if filing:
                line += f"\n- 정기보고서: {filing['report_nm']} ({filing['rcept_dt']})\n  {filing['viewer_url']}"
                path = download_filing_pdf(client, filing, corp["corp_name"], dest_dir)
                if path:
                    all_paths.append(path)
                    line += f"\n  → 저장: {path.name}"
                else:
                    line += "\n  → ⚠️ 파일 다운로드 실패 (링크로 확인)"
            else:
                line += "\n- 최근 15개월 내 정기보고서 없음"

            broker_pdfs = download_broker_report_pdfs(client, corp, dest_dir)
            if broker_pdfs:
                all_paths.extend(broker_pdfs)
                line += f"\n- 증권사 리포트 PDF {len(broker_pdfs)}건"
            summary.append(line + "\n")

    # 요약 파일 저장
    dest_dir.mkdir(parents=True, exist_ok=True)
    summary_path = dest_dir / "수집요약.md"
    summary_path.write_text("\n".join(summary), encoding="utf-8")
    all_paths.append(summary_path)

    logger.info(f"PC 저장 완료: {dest_dir} (파일 {len(all_paths)}개)")

    if not no_drive:
        try:
            uploaded = asyncio.run(upload_pdfs_to_drive(all_paths, DRIVE_FOLDER))
            logger.info(f"Drive 업로드 완료: {DRIVE_FOLDER}/{dest_dir.name} ({uploaded}건)")
        except Exception as e:
            logger.warning(f"Drive 업로드 실패 (PC에는 저장됨): {e}")

    print("\n" + "\n".join(summary))


if __name__ == "__main__":
    main()
