from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openai import OpenAI
from pypdf import PdfReader

from storage import load_json, save_json
from utils import clean_text, dedupe_list, project_root, truncate_text

logger = logging.getLogger(__name__)


def parse_resume(
    config: dict[str, Any],
    data_dir: Path,
    openai_api_key: str,
    model: str,
) -> dict[str, Any]:
    resume_path = project_root() / config["resume"]["path"]
    if not resume_path.exists():
        raise FileNotFoundError(
            f"Resume file not found: {resume_path}. Add your PDF at resume/resume.pdf."
        )

    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / "resume_profile.json"
    fingerprint = _resume_fingerprint(resume_path)
    cached = load_json(cache_path, default={})

    if cached.get("source", {}).get("fingerprint") == fingerprint and cached.get("profile"):
        logger.info("Using cached resume profile from %s", cache_path)
        return cached["profile"]

    logger.info("Extracting text from resume: %s", resume_path)
    resume_text = extract_pdf_text(resume_path)
    if len(resume_text) < 100:
        raise ValueError("Resume text extraction returned too little text to build a profile.")

    try:
        profile = extract_profile_with_openai(
            resume_text=resume_text,
            target_roles=config["job_preferences"].get("roles", []),
            openai_api_key=openai_api_key,
            model=model,
        )
    except Exception:
        logger.exception("OpenAI resume profile extraction failed. Using heuristic profile.")
        profile = heuristic_profile(resume_text, config["job_preferences"].get("roles", []))

    profile["target_roles"] = dedupe_list(
        profile.get("target_roles", []) + config["job_preferences"].get("roles", [])
    )
    profile.setdefault("keywords", [])
    profile["keywords"] = dedupe_list(profile["keywords"])

    save_json(
        cache_path,
        {
            "source": {
                "path": str(resume_path),
                "fingerprint": fingerprint,
            },
            "profile": profile,
        },
    )
    return profile


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    chunks: list[str] = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    return clean_text("\n".join(chunks))


def extract_profile_with_openai(
    resume_text: str,
    target_roles: list[str],
    openai_api_key: str,
    model: str,
) -> dict[str, Any]:
    client = OpenAI(api_key=openai_api_key)
    prompt_payload = {
        "target_roles": target_roles,
        "resume_text": truncate_text(resume_text, 18_000),
    }

    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract a concise structured candidate profile from the resume. "
                    "Return strict JSON with keys: summary, skills, projects, education, "
                    "experience, target_roles, keywords. Use arrays for list fields. "
                    "If target_roles is empty, infer 3 to 6 realistic target job titles "
                    "from the resume's skills, projects, education, and experience."
                ),
            },
            {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
        ],
    )

    content = response.choices[0].message.content or "{}"
    profile = json.loads(content)
    return normalize_profile(profile)


def heuristic_profile(resume_text: str, target_roles: list[str]) -> dict[str, Any]:
    common_skills = [
        "Python",
        "JavaScript",
        "TypeScript",
        "SQL",
        "React",
        "Node.js",
        "Django",
        "FastAPI",
        "AWS",
        "Docker",
        "Pandas",
        "Machine Learning",
    ]
    lowered = resume_text.lower()
    skills = [skill for skill in common_skills if skill.lower() in lowered]
    inferred_roles = target_roles or infer_roles_heuristically(lowered, skills)
    return {
        "summary": truncate_text(resume_text, 900),
        "skills": skills,
        "projects": [],
        "education": [],
        "experience": [],
        "target_roles": inferred_roles,
        "keywords": dedupe_list(skills + inferred_roles),
    }


def infer_roles_heuristically(lowered_resume_text: str, skills: list[str]) -> list[str]:
    roles: list[str] = []
    skill_text = " ".join(skills).lower()
    combined = f"{lowered_resume_text} {skill_text}"

    if any(token in combined for token in ["machine learning", "data science", "pandas", "modeling"]):
        roles.append("Data Scientist")
    if any(token in combined for token in ["backend", "api", "django", "fastapi", "node.js"]):
        roles.append("Backend Engineer")
    if any(token in combined for token in ["react", "frontend", "front-end", "typescript"]):
        roles.append("Frontend Engineer")
    if any(token in combined for token in ["software engineer", "python", "java", "javascript"]):
        roles.append("Software Engineer")
    if any(token in combined for token in ["data analyst", "sql", "dashboard", "analytics"]):
        roles.append("Data Analyst")

    return dedupe_list(roles or ["Software Engineer"])


def normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    normalized["summary"] = clean_text(profile.get("summary"))
    for key in ["skills", "projects", "education", "experience", "target_roles", "keywords"]:
        value = profile.get(key, [])
        if isinstance(value, str):
            value = [value]
        normalized[key] = dedupe_list(value if isinstance(value, list) else [])
    return normalized


def _resume_fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}"
