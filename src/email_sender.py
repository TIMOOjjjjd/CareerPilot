from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jinja2 import Environment, FileSystemLoader, select_autoescape

from utils import normalize_email_address

logger = logging.getLogger(__name__)


def send_job_email(
    jobs: list[dict[str, Any]],
    config: dict[str, Any],
    sender_email: str,
    app_password: str,
    template_dir: Path,
) -> bool:
    if not jobs:
        logger.info("No unseen scored jobs to email")
        return False

    subject = config["notifications"].get("email_subject", "Daily AI Job Matches")
    recipient = normalize_email_address(config["profile"]["email"])
    html = render_email(jobs, config, template_dir)

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = sender_email
    message["To"] = recipient
    message.attach(MIMEText(_plain_text_summary(jobs), "plain", "utf-8"))
    message.attach(MIMEText(html, "html", "utf-8"))

    logger.info("Sending email with %s jobs to %s", len(jobs), recipient)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(sender_email, app_password)
        smtp.sendmail(sender_email, [recipient], message.as_string())

    return True


def render_email(
    jobs: list[dict[str, Any]],
    config: dict[str, Any],
    template_dir: Path,
) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("email_template.html")
    subject = config["notifications"].get("email_subject", "Daily AI Job Matches")
    timezone_name = config["notifications"].get("timezone", "Europe/London")
    try:
        run_date = datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M %Z")
    except ZoneInfoNotFoundError:
        run_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    return template.render(
        subject=subject,
        run_date=run_date,
        jobs=jobs,
    )


def _plain_text_summary(jobs: list[dict[str, Any]]) -> str:
    lines = ["Daily AI Job Matches", ""]
    for index, job in enumerate(jobs, start=1):
        lines.append(
            f"{index}. {job.get('title')} at {job.get('company')} "
            f"({job.get('location')}) - {job.get('score')}/100"
        )
        if job.get("apply_url"):
            lines.append(str(job["apply_url"]))
        lines.append("")
    return "\n".join(lines)
