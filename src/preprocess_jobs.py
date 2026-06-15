from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from location_filters import job_is_in_uk, requires_uk_target
from utils import clean_html, clean_text, first_non_empty, job_identity, parse_salary_gbp

logger = logging.getLogger(__name__)


def normalize_and_filter_jobs(
    jobs: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    normalized = [normalize_job(job) for job in jobs]
    deduped = deduplicate_jobs(normalized)
    filtered: list[dict[str, Any]] = []

    for job in deduped:
        keep, reason = passes_rule_filters(job, config)
        if keep:
            filtered.append(job)
        else:
            logger.debug("Filtered out %s at %s: %s", job.get("title"), job.get("company"), reason)

    logger.info("Normalized %s jobs, deduplicated to %s, filtered to %s", len(jobs), len(deduped), len(filtered))
    return filtered


def normalize_job(job: dict[str, Any]) -> dict[str, Any]:
    salary = job.get("salary")
    if not isinstance(salary, dict):
        salary = parse_salary_gbp(salary)

    return {
        **job,
        "title": clean_text(job.get("title")),
        "company": clean_text(job.get("company")),
        "location": clean_text(job.get("location")),
        "work_type": clean_text(job.get("work_type")),
        "salary": salary,
        "description": clean_html(job.get("description")),
        "requirements": clean_html(job.get("requirements")),
        "apply_url": clean_text(job.get("apply_url")),
        "source": first_non_empty(job.get("source"), "unknown"),
        "posted_date": clean_text(job.get("posted_date")),
    }


def deduplicate_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for job in jobs:
        key = job_identity(job)
        if key in seen:
            continue
        seen.add(key)
        result.append(job)
    return result


def passes_rule_filters(job: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str]:
    preferences = config["job_preferences"]
    title = clean_text(job.get("title"))
    company = clean_text(job.get("company"))
    searchable_text = " ".join(
        [
            title,
            company,
            clean_text(job.get("location")),
            clean_text(job.get("work_type")),
            clean_text(job.get("description")),
            clean_text(job.get("requirements")),
        ]
    ).lower()

    blacklist = preferences.get("blacklist_companies", [])
    if _matches_company(company, blacklist):
        return False, "blacklisted company"

    whitelist = preferences.get("whitelist_companies", [])
    if whitelist and not _matches_company(company, whitelist):
        return False, "not in whitelist"

    excluded_keywords = preferences.get("excluded_keywords", [])
    if _contains_keyword(searchable_text, excluded_keywords):
        return False, "excluded keyword"

    if requires_uk_target(preferences) and not job_is_in_uk(job):
        return False, "outside United Kingdom"

    allowed_work_types = [clean_text(item).lower() for item in preferences.get("work_type", [])]
    work_type = clean_text(job.get("work_type")).lower()
    if (
        work_type
        and allowed_work_types
        and _contains_employment_type(work_type)
        and not any(item in work_type for item in allowed_work_types)
    ):
        return False, "work type mismatch"

    allowed_arrangements = [
        clean_text(item).lower()
        for item in preferences.get("hybrid_remote", [])
        if clean_text(item)
    ]
    arrangement = _infer_arrangement(searchable_text)
    if arrangement and allowed_arrangements and arrangement not in allowed_arrangements:
        return False, "remote or hybrid preference mismatch"

    max_age_days = preferences.get("max_job_age_days")
    posted_at = parse_posted_date(job.get("posted_date"))
    if posted_at and max_age_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(max_age_days))
        if posted_at < cutoff:
            return False, "job too old"

    salary_min = int(preferences.get("salary", {}).get("min_gbp", 0) or 0)
    if salary_min > 0 and not _salary_meets_minimum(job.get("salary", {}), salary_min):
        return False, "salary below threshold"

    sponsorship = preferences.get("visa_sponsorship", {})
    if sponsorship.get("required") and _explicitly_rejects_sponsorship(searchable_text):
        return False, "explicitly rejects visa sponsorship"

    return True, ""


def parse_posted_date(value: Any) -> datetime | None:
    text = clean_text(value).lower()
    if not text:
        return None

    now = datetime.now(timezone.utc)
    if text in {"today", "just posted"}:
        return now
    if text == "yesterday":
        return now - timedelta(days=1)

    relative = re.search(r"(\d+)\s+(day|days|hour|hours)\s+ago", text)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        return now - (timedelta(days=amount) if "day" in unit else timedelta(hours=amount))

    cleaned = text.replace("z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _salary_meets_minimum(salary: dict[str, Any], threshold: int) -> bool:
    min_gbp = salary.get("min_gbp")
    max_gbp = salary.get("max_gbp")
    if min_gbp is None and max_gbp is None:
        return True
    best_known = max_gbp if max_gbp is not None else min_gbp
    return int(best_known) >= threshold


def _explicitly_rejects_sponsorship(text: str) -> bool:
    patterns = [
        "no visa sponsorship",
        "cannot sponsor",
        "unable to sponsor",
        "must have right to work",
        "must already have the right to work",
        "no sponsorship available",
    ]
    return any(pattern in text for pattern in patterns)


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    return any(clean_text(keyword).lower() in text for keyword in keywords if clean_text(keyword))


def _matches_company(company: str, companies: list[str]) -> bool:
    company_lower = company.lower()
    return any(clean_text(item).lower() in company_lower for item in companies if clean_text(item))


def _infer_arrangement(text: str) -> str:
    if "remote" in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if "on-site" in text or "onsite" in text or "office based" in text:
        return "on-site"
    return ""


def _contains_employment_type(text: str) -> bool:
    markers = [
        "full-time",
        "full time",
        "part-time",
        "part time",
        "contract",
        "permanent",
        "internship",
        "temporary",
        "fixed term",
    ]
    return any(marker in text for marker in markers)
