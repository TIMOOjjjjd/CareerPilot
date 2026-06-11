from __future__ import annotations

import logging
import os

from collect_jobs import collect_jobs
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
)
from utils import dedupe_list, project_root, setup_logging

logger = logging.getLogger(__name__)


def run() -> int:
    setup_logging()
    root = project_root()
    data_dir = root / "data"
    template_dir = root / "templates"

    config = load_config()
    validate_environment(config)
    validate_runtime_config(config)

    openai_api_key = os.environ["OPENAI_API_KEY"]
    apify_token = os.environ["APIFY_TOKEN"]
    model = get_openai_model()

    candidate_profile = parse_resume(
        config=config,
        data_dir=data_dir,
        openai_api_key=openai_api_key,
        model=model,
    )
    resolve_target_roles(config, candidate_profile)

    raw_jobs = collect_jobs(config=config, apify_token=apify_token)
    save_raw_jobs(data_dir, raw_jobs)

    filtered_jobs = normalize_and_filter_jobs(raw_jobs, config)
    if not filtered_jobs:
        logger.info("No jobs left after preprocessing filters")
        save_scored_jobs(data_dir, [])
        return 0

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
        return 0

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

    return 0


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
