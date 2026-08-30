import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from jinja2 import Environment, FileSystemLoader

from app.config import get_settings
from app.database import db, now_iso
from app.logging_config import logger

_jinja_env = Environment(loader=FileSystemLoader("templates"), autoescape=True)


async def send_email(to: str, subject: str, template: str, context: dict) -> bool:
    settings = get_settings()
    if not settings.SMTP_HOST:
        logger.info("smtp_disabled", to=to, subject=subject)
        await _log_email(to, subject, template, "skipped", "SMTP not configured")
        return False

    try:
        html = _jinja_env.get_template(f"emails/{template}.html").render(**context)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to
        msg.attach(MIMEText(html, "html"))

        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASS)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASS)
                server.send_message(msg)

        await _log_email(to, subject, template, "sent", None)
        return True
    except Exception as e:
        logger.exception("email_failed", to=to, template=template)
        await _log_email(to, subject, template, "failed", str(e))
        return False


async def _log_email(to: str, subject: str, template: str, status: str, error: str | None):
    await db["email_logs"].insert_one({
        "to": to,
        "subject": subject,
        "template": template,
        "status": status,
        "error": error,
        "created_at": now_iso(),
    })
