#Environment-aware authentication configuration for Nairobi FloodWatch.

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

LOCAL_HOSTS = {"127.0.0.1", "localhost"}

@dataclass(frozen=True)
class AuthSettings:
    environment: str
    base_url: str
    google_client_id: str
    google_client_secret: str
    oauth_session_secret: str
    callback_path: str = "/auth/google/callback"
    oauth_state_max_age: int = 600
    user_session_max_age: int = 28800

    @classmethod
    def from_environment(cls):
        environment = os.getenv("FLOODWATCH_ENV", "development").strip().lower()

        if environment not in {"development", "production"}:
            raise ValueError("FLOODWATCH_ENV must be 'development' or 'production'.")

        base_url = os.getenv("FLOODWATCH_BASE_URL", "http://127.0.0.1:8010").strip().rstrip("/")
        parsed = urlsplit(base_url)

        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Application URL must use HTTP or HTTPS.")

        if not parsed.hostname or not parsed.netloc:
            raise ValueError("FLOODWATCH_BASE_URL has an invalid hostname.")

        if parsed.username or parsed.password:
            raise ValueError("Application URL must not contain credentials.")

        if parsed.path or parsed.query or parsed.fragment:
            raise ValueError("FLOODWATCH_BASE_URL must contain only the origin.")

        try:
            port = parsed.port

        except ValueError as exc:
            raise ValueError("Invalid application port.") from exc

        if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
            raise ValueError("HTTP is permitted only for local development.")

        if environment == "production":
            if parsed.scheme != "https":
                raise ValueError("Production authentication requires HTTPS.")

            if parsed.hostname in LOCAL_HOSTS:
                raise ValueError("Production requires a public HTTPS domain.")

        client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
        client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
        session_secret = os.getenv("FLOODWATCH_OAUTH_SESSION_SECRET", "").strip()

        if bool(client_id) != bool(client_secret):
            raise ValueError("Google Client ID and Client Secret must be configured together.")

        if session_secret and len(session_secret) < 32:
            raise ValueError("OAuth session secret must contain at least 32 characters.")

        return cls(environment=environment, base_url=base_url, google_client_id=client_id, google_client_secret=client_secret, oauth_session_secret=session_secret,)

    @property
    def google_redirect_uri(self):
        return f"{self.base_url}{self.callback_path}"

    @property
    def secure_cookies(self):
        return self.base_url.startswith("https://")

    @property
    def cookie_samesite(self):
        return "lax"

    @property
    def google_configured(self):
        return bool(self.google_client_id and self.google_client_secret and self.oauth_session_secret)

    def require_google_configuration(self):
        if not self.google_configured:
            raise RuntimeError(
                "Google authentication is not configured. "
                "Set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and FLOODWATCH_OAUTH_SESSION_SECRET.")

        return self

def get_auth_settings():
    return AuthSettings.from_environment()
