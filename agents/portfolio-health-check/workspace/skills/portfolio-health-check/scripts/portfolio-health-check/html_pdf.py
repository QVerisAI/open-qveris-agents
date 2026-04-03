"""Convert local HTML reports to PDF using a local Chromium browser."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


BROWSER_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
]


def find_pdf_browser() -> Path:
    """Locate a Chromium browser that supports headless print-to-pdf."""
    for candidate in BROWSER_CANDIDATES:
        if candidate.exists():
            return candidate

    for name in ("chrome", "chrome.exe", "msedge", "msedge.exe"):
        resolved = shutil.which(name)
        if resolved:
            return Path(resolved)

    raise RuntimeError(
        "未找到可用的 Chrome/Edge 浏览器，无法将 HTML 转成 PDF"
    )


def convert_html_to_pdf(
    html_path: str | Path,
    pdf_path: str | Path,
    *,
    browser_path: str | Path | None = None,
    timeout_sec: int = 120,
) -> Path:
    """Render a local HTML file to PDF via headless Chrome/Edge."""
    html_file = Path(html_path).resolve()
    pdf_file = Path(pdf_path).resolve()
    pdf_file.parent.mkdir(parents=True, exist_ok=True)

    browser = Path(browser_path).resolve() if browser_path else find_pdf_browser()
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
