from __future__ import annotations

from copy import deepcopy
import logging
import os
import re
from pathlib import Path
from typing import Any

from collect_jobs import build_apify_input, collect_jobs, search_urls_from_apify_input
from config_loader import (
    ConfigError,
    get_openai_model,
    load_config,
    validate_environment,
    validate_runtime_config,
)
from email_sender import send_job_email
from preprocess_jobs import normalize_and_filter_jobs
from resume_parser import parse_resume
from score_jobs import score_jobs
from storage import (
    filter_unseen_jobs,
    load_history,
    mark_jobs_emailed,
    save_history,
    save_raw_jobs,
    save_scored_jobs,
    save_search_urls,
)
from utils import clean_text, dedupe_list, project_root, setup_logging

logger = logging.getLogger(__name__)


def run() -> int:
    setup_logging()
    root = project_root()
    base_data_dir = root / "data"
    template_dir = root / "templates"

    config = load_config()
    validate_environment(config)
    validate_runtime_config(config)

    openai_api_key = os.environ["OPENAI_API_KEY"]
    apify_token = os.environ["APIFY_TOKEN"]
    model = get_openai_model()

    failures = 0
    for candidate_config in build_candidate_configs(config):
        candidate_id = candidate_config["_candidate_id"]
        data_dir = base_data_dir / candidate_id
        try:
            run_candidate(
                config=candidate_config,
                candidate_id=candidate_id,
                data_dir=data_dir,
                template_dir=template_dir,
                openai_api_key=openai_api_key,
                apify_token=apify_token,
                model=model,
            )
        except Exception:
            failures += 1
            logger.exception("Candidate run failed: %s", candidate_id)

    if failures:
        raise RuntimeError(f"{failures} candidate run(s) failed")

    return 0


def run_candidate(
    config: dict[str, Any],
    candidate_id: str,
    data_dir: Path,
    template_dir: Path,
    openai_api_key: str,
    apify_token: str,
    model: str,
) -> None:
    logger.info(
        "Running candidate %s with resume %s",
        candidate_id,
        config["resume"]["path"],
    )

    candidate_profile = parse_resume(
        config=config,
        data_dir=data_dir,
        openai_api_key=openai_api_key,
        model=model,
    )
    resolve_target_roles(config, candidate_profile)

    run_input = build_apify_input(config)
    save_search_urls(data_dir, search_urls_from_apify_input(run_input))
    raw_jobs = collect_jobs(config=config, apify_token=apify_token, run_input=run_input)
    save_raw_jobs(data_dir, raw_jobs)

    filtered_jobs = normalize_and_filter_jobs(raw_jobs, config)
    if not filtered_jobs:
        logger.info("No jobs left after preprocessing filters")
        save_scored_jobs(data_dir, [])
        return

    scored_jobs = score_jobs(
        jobs=filtered_jobs,
        candidate_profile=candidate_profile,
        config=config,
        openai_api_key=openai_api_key,
        model=model,
    )
    save_scored_jobs(data_dir, scored_jobs)

    if not config["notifications"].get("email_enabled", True):
        logger.info("Email notifications are disabled")
        return

    history = load_history(data_dir)
    unseen_jobs = filter_unseen_jobs(scored_jobs, history)
    top_k = int(config["ranking"].get("top_k_email", 10))
    email_jobs = unseen_jobs[:top_k]

    sent = send_job_email(
        jobs=email_jobs,
        config=config,
        sender_email=os.environ["EMAIL_ADDRESS"],
        app_password=os.environ["EMAIL_APP_PASSWORD"],
        template_dir=template_dir,
    )
    if sent:
        history = mark_jobs_emailed(history, email_jobs)
        save_history(data_dir, history)


def build_candidate_configs(config: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = config.get("candidates")
    if not candidates:
        legacy_config = deepcopy(config)
        legacy_config["_candidate_id"] = "default"
        return [legacy_config]

    base_config = {key: value for key, value in config.items() if key != "candidates"}
    candidate_configs: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = safe_candidate_id(candidate["id"])
        merged = deepcopy(base_config)
        merged["profile"] = deepcopy(candidate["profile"])
        merged["resume"] = deepcopy(candidate["resume"])

        for section in ["job_preferences", "ranking", "sources", "notifications"]:
            if isinstance(candidate.get(section), dict):
                merged[section] = merge_dicts(merged.get(section, {}), candidate[section])

        merged["_candidate_id"] = candidate_id
        candidate_configs.append(merged)

    return candidate_configs


def merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def safe_candidate_id(value: Any) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^a-z0-9_.-]+", "-", text).strip("-")
    if not text:
        raise ConfigError("Candidate id must contain at least one letter or number")
    return text


def resolve_target_roles(config: dict, candidate_profile: dict) -> None:
    configured_roles = dedupe_list(config["job_preferences"].get("roles", []))
    if configured_roles:
        config["job_preferences"]["roles"] = configured_roles
        logger.info("Using configured target roles: %s", ", ".join(configured_roles))
        return

    inferred_roles = dedupe_list(candidate_profile.get("target_roles", []))
    if not inferred_roles:
        raise ConfigError(
            "job_preferences.roles is empty and no target roles could be inferred from the resume."
        )

    config["job_preferences"]["roles"] = inferred_roles
    logger.info("Inferred target roles from resume: %s", ", ".join(inferred_roles))


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (ConfigError, FileNotFoundError, ValueError) as exc:
        setup_logging()
        logger.error("%s", exc)
        raise SystemExit(1)
    except Exception:
        setup_logging()
        logger.exception("CareerPilot AI failed")
        raise SystemExit(1)
