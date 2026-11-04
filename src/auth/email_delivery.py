# Secure transactional email delivery for Nairobi FloodWatch.
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote
import requests

from src.auth.config import get_auth_settings
from src.auth.password_service import normalize_email

class EmailConfigurationError(RuntimeError):
    pass

class EmailDeliveryError(RuntimeError):
    pass

@dataclass(frozen=True)
class EmailSettings:
    host: str
    port: int
    security: str
    username: str
    password: str
    sender: str

    @classmethod
    def from_environment(cls):
        host = os.getenv("FLOODWATCH_SMTP_HOST", "").strip()
        security = os.getenv("FLOODWATCH_SMTP_SECURITY", "starttls").strip().lower()
        username = os.getenv("FLOODWATCH_SMTP_USERNAME", "").strip()
        password = os.getenv("FLOODWATCH_SMTP_PASSWORD", "").strip()
        sender = os.getenv("FLOODWATCH_MAIL_FROM", "").strip()

        if security not in {"ssl", "starttls"}:
            raise EmailConfigurationError("SMTP security must be 'ssl' or 'starttls'.")

        default_port = 465 if security == "ssl" else 587

        try:
            port = int(os.getenv("FLOODWATCH_SMTP_PORT", str(default_port)))
        except ValueError as exc:
            raise EmailConfigurationError("Invalid SMTP port.") from exc

        if not 1 <= port <= 65535:
            raise EmailConfigurationError("SMTP port is out of range.")

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host):
            raise EmailConfigurationError("A valid SMTP hostname is required.")

        if not username or not password or not sender:
            raise EmailConfigurationError("SMTP username, password and sender must be configured.")

        if any(char in username for char in "\r\n"):
            raise EmailConfigurationError("Invalid SMTP username.")

        try:
            sender = normalize_email(sender)
        except ValueError as exc:
            raise EmailConfigurationError("Invalid sender email address.") from exc

        return cls(host=host, port=port, security=security, username=username, password=password, sender=sender)

def ensure_email_configured():
    api_key = os.getenv("FLOODWATCH_BREVO_API_KEY", "").strip()

    if api_key:
        sender = os.getenv("FLOODWATCH_MAIL_FROM", "").strip()

        try:
            normalize_email(sender)
        except ValueError as exc:
            raise EmailConfigurationError("A valid FloodWatch sender email is required.") from exc
        return True

    if get_auth_settings().environment == "production":
        raise EmailConfigurationError("Production transactional email is not configured.")
    EmailSettings.from_environment()
    return True

def _build_link(path, token):
    #Build recovery links from the trusted configured application URL.

    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", token):
        raise ValueError("Invalid email token.")

    base_url = get_auth_settings().base_url

    # Fragment tokens are not sent in the initial HTTP GET request.
    return f"{base_url}{path}#token={quote(token, safe='')}"


