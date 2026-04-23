"""CIP 브랜드 슬라이드 생성 서비스"""

import logging
from pathlib import Path

from bot.config import BASE_DIR

logger = logging.getLogger(__name__)

DESIGN_DIR = BASE_DIR / "web" / "design"
SLIDES_DIR = DESIGN_DIR / "slides"
ASSETS_DIR = DESIGN_DIR / "assets"

def _css_path() -> str:
    return str(DESIGN_DIR / "colors_and_type.css")

def _asset_path(name: str) -> str:
    return str(ASSETS_DIR / name)

SLIDE_TYPES = {
    "cover": "CoverSlide.html",
    "toc": "TOCSlide.html",
    "divider": "SectionDivider.html",
    "twocol": "TwoColumn.html",
    "chart": "ChartSlide.html",
    "closing": "ClosingSlide.html",
}


def get_slide_template(slide_type: str) -> str | None:
    filename = SLIDE_TYPES.get(slide_type)
    if not filename:
        return None
    path = SLIDES_DIR / filename
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def list_slide_types() -> list[dict]:
    result = []
    labels = {
        "cover": "Cover (표지)",
        "toc": "Table of Contents (목차)",
        "divider": "Section Divider (섹션 구분)",
        "twocol": "Two Column (2단 레이아웃)",
        "chart": "Chart (차트)",
        "closing": "Closing (종료)",
    }
    for key, filename in SLIDE_TYPES.items():
        path = SLIDES_DIR / filename
        result.append({
            "type": key,
            "label": labels.get(key, key),
            "file": filename,
            "exists": path.exists(),
        })
    return result


def generate_cover_slide(
    title: str,
    subtitle: str = "Investment Memorandum",
    date: str = "",
) -> str:
    if not date:
        from datetime import datetime, timezone, timedelta
        kst = timezone(timedelta(hours=9))
        date = datetime.now(kst).strftime("%B %Y")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="{_css_path()}">
