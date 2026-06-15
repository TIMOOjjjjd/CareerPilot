from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote, urlencode

import requests

from config_loader import ConfigError
from location_filters import linkedin_geo_id_for_location, target_search_locations
from utils import clean_html, clean_text, first_non_empty, is_placeholder, parse_salary_gbp

logger = logging.getLogger(__name__)

APIFY_SYNC_ENDPOINT = "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"


def collect_jobs(config: dict[str, Any], apify_token: str) -> list[dict[str, Any]]:
    sources = config["sources"]
    actor_id = clean_text(sources.get("apify_actor_id"))
    if is_placeholder(actor_id):
        raise ConfigError("sources.apify_actor_id must be set to a real Apify actor ID.")

    max_jobs = int(sources.get("max_jobs_per_run", 100))
    actor_ref = quote(actor_id.replace("/", "~"), safe="~")
    url = APIFY_SYNC_ENDPOINT.format(actor_id=actor_ref)
    run_input = build_apify_input(config)

    logger.info("Collecting jobs from Apify actor %s", actor_id)
    response = requests.post(
        url,
        params={
            "token": apify_token,
            "clean": "true",
            "format": "json",
            "timeout": "180",
        },
        json=run_input,
        timeout=240,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Apify request failed with HTTP {response.status_code}: {response.text[:500]}"
        )

    payload = response.json()
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("data") or payload.get("results") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    jobs = [normalize_apify_item(item) for item in items[:max_jobs] if isinstance(item, dict)]
    jobs = [job for job in jobs if job.get("title") or job.get("apply_url")]
    logger.info("Collected %s jobs from Apify", len(jobs))
    return jobs


def build_apify_input(config: dict[str, Any]) -> dict[str, Any]:
    preferences = config["job_preferences"]
    sources = config["sources"]
    actor_id = clean_text(sources.get("apify_actor_id"))
    actor_input = sources.get("apify_input")

    if actor_id.lower() == "curious_coder/linkedin-jobs-scraper":
        run_input = build_linkedin_jobs_scraper_input(config)
        if isinstance(actor_input, dict):
            run_input.update(actor_input)
        return run_input

    roles = [clean_text(role) for role in preferences.get("roles", []) if clean_text(role)]
    locations = target_search_locations(preferences)
    max_jobs = int(sources.get("max_jobs_per_run", 100))
    queries = [f"{role} {location}".strip() for role in roles for location in locations]

    run_input: dict[str, Any] = {
        "queries": queries,
        "searchQueries": queries,
        "searchTerms": queries,
        "roles": roles,
        "locations": locations,
        "workType": preferences.get("work_type", []),
        "maxItems": max_jobs,
        "maxResults": max_jobs,
        "maxJobs": max_jobs,
        "resultsLimit": max_jobs,
        "publishedWithinDays": published_within_days(preferences),
    }

    if isinstance(actor_input, dict):
        run_input.update(actor_input)

    return run_input


def build_linkedin_jobs_scraper_input(config: dict[str, Any]) -> dict[str, Any]:
    sources = config["sources"]
    actor_input = sources.get("apify_input")
    scrape_company = bool(sources.get("scrapeCompany", False))
    if isinstance(actor_input, dict) and actor_input.get("urls"):
        return {
            "urls": actor_input["urls"],
            "count": max(10, int(sources.get("max_jobs_per_run", 100))),
            "scrapeCompany": bool(actor_input.get("scrapeCompany", scrape_company)),
        }

    preferences = config["job_preferences"]
    roles = [clean_text(role) for role in preferences.get("roles", []) if clean_text(role)]
    locations = target_search_locations(preferences)
    if not roles:
        raise ConfigError(
            "No target roles available for LinkedIn search URL generation. "
            "Set job_preferences.roles or allow resume role inference before collection."
        )
    if not locations:
        raise ConfigError("job_preferences.locations must contain at least one location.")

    urls = [
        build_linkedin_search_url(role, location, preferences)
        for role in roles
        for location in locations
    ]
    return {
        "urls": urls,
        "count": max(10, int(sources.get("max_jobs_per_run", 100))),
        "scrapeCompany": scrape_company,
    }


def build_linkedin_search_url(role: str, location: str, preferences: dict[str, Any]) -> str:
    params: dict[str, str] = {
        "keywords": role,
        "location": location,
    }

    geo_id = linkedin_geo_id_for_location(location)
    if geo_id:
        params["geoId"] = geo_id

    date_filter = linkedin_date_filter(preferences)
    if date_filter:
        params["f_TPR"] = date_filter

    job_type_filter = linkedin_job_type_filter(preferences.get("work_type", []))
    if job_type_filter:
        params["f_JT"] = job_type_filter

    work_arrangement_filter = linkedin_work_arrangement_filter(
        preferences.get("hybrid_remote", []),
        location,
    )
    if work_arrangement_filter:
        params["f_WT"] = work_arrangement_filter

    seniority_filter = linkedin_experience_filter(preferences.get("seniority", []))
    if seniority_filter:
        params["f_E"] = seniority_filter

    return "https://www.linkedin.com/jobs/search/?" + urlencode(params)


