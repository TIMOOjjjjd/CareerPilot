from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI

from utils import truncate_text

logger = logging.getLogger(__name__)

RECOMMENDATIONS = {"Strong Match", "Good Match", "Weak Match", "Not Recommended"}


def score_jobs(
    jobs: list[dict[str, Any]],
    candidate_profile: dict[str, Any],
    config: dict[str, Any],
    openai_api_key: str,
    model: str,
) -> list[dict[str, Any]]:
    client = OpenAI(api_key=openai_api_key)
    min_score = int(config["ranking"].get("min_score", 70))
    scored_jobs: list[dict[str, Any]] = []

    for index, job in enumerate(jobs, start=1):
        try:
            score = score_single_job(client, model, candidate_profile, job, config)
        except Exception:
            logger.exception("Failed to score job %s/%s: %s", index, len(jobs), job.get("title"))
            continue

        public_job = {key: value for key, value in job.items() if key != "raw"}
        scored = {
            **public_job,
            **score,
        }

        if int(scored["score"]) >= min_score:
            scored_jobs.append(scored)

    scored_jobs.sort(key=lambda item: int(item.get("score", 0)), reverse=True)
    logger.info("Scored %s jobs and kept %s above min_score=%s", len(jobs), len(scored_jobs), min_score)
    return scored_jobs


def score_single_job(
    client: OpenAI,
    model: str,
    candidate_profile: dict[str, Any],
    job: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "candidate_profile": candidate_profile,
        "ranking_weights": config["ranking"].get("scoring_weights", {}),
        "job": {
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "work_type": job.get("work_type"),
            "salary": job.get("salary"),
            "requirements": truncate_text(job.get("requirements"), 1_500),
            "description_summary": truncate_text(job.get("description"), 3_000),
        },
    }

    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are scoring job fit for one candidate. Return strict JSON only: "
                    '{"score": 0-100, "matched_skills": [], "missing_skills": [], '
                    '"reason": "", "recommendation": "Strong Match | Good Match | Weak Match | Not Recommended"}. '
                    "Use the provided weights, be conservative, and penalize seniority mismatch."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )

    content = response.choices[0].message.content or "{}"
    parsed = _parse_json_object(content)
    return normalize_score(parsed)


def normalize_score(parsed: dict[str, Any]) -> dict[str, Any]:
    try:
        score = int(round(float(parsed.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))

    matched = parsed.get("matched_skills", [])
    missing = parsed.get("missing_skills", [])
    recommendation = str(parsed.get("recommendation", "")).strip()
    if recommendation not in RECOMMENDATIONS:
        recommendation = recommendation_from_score(score)

    return {
        "score": score,
        "matched_skills": _as_string_list(matched),
        "missing_skills": _as_string_list(missing),
        "reason": truncate_text(parsed.get("reason", ""), 500),
        "recommendation": recommendation,
    }


def recommendation_from_score(score: int) -> str:
    if score >= 85:
        return "Strong Match"
    if score >= 70:
        return "Good Match"
    if score >= 50:
        return "Weak Match"
    return "Not Recommended"


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("OpenAI response was not a JSON object")
    return parsed


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