def _send_email(recipient, subject, body):
    recipient = normalize_email(recipient)
    api_key = os.getenv("FLOODWATCH_BREVO_API_KEY", "").strip()

    if api_key:
        sender = os.getenv("FLOODWATCH_MAIL_FROM", "").strip()

        try:
            sender = normalize_email(sender)
        except ValueError as exc:
            raise EmailConfigurationError(
                "A valid FLOODWATCH_MAIL_FROM is required."
            ) from exc

        try:
            response = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": api_key,
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                json={
                    "sender": {
                        "name": "Nairobi FloodWatch",
                        "email": sender,
                    },
                    "to": [{"email": recipient}],
                    "subject": subject,
                    "textContent": body,
                },
                timeout=(5, 15),
            )
        except requests.RequestException:
            raise EmailDeliveryError(
                "FloodWatch could not deliver the email."
            ) from None

        if response.status_code != 201:
            raise EmailDeliveryError(
                f"Email provider rejected the message (HTTP {response.status_code})."
            )

        return True

    if get_auth_settings().environment == "production":
        raise EmailConfigurationError(
            "Production email API is not configured."
        )

    settings = EmailSettings.from_environment()
    message = EmailMessage()
    message["From"] = f"Nairobi FloodWatch <{settings.sender}>"
    message["To"] = recipient
    message["Subject"] = subject
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)
    context = ssl.create_default_context()

    try:
        if settings.security == "ssl":
            with smtplib.SMTP_SSL(
                settings.host, settings.port,
                timeout=15, context=context
            ) as smtp:
                smtp.login(settings.username, settings.password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(
                settings.host, settings.port, timeout=15
            ) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(settings.username, settings.password)
                smtp.send_message(message)

    except (smtplib.SMTPException, OSError):
        raise EmailDeliveryError(
            "FloodWatch could not deliver the email."
        ) from None

    return True


def send_verification_email(recipient, token):
    link = _build_link("/auth/verify-email", token)
    body = (
        "Welcome to Nairobi FloodWatch!\n\n"
        "Please verify your email address using this link:\n\n"
        f"{link}\n\n"
        "This link expires after 24 hours and can only be used once.\n\n"
        "If you did not create a FloodWatch account, "
        "you can ignore this message.\n\n"
        "Nairobi FloodWatch\n"
        "Academic Flood Monitoring Prototype"
    )

    return _send_email(recipient, "Verify your Nairobi FloodWatch email", body)

def send_password_reset_email(recipient, token):
    link = _build_link("/auth/reset-password", token)
    body = (
        "Nairobi FloodWatch Password Recovery\n\n"
        "A password reset was requested for your account.\n\n"
        "To choose a new password, visit:\n\n"
        f"{link}\n\n"
        "This link expires after 15 minutes and can only be used once.\n\n"
        "If you did not request a password reset, "
        "you can ignore this message.\n\n"
        "Nairobi FloodWatch"
    )

    return _send_email(recipient, "Reset your Nairobi FloodWatch password", body)


def send_password_change_notice(recipient):
    body = (
        "Nairobi FloodWatch Security Notification\n\n"
        "Your account password was successfully changed.\n\n"
        "Existing FloodWatch sessions have been revoked.\n\n"
        "If you did not make this change, "
        "contact the system administrator through "
        "an established support channel.\n\n"
        "Nairobi FloodWatch"
    )

    return _send_email( recipient, "Your Nairobi FloodWatch password was changed", body)


# RESIDENT EMAIL OTP DELIVERY
def send_resident_verification_otp(recipient, code):

    #Deliver a single-use, six-digit email OTP.
    #This code confirms email ownership only.
    #It does not satisfy Agency/Admin MFA.

    if (not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code)):
        raise ValueError("Invalid verification code.")

    body = (
        "Nairobi FloodWatch - Email Verification\n\n"
        "Thank you for registering with Nairobi FloodWatch.\n\n"
        "Your email verification code is:\n\n"
        f"{code}\n\n"
        "This code expires in 10 minutes and can only "
        "be used once.\n\n"
        "You can continue viewing public flood monitoring "
        "information while verification is pending.\n\n"
        "If you did not create this account, "
        "you can ignore this message.\n\n"
        "Nairobi FloodWatch\n"
        "Academic Flood Monitoring Prototype"
    )

    return _send_email(recipient, "Your Nairobi FloodWatch verification code", body)

def send_agency_decision_email(recipient, applicant_name, organization_name, decision):
    #Notify an applicant of an agency access decision.
    #Uses the existing FloodWatch SMTP infrastructure.
    #Never includes passwords or session tokens.
   
    if decision not in {"approved", "rejected"}:
        raise ValueError("Invalid agency application decision.")

    name = " ".join(str(applicant_name or "").split())[:120]
    organization = " ".join(str(organization_name or "").split())[:200]
    name = name or "Applicant"
    organization = organization or "your organization"
    base_url = get_auth_settings().base_url.rstrip("/")

    if decision == "approved":
        subject = "Nairobi FloodWatch - Agency Access Approved"
        body = (
            f"Hello {name},\n\n"
            "Your request for agency access to Nairobi FloodWatch "
            "has been approved.\n\n"
            f"Organization: {organization}\n\n"
            "You can now sign in using your existing account. "
            "Additional security verification may be required "
            "before accessing the Agency Dashboard.\n\n"
            f"Sign in: {base_url}/agency/entry\n\n"
            "You do not need to create another account.\n\n"
            "Nairobi FloodWatch"
        )

    else:
        subject = "Nairobi FloodWatch - Agency Access Request Update"
        body = (
            f"Hello {name},\n\n"
            "Your request for agency access to Nairobi FloodWatch "
            "has been reviewed and was not approved.\n\n"
            f"Organization: {organization}\n\n"
            "Your existing account remains subject to its "
            "current permissions. No agency privileges "
            "have been granted.\n\n"
            "If you believe this decision was made in error, "
            "please contact the FloodWatch administrator "
            "through the application's official contact channel.\n\n"
            "Nairobi FloodWatch"
        )

    return _send_email(recipient, subject, body)

