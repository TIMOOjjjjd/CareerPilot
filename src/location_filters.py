from __future__ import annotations

import re
from typing import Any

from utils import clean_text, dedupe_list

UK_LINKEDIN_GEO_ID = "101165590"
UK_COUNTRY_NAME = "United Kingdom"

_UK_COUNTRY_TERMS = [
    "united kingdom",
    "great britain",
    "britain",
    "england",
    "scotland",
    "wales",
    "northern ireland",
]

_GENERIC_LOCATION_TERMS = {
    "remote",
    "hybrid",
    "on-site",
    "onsite",
    "office based",
    "office-based",
    "work from home",
    "anywhere",
}

_NON_UK_PATTERNS = [
    r"\bunited states\b",
    r"\bu\.s\.\b",
    r"\busa\b",
    r"\bus\b",
    r"\bcanada\b",
    r"\bchina\b",
    r"\bhong kong\b",
    r"\bindia\b",
    r"\bsingapore\b",
    r"\bfrance\b",
    r"\bspain\b",
    r"\bgermany\b",
    r"\bnetherlands\b",
    r"\bpoland\b",
    r"\bitaly\b",
    r"\bportugal\b",
    r"\bcolombia\b",
    r"\bnew zealand\b",
    r"\baustralia\b",
    r"\bafrica\b",
    r"\basia\b",
    r"\bworldwide\b",
    r"\bglobal\b",
    r"\bemea\b",
    r"\beurope\b",
]

_US_STATE_CODES = {
    "al",
    "ak",
    "az",
    "ar",
    "ca",
    "co",
    "ct",
    "dc",
    "de",
    "fl",
    "ga",
    "hi",
    "ia",
    "id",
    "il",
    "in",
    "ks",
    "ky",
    "la",
    "ma",
    "md",
    "me",
    "mi",
    "mn",
    "mo",
    "ms",
    "mt",
    "nc",
    "nd",
    "ne",
    "nh",
    "nj",
    "nm",
    "nv",
    "ny",
    "oh",
    "ok",
    "or",
    "pa",
    "ri",
    "sc",
    "sd",
    "tn",
    "tx",
    "ut",
    "va",
    "vt",
    "wa",
    "wi",
    "wv",
    "wy",
}

_JOB_LOCATION_KEYS = [
    "location",
    "jobLocation",
    "jobLocations",
    "address",
    "city",
    "area",
    "region",
    "country",
    "countryCode",
    "addressCountry",
    "addressRegion",
    "addressLocality",
]


def requires_uk_target(preferences: dict[str, Any]) -> bool:
    target_country = clean_text(preferences.get("target_country"))
    if target_country:
        return is_uk_location_term(target_country)

    return any(
        is_uk_location_term(location)
        for location in preferences.get("locations", [])
        if clean_text(location)
    )


def target_search_locations(preferences: dict[str, Any]) -> list[str]:
    locations = [
        clean_text(location)
        for location in preferences.get("locations", [])
        if clean_text(location)
    ]
    if not requires_uk_target(preferences):
        return dedupe_list(locations)

    filtered = [
        location
        for location in locations
        if clean_text(location).lower() not in _GENERIC_LOCATION_TERMS
    ]
    if not any(is_uk_location_term(location) for location in filtered):
        filtered.append(UK_COUNTRY_NAME)
    return dedupe_list(filtered)


def is_uk_location_term(value: Any) -> bool:
    text = _normalize_location_text(value)
    if not text:
        return False
    if re.search(r"\bu\.?k\.?(?:\b|$)", text):
        return True
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in _UK_COUNTRY_TERMS)


def job_is_in_uk(job: dict[str, Any]) -> bool:
    location_values = _job_location_values(job)
    if any(_has_non_uk_location_signal(location) for location in location_values):
        return False
    if any(is_uk_location_term(location) for location in location_values):
        return True

    return False


def linkedin_geo_id_for_location(location: Any) -> str:
    if is_uk_location_term(location):
        return UK_LINKEDIN_GEO_ID
    return ""


def _job_location_values(job: dict[str, Any]) -> list[str]:
    values = [
        clean_text(job.get(key))
        for key in _JOB_LOCATION_KEYS
        if clean_text(job.get(key))
    ]

    raw = job.get("raw")
    if isinstance(raw, dict):
        for key in _JOB_LOCATION_KEYS:
            if key in raw:
                values.extend(_flatten_location_value(raw[key]))

    return dedupe_list(values)


def _flatten_location_value(value: Any) -> list[str]:
    if isinstance(value, dict):
        values: list[str] = []
        for nested in value.values():
            values.extend(_flatten_location_value(nested))
        return values
    if isinstance(value, list):
        values = []
        for nested in value:
            values.extend(_flatten_location_value(nested))
        return values

    text = clean_text(value)
    return [text] if text else []


def _has_non_uk_location_signal(value: Any) -> bool:
    raw_text = clean_text(value).lower()
    text = _normalize_location_text(value)
    if not text:
        return False

    text_without_northern_ireland = text.replace("northern ireland", "")
    if re.search(r"\bireland\b", text_without_northern_ireland):
        return True

    if _has_us_state_signal(raw_text):
        return True

    return any(re.search(pattern, text) for pattern in _NON_UK_PATTERNS)


def _normalize_location_text(value: Any) -> str:
    text = clean_text(value).lower()
    return re.sub(r"[\s,/|]+", " ", text).strip()


def _has_us_state_signal(text: str) -> bool:
    if re.search(r"\bwashington\s*,?\s*d\.?c\.?\b", text):
        return True

    codes = "|".join(sorted(_US_STATE_CODES))
    return bool(re.search(rf"(?:,|\||/|\s-\s)\s*({codes})(?:\b|$)", text))
