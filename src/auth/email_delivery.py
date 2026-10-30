# Secure transactional email delivery for Nairobi FloodWatch.
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote

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

def _build_link(path, token):
    #Build recovery links from the trusted configured application URL.

    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", token):
        raise ValueError("Invalid email token.")

    base_url = get_auth_settings().base_url

    # Fragment tokens are not sent in the initial HTTP GET request.
    return f"{base_url}{path}#token={quote(token, safe='')}"

def _send_email(recipient, subject, body):
    settings = EmailSettings.from_environment()
    recipient = normalize_email(recipient)

    message = EmailMessage()
    message["From"] = f"Nairobi FloodWatch <{settings.sender}>"
    message["To"] = recipient
    message["Subject"] = subject
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)
    context = ssl.create_default_context()

    try:
        if settings.security == "ssl":
            with smtplib.SMTP_SSL(settings.host, settings.port, timeout=15, context=context) as smtp:
                smtp.login(settings.username, settings.password)
                smtp.send_message(message)

        else:
            with smtplib.SMTP(settings.host, settings.port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(settings.username, settings.password)
                smtp.send_message(message)

    except (smtplib.SMTPException, OSError) as exc:
        raise EmailDeliveryError("FloodWatch could not deliver the email.") from None

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

