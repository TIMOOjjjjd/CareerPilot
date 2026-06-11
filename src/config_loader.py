from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from utils import is_placeholder, project_root


class ConfigError(RuntimeError):
    pass


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    root = project_root()
    load_dotenv(root / ".env")

    path = Path(config_path) if config_path else root / "config" / "config.yaml"
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    _apply_env_defaults(config)
    _validate_config(config)
    return config


def validate_environment(config: dict[str, Any]) -> None:
    required = ["OPENAI_API_KEY", "APIFY_TOKEN"]
    if config.get("notifications", {}).get("email_enabled", True):
        required.extend(["EMAIL_ADDRESS", "EMAIL_APP_PASSWORD"])

    missing = [name for name in required if is_placeholder(os.getenv(name))]
    if missing:
        names = ", ".join(missing)
        raise ConfigError(
            f"Missing required environment variables: {names}. "
            "For local runs, create .env from .env.example. "
            "For GitHub Actions, add repository secrets."
        )


def validate_runtime_config(config: dict[str, Any]) -> None:
    actor_id = config.get("sources", {}).get("apify_actor_id")
    if is_placeholder(actor_id):
        raise ConfigError("sources.apify_actor_id must be set before running the agent.")


def get_openai_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _apply_env_defaults(config: dict[str, Any]) -> None:
    email_address = os.getenv("EMAIL_ADDRESS")

    if "profile" in config:
        profile = config.setdefault("profile", {})
        if is_placeholder(profile.get("email")) and not is_placeholder(email_address):
            profile["email"] = email_address

    candidates = config.get("candidates", [])
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            profile = candidate.setdefault("profile", {})
            if is_placeholder(profile.get("email")) and not is_placeholder(email_address):
                profile["email"] = email_address

    sources = config.setdefault("sources", {})
    actor_id = sources.get("apify_actor_id") or sources.get("actor_id") or sources.get("actors_id")
    env_actor_id = os.getenv("APIFY_ACTOR_ID")
    if is_placeholder(actor_id) and not is_placeholder(env_actor_id):
        sources["apify_actor_id"] = env_actor_id


def _validate_config(config: dict[str, Any]) -> None:
    required_sections = [
        "job_preferences",
        "ranking",
        "sources",
        "notifications",
    ]
    if not config.get("candidates"):
        required_sections.extend(["profile", "resume"])

    missing = [section for section in required_sections if section not in config]
    if missing:
        raise ConfigError(f"Missing config sections: {', '.join(missing)}")

    _validate_candidates(config)

    preferences = config["job_preferences"]
    roles = preferences.get("roles", [])
    if roles is None:
        preferences["roles"] = []
    elif not isinstance(roles, list):
        raise ConfigError("job_preferences.roles must be a list. Use [] to infer roles from your resume.")

    for key in ["locations", "work_type"]:
        if not isinstance(preferences.get(key), list) or not preferences[key]:
            raise ConfigError(f"job_preferences.{key} must be a non-empty list")

    if not config.get("resume", {}).get("path"):
        if not config.get("candidates"):
            raise ConfigError("resume.path is required")

    if not config.get("profile", {}).get("email"):
        if not config.get("candidates"):
            raise ConfigError("profile.email is required")

    ranking = config["ranking"]
    if int(ranking.get("top_k_email", 0)) <= 0:
        raise ConfigError("ranking.top_k_email must be greater than zero")

    min_score = int(ranking.get("min_score", -1))
    if min_score < 0 or min_score > 100:
        raise ConfigError("ranking.min_score must be between 0 and 100")


def _validate_candidates(config: dict[str, Any]) -> None:
    candidates = config.get("candidates")
    if candidates is None:
        return
    if not isinstance(candidates, list) or not candidates:
        raise ConfigError("candidates must be a non-empty list")

    seen_ids: set[str] = set()
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            raise ConfigError(f"candidates[{index}] must be a mapping")

        candidate_id = candidate.get("id")
        if is_placeholder(candidate_id):
            raise ConfigError(f"candidates[{index}].id is required")
        if candidate_id in seen_ids:
            raise ConfigError(f"Duplicate candidate id: {candidate_id}")
        seen_ids.add(candidate_id)

        profile = candidate.get("profile", {})
        if not isinstance(profile, dict):
            raise ConfigError(f"candidates[{index}].profile must be a mapping")
        if not profile.get("email"):
            raise ConfigError(f"candidates[{index}].profile.email is required")

        resume = candidate.get("resume", {})
        if not isinstance(resume, dict):
            raise ConfigError(f"candidates[{index}].resume must be a mapping")
        if not resume.get("path"):
            raise ConfigError(f"candidates[{index}].resume.path is required")
