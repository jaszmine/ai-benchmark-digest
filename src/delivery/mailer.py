import resend
from src.config import get_settings

settings = get_settings()


def send_digest_email(html_content: str, subject_prefix: str = "AI Benchmark Digest") -> dict:
    resend.api_key = settings.resend_api_key

    params = {
        "from": settings.notification_email_from,
        "to": [settings.notification_email_to],
        "subject": f"[{subject_prefix}] Track A vs Track B Telemetry",
        "html": html_content,
    }

    try:
        response = resend.Emails.send(params)
        return {"status": "success", "id": response.get("id")}
    except Exception as e:
        print(f"[Mailer Error] Dispatch failed: {e}")
        return {"status": "error", "error": str(e)}
