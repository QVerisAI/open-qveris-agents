"""Minimal QVeris REST client.

Pruned copy of the core HTTP methods in
`portfolio-health-check/scripts/portfolio-health-check/qveris_client.py`.
This is intentionally not a strict mirror — the following changes were
made and should NOT be reverted when syncing:

- `QVerisConfig` drops the portfolio-specific `state_file` field.
- `search_tools` / `execute_tool` drop the unused `session_id` parameter.
- Both this and the portfolio copy now treat `search_id` as optional and
  enforce keyword-only call style (Qveris broker no longer requires it,
  and keyword-only prevents positional-call drift). Keep aligned on
  the next sync.
- `download_full_content` is promoted from private `_download_full_content`
  to public, and now raises on failure (the portfolio copy swallowed errors
  and returned None — that behavior is incorrect for this skill).
- A 5MB response-size cap is applied to `download_full_content` to prevent
  a poisoned / misbehaving OSS URL from blowing up skill memory.
- Pandas, date_utils, portfolio state_file, and CLI subcommands are
  intentionally omitted so this module has no pip dependencies.

Sync procedure: when the Qveris HTTP contract (base_url, auth header,
/search or /tools/execute envelope structure) changes, manually diff
against the portfolio copy — do NOT copy-paste, since the two files
have diverged in the ways listed above. Last reviewed: 2026-04-11.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class QVerisConfig:
    api_key: str = os.getenv("QVERIS_TOKEN", "")
    base_url: str = os.getenv("QVERIS_BASE_URL", "https://qveris.ai/api/v1")
    timeout: int = int(os.getenv("QVERIS_TIMEOUT", "60"))


class QVerisClient:
    # Hard cap on ANY network read from Qveris endpoints — covers both
    # `/search`, `/tools/execute` JSON responses in `_post_json` and the
    # signed-OSS full-content download in `download_full_content`.
    # Largest observed real execute response is ~340KB; 5MB leaves ~15x
    # margin for schema growth and still blocks a poisoned / misbehaving
    # endpoint from exhausting skill memory.
    MAX_RESPONSE_BYTES = 5 * 1024 * 1024

    # Cap on HTTPError response body used purely for diagnostic messages —
    # 4KB is more than enough for any sane JSON error envelope.
    MAX_ERROR_BODY_BYTES = 4096

    def __init__(self, config: Optional[QVerisConfig] = None) -> None:
        self.config = config or QVerisConfig()
        if not self.config.api_key:
            raise ValueError("QVERIS_TOKEN is not set")
        self.headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, headers=self.headers, method="POST")
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                # Read one extra byte so we can detect overflow; a cooperating
                # server will always fit under MAX_RESPONSE_BYTES.
                raw_bytes = response.read(self.MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            body = (
                exc.read(self.MAX_ERROR_BODY_BYTES).decode("utf-8", errors="replace")
                if exc.fp
                else ""
            )
            raise RuntimeError(
                f"QVeris HTTP error {exc.code}: {body or exc.reason}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"QVeris request failed: {exc.reason}") from exc
        if len(raw_bytes) > self.MAX_RESPONSE_BYTES:
            raise RuntimeError(
                f"QVeris response exceeded limit {self.MAX_RESPONSE_BYTES} bytes"
            )
        return json.loads(raw_bytes.decode("utf-8"))

    def search_tools(self, query: str, limit: int = 10) -> Dict[str, Any]:
        payload = {"query": query, "limit": limit}
        return self._post_json(f"{self.config.base_url}/search", payload)

    def execute_tool(
        self,
        tool_id: str,
        *,
        parameters: Dict[str, Any],
        max_response_size: int = 102400,
        search_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "parameters": parameters,
            "max_response_size": max_response_size,
        }
        if search_id:
            payload["search_id"] = search_id
        url = f"{self.config.base_url}/tools/execute?tool_id={tool_id}"
        return self._post_json(url, payload)

    def download_full_content(self, url: str) -> Any:
        """Fetch the signed OSS URL returned when an execute_tool response is truncated.

        Qveris returns `result.full_content_file_url` instead of `result.data`
        when the raw tool response exceeds `max_response_size`. The downloaded
        file contains the original tool response as-is.

        Enforces `MAX_RESPONSE_BYTES` on both the pre-read `Content-Length`
        header (when present) and the actual bytes read, so a misbehaving URL
        cannot OOM the skill process.
        """
        limit = self.MAX_RESPONSE_BYTES
        try:
            with urlopen(url, timeout=self.config.timeout) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None:
                    try:
                        declared_len = int(declared)
                    except ValueError:
                        declared_len = -1
                    if declared_len > limit:
                        raise RuntimeError(
                            f"QVeris full-content too large: Content-Length={declared_len} "
                            f"exceeds limit {limit}"
                        )
                # Read one extra byte so we can detect silent overflow when
                # Content-Length is missing or understated.
                raw_bytes = response.read(limit + 1)
        except HTTPError as exc:
            body = (
                exc.read(self.MAX_ERROR_BODY_BYTES).decode("utf-8", errors="replace")
                if exc.fp
                else ""
            )
            raise RuntimeError(
                f"QVeris full-content download HTTP error {exc.code}: {body or exc.reason}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"QVeris full-content download failed: {exc.reason}"
            ) from exc
        if len(raw_bytes) > limit:
            raise RuntimeError(
                f"QVeris full-content exceeded limit {limit} bytes while streaming"
            )
        return json.loads(raw_bytes.decode("utf-8"))
