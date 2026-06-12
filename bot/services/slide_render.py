"""슬라이드 HTML → PNG 렌더링 (Playwright). 미설치 시 None 반환하여 HTML 폴백."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def render_html_to_png(html_path: str) -> str | None:
    """HTML 슬라이드를 1280x720 PNG로 렌더링. 실패 시 None (HTML 전송으로 폴백)."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.debug("playwright 미설치 - 슬라이드를 HTML로 전송합니다")
        return None

    try:
        png_path = str(Path(html_path).with_suffix(".png"))
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.goto(f"file://{Path(html_path).resolve()}")
            # 폰트/이미지 로딩 대기
            await page.wait_for_timeout(500)
            await page.screenshot(path=png_path)
            await browser.close()
        logger.info(f"슬라이드 PNG 렌더링 완료: {png_path}")
        return png_path
    except Exception as e:
        logger.warning(f"슬라이드 PNG 렌더링 실패 (HTML로 폴백): {e}")
        return None
