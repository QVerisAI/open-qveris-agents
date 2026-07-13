"""Convert local HTML reports to PDF using a local Chromium browser."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

# 硬编码可信绝对路径：这些位置的浏览器直接信任（无需再过名字白名单）
BROWSER_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/google-chrome-stable"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
    Path("/opt/google/chrome/chrome"),
]

# shutil.which 回退结果必须：落在可信根内 + basename 命中白名单，两者同时满足才放行
_TRUSTED_BROWSER_ROOTS = (
    Path(r"C:\Program Files"),
    Path(r"C:\Program Files (x86)"),
    Path("/Applications"),
    Path("/opt"),
    Path("/usr"),
)
_ALLOWED_BROWSER_NAME = re.compile(
    r"^(google-chrome(-stable)?|chromium(-browser)?|chrome|msedge|microsoft-edge)(\.exe)?$",
    re.IGNORECASE,
)


def _under_trusted_root(path: Path) -> bool:
    try:
        rp = path.resolve()
    except OSError:
        return False
    for root in _TRUSTED_BROWSER_ROOTS:
        try:
            rp.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def find_pdf_browser() -> Path:
    """定位支持 headless print-to-pdf 的 Chromium 浏览器（仅信任硬编码路径与可信根内的 PATH 结果）。"""
    for candidate in BROWSER_CANDIDATES:
        if candidate.exists():
            return candidate

    for name in ("chrome", "chrome.exe", "msedge", "msedge.exe", "google-chrome", "chromium"):
        resolved = shutil.which(name)
        if resolved:
            found = Path(resolved)
            # 防 PATH 劫持：只接受落在可信系统目录、且文件名是已知浏览器的可执行文件
            if _ALLOWED_BROWSER_NAME.match(found.name) and _under_trusted_root(found):
                return found

    raise RuntimeError("未找到可用的 Chrome/Edge 浏览器，无法将 HTML 转成 PDF")


def convert_html_to_pdf(
    html_path: str | Path,
    pdf_path: str | Path,
    *,
    timeout_sec: int = 120,
) -> Path:
    """Render a local HTML file to PDF via headless Chrome/Edge.

    浏览器可执行文件只能由 find_pdf_browser() 定位；不再接受外部传入路径（已消除 RCE 攻击面）。
    """
    html_file = Path(html_path).resolve()
    pdf_file = Path(pdf_path).resolve()
    pdf_file.parent.mkdir(parents=True, exist_ok=True)

    browser = find_pdf_browser()
    input_url = html_file.as_uri()

    commands = [
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=5000",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={pdf_file}",
            input_url,
        ],
        [
            str(browser),
            "--headless",
            "--disable-gpu",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=5000",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={pdf_file}",
            input_url,
        ],
    ]

    last_error: str | None = None
    for command in commands:
        if pdf_file.exists():
            pdf_file.unlink()
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        if completed.returncode == 0 and pdf_file.exists() and pdf_file.stat().st_size > 0:
            return pdf_file
        last_error = (completed.stderr or completed.stdout or "").strip()

    raise RuntimeError(
        f"HTML 转 PDF 失败: {last_error or 'browser did not create the PDF file'}"
    )
