from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from utils import job_identity, utc_now_iso

logger = logging.getLogger(__name__)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError:
        logger.warning("Ignoring invalid JSON file: %s", path)
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")
    tmp_path.replace(path)


def save_raw_jobs(data_dir: Path, jobs: list[dict[str, Any]]) -> None:
    save_json(data_dir / "jobs_raw.json", jobs)


def save_search_urls(data_dir: Path, urls: list[str]) -> None:
    save_json(
        data_dir / "search_urls.json",
        {
            "updated_at": utc_now_iso(),
            "urls": urls,
        },
    )


def save_scored_jobs(data_dir: Path, jobs: list[dict[str, Any]]) -> None:
    save_json(data_dir / "jobs_scored.json", jobs)


def load_history(data_dir: Path) -> dict[str, Any]:
    return load_json(
        data_dir / "history.json",
        {
            "emailed_job_keys": [],
            "emailed_jobs": [],
            "updated_at": None,
        },
    )


def save_history(data_dir: Path, history: dict[str, Any]) -> None:
    history["updated_at"] = utc_now_iso()
    save_json(data_dir / "history.json", history)


def filter_unseen_jobs(jobs: list[dict[str, Any]], history: dict[str, Any]) -> list[dict[str, Any]]:
    seen = set(history.get("emailed_job_keys", []))
    return [job for job in jobs if job_identity(job) not in seen]


def mark_jobs_emailed(history: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    emailed_keys = list(history.get("emailed_job_keys", []))
    emailed_jobs = list(history.get("emailed_jobs", []))
    key_set = set(emailed_keys)
    sent_at = utc_now_iso()

    for job in jobs:
        key = job_identity(job)
        if key in key_set:
            continue
        key_set.add(key)
        emailed_keys.append(key)
        emailed_jobs.append(
            {
                "key": key,
                "title": job.get("title"),
                "company": job.get("company"),
                "location": job.get("location"),
                "apply_url": job.get("apply_url"),
                "score": job.get("score"),
                "sent_at": sent_at,
            }
        )

    history["emailed_job_keys"] = emailed_keys[-5000:]
    history["emailed_jobs"] = emailed_jobs[-1000:]
    return history