def linkedin_date_filter(preferences: dict[str, Any]) -> str:
    job_age_window = clean_text(preferences.get("job_age_window")).lower()
    if job_age_window == "24h":
        return "r86400"
    if job_age_window == "7d":
        return "r604800"

    max_job_age_days = preferences.get("max_job_age_days")
    try:
        days = int(max_job_age_days)
    except (TypeError, ValueError):
        return ""
    if days <= 1:
        return "r86400"
    if days <= 7:
        return "r604800"
    if days <= 30:
        return "r2592000"
    return ""


def published_within_days(preferences: dict[str, Any]) -> int:
    job_age_window = clean_text(preferences.get("job_age_window")).lower()
    if job_age_window == "24h":
        return 1
    if job_age_window == "7d":
        return 7
    return int(preferences.get("max_job_age_days", 7))


def linkedin_job_type_filter(work_types: list[str]) -> str:
    mapping = {
        "full-time": "F",
        "full time": "F",
        "part-time": "P",
        "part time": "P",
        "contract": "C",
        "temporary": "T",
        "internship": "I",
    }
    values = []
    for work_type in work_types:
        value = mapping.get(clean_text(work_type).lower())
        if value and value not in values:
            values.append(value)
    return ",".join(values)


def linkedin_work_arrangement_filter(arrangements: list[str], location: str) -> str:
    mapping = {
        "on-site": "1",
        "onsite": "1",
        "remote": "2",
        "hybrid": "3",
    }
    values = []
    for arrangement in arrangements:
        value = mapping.get(clean_text(arrangement).lower())
        if value and value not in values:
            values.append(value)

    if clean_text(location).lower() == "remote" and "2" not in values:
        values.append("2")

    return ",".join(values)


def linkedin_experience_filter(seniority: list[str]) -> str:
    mapping = {
        "internship": "1",
        "graduate": "2",
        "entry level": "2",
        "entry-level": "2",
        "associate": "3",
        "mid-senior level": "4",
        "director": "5",
        "executive": "6",
    }
    values = []
    for item in seniority:
        value = mapping.get(clean_text(item).lower())
        if value and value not in values:
            values.append(value)
    return ",".join(values)


def normalize_apify_item(item: dict[str, Any]) -> dict[str, Any]:
    salary_raw = _first_value(
        item,
        [
            "salary",
            "salaryText",
            "salaryRange",
            "compensation",
            "baseSalary",
            "pay",
        ],
    )
    description = clean_html(
        _first_value(
            item,
            [
                "description",
                "jobDescription",
                "htmlDescription",
                "details",
                "summary",
            ],
        )
    )

    return {
        "title": _first_value(item, ["title", "jobTitle", "position", "positionName", "name"]),
        "company": _first_value(item, ["company", "companyName", "employer", "organization", "hiringOrganization.name"]),
        "location": _first_value(item, ["location", "jobLocation", "address", "city", "area"]),
        "work_type": _infer_work_type(item, description),
        "salary": parse_salary_gbp(salary_raw),
        "description": description,
        "requirements": clean_html(_first_value(item, ["requirements", "qualifications", "skills"])),
        "apply_url": _first_value(item, ["applyUrl", "apply_url", "url", "link", "jobUrl", "job_url"]),
        "source": first_non_empty(_first_value(item, ["source", "site", "platform"]), "apify"),
        "posted_date": _first_value(item, ["postedDate", "posted_date", "datePosted", "createdAt", "publishedAt"]),
        "raw": item,
    }


def _first_value(item: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = _get_nested(item, key)
        if isinstance(value, dict):
            value = first_non_empty(
                value.get("name"),
                value.get("title"),
                value.get("text"),
                value.get("value"),
            )
        elif isinstance(value, list):
            value = " ".join(clean_text(entry) for entry in value if entry)
        text = clean_text(value)
        if text:
            return text
    return ""


def _get_nested(item: dict[str, Any], key: str) -> Any:
    current: Any = item
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _infer_work_type(item: dict[str, Any], description: str) -> str:
    direct = _first_value(item, ["workType", "employmentType", "jobType", "schedule"])
    text = f"{direct} {description}".lower()
    arrangement = ""
    if "remote" in text:
        arrangement = "Remote"
    elif "hybrid" in text:
        arrangement = "Hybrid"

    if direct and arrangement and arrangement.lower() not in direct.lower():
        return f"{direct} {arrangement}"
    if direct:
        return direct
    if arrangement:
        return arrangement
    return ""
