"""
EmailSender — sends the MoM as a formatted HTML email after the meeting ends.
Supports Gmail (App Password) and Office 365 / Outlook SMTP.
Configure via .env — if SMTP settings are missing the class silently skips.
"""

import logging
import smtplib
from datetime import date
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from config import (
    EMAIL_FROM,
    EMAIL_TO,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
)

logger = logging.getLogger(__name__)


def _build_html(mom_text: str, meeting_title: str) -> str:
    """Wrap MoM plain-text in a clean branded HTML email."""
    safe = (
        mom_text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    today = date.today().strftime("%B %d, %Y")
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:24px 12px">
      <table width="660" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:8px;
                    box-shadow:0 2px 8px rgba(0,0,0,.12)">

        <!-- Header -->
        <tr>
          <td style="background:#003087;padding:22px 30px;border-radius:8px 8px 0 0">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <h1 style="margin:0;color:#fff;font-size:20px;font-weight:700">
                    Protiviti AI Scribe
                  </h1>
                  <p style="margin:4px 0 0;color:#a8c4e8;font-size:13px">
                    Minutes of Meeting &mdash; {meeting_title}
                  </p>
                </td>
                <td align="right">
                  <span style="color:#a8c4e8;font-size:12px">{today}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:28px 30px">
            <pre style="font-family:Arial,sans-serif;font-size:13.5px;
                        line-height:1.75;white-space:pre-wrap;
                        margin:0;color:#1a1a1a">{safe}</pre>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f0f4f8;padding:14px 30px;
                     border-radius:0 0 8px 8px;
                     border-top:1px solid #e2e8f0">
            <p style="margin:0;font-size:11px;color:#8a9ab0">
              Generated automatically by <strong>Protiviti AI Scribe</strong>
              &middot; Powered by Gemini 2.5 Flash (free tier)
              &middot; The .md file is attached for your records.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


class EmailSender:
    def __init__(self) -> None:
        self.configured = bool(SMTP_USER and SMTP_PASSWORD and EMAIL_TO)
        if not self.configured:
            logger.info(
                "Email delivery not configured — set SMTP_USER, SMTP_PASSWORD, "
                "and EMAIL_TO in .env to enable it."
            )

    # ------------------------------------------------------------------

    def send_mom(
        self,
        mom_text: str,
        mom_path: str,
        meeting_title: str = "Teams Meeting",
        participants: Optional[list[str]] = None,
    ) -> bool:
        """Send the MoM email with the .md file attached. Returns True on success."""
        if not self.configured:
            return False

        recipients = [r.strip() for r in EMAIL_TO.split(",") if r.strip()]
        if not recipients:
            logger.warning("EMAIL_TO is empty — skipping email delivery")
            return False

        today = date.today().strftime("%B %d, %Y")
        subject = f"MoM: {meeting_title} — {today}"

        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM or SMTP_USER
        msg["To"] = ", ".join(recipients)

        # Multipart/alternative for plain + HTML
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(mom_text, "plain", "utf-8"))
        alt.attach(MIMEText(_build_html(mom_text, meeting_title), "html", "utf-8"))
        msg.attach(alt)

        # Attach the raw .md file
        mom_file = Path(mom_path)
        if mom_file.exists():
            part = MIMEBase("application", "octet-stream")
            part.set_payload(mom_file.read_bytes())
            encoders.encode_base64(part)
            safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in meeting_title)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="MoM_{safe_title}_{today}.md"',
            )
            msg.attach(part)

        try:
            logger.info("Connecting to %s:%d …", SMTP_HOST, SMTP_PORT)
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.sendmail(msg["From"], recipients, msg.as_string())
            logger.info("MoM email sent to: %s", ", ".join(recipients))
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error(
                "SMTP authentication failed. "
                "For Gmail use an App Password (not your account password). "
                "See: https://myaccount.google.com/apppasswords"
            )
        except Exception as exc:
            logger.error("Email send failed: %s", exc)
        return False
