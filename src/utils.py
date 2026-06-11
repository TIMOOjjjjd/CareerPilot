from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def setup_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value if item is not None)
    return re.sub(r"\s+", " ", str(value)).strip()


def clean_html(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    if "<" not in text or ">" not in text:
        return text
    soup = BeautifulSoup(text, "html.parser")
    return clean_text(soup.get_text(" "))


def first_non_empty(*values: Any) -> str:
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""


def truncate_text(text: Any, max_chars: int) -> str:
    cleaned = clean_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def is_placeholder(value: Any) -> bool:
    text = clean_text(value).lower()
    return not text or text.startswith("your_") or text.startswith("replace_") or "your_" in text


def normalize_email_address(value: str) -> str:
    text = clean_text(value)
    mailto_match = re.search(r"mailto:([^)>\s]+)", text)
    if mailto_match:
        return mailto_match.group(1)
    bracket_match = re.search(r"<([^>]+@[^>]+)>", text)
    if bracket_match:
        return bracket_match.group(1)
    plain_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    if plain_match:
        return plain_match.group(0)
    return text


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def job_identity(job: dict[str, Any]) -> str:
    url = clean_text(job.get("apply_url") or job.get("url"))
    if url:
        return f"url:{stable_hash(url.lower())}"
    parts = [
        clean_text(job.get("title")).lower(),
        clean_text(job.get("company")).lower(),
        clean_text(job.get("location")).lower(),
    ]
    return f"job:{stable_hash('|'.join(parts))}"


def dedupe_list(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = clean_text(value)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def parse_salary_gbp(value: Any) -> dict[str, Any]:
    raw = clean_text(value)
    if not raw:
        return {"raw": "", "min_gbp": None, "max_gbp": None}

    if isinstance(value, (int, float)) and value > 0:
        amount = int(value)
        return {"raw": raw, "min_gbp": amount, "max_gbp": amount}

    lowered = raw.lower()
    if any(token in lowered for token in ["hour", "hourly", "/hr", "per hour", "day rate", "per day"]):
        return {"raw": raw, "min_gbp": None, "max_gbp": None}

    amounts: list[int] = []
    pattern = re.compile(
        r"(?:£|gbp\s*)?\s*(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)\s*(k|thousand)?",
        re.IGNORECASE,
    )

    for match in pattern.finditer(raw):
        number_text = match.group(1).replace(",", "").replace(" ", "")
        suffix = (match.group(2) or "").lower()
        try:
            number = float(number_text)
        except ValueError:
            continue

        if suffix in {"k", "thousand"}:
            number *= 1000
        elif number < 1000:
            continue

        if 10_000 <= number <= 500_000:
            amounts.append(int(number))

    if not amounts:
        return {"raw": raw, "min_gbp": None, "max_gbp": None}

    return {"raw": raw, "min_gbp": min(amounts), "max_gbp": max(amounts)}