<style>
html,body{{margin:0;padding:0;width:1280px;height:720px;overflow:hidden;background:#fff;font-family:var(--font-sans);}}
.slide{{position:relative;width:1280px;height:720px;}}
.logo{{position:absolute;top:54px;left:70px;width:170px;}}
.titleblock{{position:absolute;top:220px;left:70px;width:520px;}}
.titleblock .rule{{width:3px;height:110px;background:#1C3664;position:absolute;left:0;top:0;}}
.titleblock .inner{{padding-left:22px;}}
.eyebrow{{font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#1C3664;font-weight:700;margin-bottom:14px;}}
.title{{font-size:54px;line-height:1.08;color:#1C3664;font-weight:700;letter-spacing:.005em}}
.date{{font-size:14px;color:#797979;letter-spacing:.04em;margin-top:24px;}}
.hero{{position:absolute;bottom:0;left:0;right:0;height:360px;background:url({_asset_path('hero_ifc_seoul.jpeg')}) center/cover;}}
.footer{{position:absolute;bottom:18px;left:70px;right:70px;display:flex;justify-content:space-between;font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:#fff;font-weight:700;z-index:2;}}
.confidential-top{{position:absolute;top:58px;right:70px;font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:#797979;font-weight:700;}}
</style></head>
<body>
<div class="slide">
  <img src="{_asset_path('centroid_logo_horizontal.png')}" class="logo" alt="Centroid">
  <div class="confidential-top">Strictly Confidential</div>
  <div class="titleblock">
    <div class="rule"></div>
    <div class="inner">
      <div class="eyebrow">{_esc(subtitle)}</div>
      <div class="title">{_esc(title)}</div>
      <div class="date">{_esc(date)}</div>
    </div>
  </div>
  <div class="hero"></div>
  <div class="footer"><span>Centroid Investment Partners</span><span>Seoul</span></div>
</div>
</body></html>"""


def generate_twocol_slide(
    section_label: str,
    title: str,
    tagline: str,
    left_title: str,
    left_body: str,
    right_title: str,
    right_body: str,
    page_num: int = 1,
) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="{_css_path()}">
<style>
html,body{{margin:0;padding:0;width:1280px;height:720px;overflow:hidden;background:#fff;font-family:var(--font-sans);}}
.slide{{position:relative;width:1280px;height:720px;padding:70px;box-sizing:border-box}}
.eyebrow{{font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:#797979;font-weight:700}}
.title{{font-size:32px;font-weight:700;color:#1C3664;margin-top:6px;border-left:3px solid #1C3664;padding-left:16px;line-height:1.1}}
.tagline{{font-size:14px;color:#797979;margin-top:12px;}}
.grid{{margin-top:44px;display:grid;grid-template-columns:1fr 1fr;gap:32px}}
.col h3{{font-size:16px;color:#1C3664;margin:0 0 12px;font-weight:700}}
.col p{{font-size:14px;line-height:1.55;color:#4D4D4D;margin:0 0 14px}}
.footer{{position:absolute;left:70px;right:70px;bottom:26px;display:flex;justify-content:space-between;font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:#797979;font-weight:700;border-top:1px solid #D2D2D2;padding-top:12px;}}
.pg{{width:22px;height:22px;border-radius:999px;background:#797979;color:#fff;display:inline-flex;align-items:center;justify-content:center;font-size:10px;letter-spacing:0;}}
</style></head>
<body>
<div class="slide">
  <div class="eyebrow">{_esc(section_label)}</div>
  <div class="title">{_esc(title)}</div>
  <div class="tagline">{_esc(tagline)}</div>
  <div class="grid">
    <div class="col">
      <h3>{_esc(left_title)}</h3>
      <p>{_esc(left_body)}</p>
    </div>
    <div class="col">
      <h3>{_esc(right_title)}</h3>
      <p>{_esc(right_body)}</p>
    </div>
  </div>
  <div class="footer"><span>Strictly Confidential</span><span class="pg">{page_num}</span></div>
</div>
</body></html>"""


def generate_section_divider(
    number: str,
    title: str,
    page_num: int = 1,
) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="{_css_path()}">
<style>
html,body{{margin:0;padding:0;width:1280px;height:720px;overflow:hidden;font-family:var(--font-sans);}}
.slide{{position:relative;width:1280px;height:720px;background:url({_asset_path('hero_ifc_seoul.jpeg')}) center/cover;}}
.slide::after{{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(28,54,100,.05) 0%, rgba(28,54,100,.6) 100%);}}
.content{{position:absolute;inset:0;z-index:2;padding:70px;display:flex;align-items:center;gap:30px;}}
.num{{font-size:220px;font-weight:700;color:#fff;line-height:1;letter-spacing:-.02em;text-shadow:0 2px 8px rgba(0,0,0,.2);}}
.block{{border-left:3px solid #fff;padding-left:22px;color:#fff;}}
.eyebrow{{font-size:12px;letter-spacing:.2em;text-transform:uppercase;opacity:.85;font-weight:700;margin-bottom:14px;}}
.title{{font-size:60px;font-weight:700;line-height:1.05;}}
.footer{{position:absolute;left:70px;right:70px;bottom:26px;display:flex;justify-content:space-between;font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:#fff;font-weight:700;z-index:3;}}
.pg{{width:22px;height:22px;border-radius:999px;background:rgba(255,255,255,.25);color:#fff;display:inline-flex;align-items:center;justify-content:center;font-size:10px;letter-spacing:0;}}
.logo{{position:absolute;top:54px;left:70px;z-index:3;height:28px;filter:brightness(0) invert(1);}}
</style></head>
<body>
<div class="slide">
  <img src="{_asset_path('centroid_logo_horizontal.png')}" class="logo" alt="">
  <div class="content">
    <div class="num">{_esc(number)}</div>
    <div class="block">
      <div class="eyebrow">Section</div>
      <div class="title">{_esc(title)}</div>
    </div>
  </div>
  <div class="footer"><span>Strictly Confidential</span><span class="pg">{page_num}</span></div>
</div>
</body></html>"""


def generate_closing_slide() -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="{_css_path()}">
<style>
html,body{{margin:0;padding:0;width:1280px;height:720px;overflow:hidden;background:#fff;font-family:var(--font-sans);}}
.slide{{position:relative;width:1280px;height:720px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:22px;}}
.line{{width:60px;height:2px;background:#1C3664}}
.t{{font-size:44px;color:#1C3664;font-weight:700;letter-spacing:.02em;}}
.s{{font-size:12px;color:#797979;letter-spacing:.22em;text-transform:uppercase;font-weight:700}}
.logo{{position:absolute;top:54px;left:70px;height:28px;}}
.footer{{position:absolute;left:70px;right:70px;bottom:26px;display:flex;justify-content:space-between;font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:#797979;font-weight:700;border-top:1px solid #D2D2D2;padding-top:12px;}}
</style></head>
<body>
<div class="slide">
  <img src="{_asset_path('centroid_logo_horizontal.png')}" class="logo" alt="">
  <div class="s">End of Document</div>
  <div class="line"></div>
  <div class="t">Thank you</div>
  <div class="footer"><span>Strictly Confidential</span><span>Centroid Investment Partners · Seoul</span></div>
</div>
</body></html>"""


def save_generated_slide(html: str, filename: str) -> Path:
    output_dir = BASE_DIR / "data" / "slides"
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / filename
    dest.write_text(html, encoding="utf-8")
    return dest


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
