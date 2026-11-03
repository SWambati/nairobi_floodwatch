import importlib
import sys
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from starlette.requests import Request

import src.auth

BASE_URL = "http://127.0.0.1:8010"

class FakeEngine:
    def begin(self):
        return nullcontext(object())

def find_auth_module():
    #Find the existing local authentication module.
    directory = Path(src.auth.__file__).parent

    for path in directory.glob("*.py"):
        source = path.read_text(encoding="utf-8")

        if ("def require_same_origin_json(" in source and "def _throttle(" in source):
            return importlib.import_module(f"src.auth.{path.stem}")
    raise RuntimeError("Could not locate local authentication functions.")

def make_request(origin=BASE_URL,
                 content_type="application/json"):
    headers = []

    if origin is not None:
        headers.append((b"origin", origin.encode()))

    if content_type is not None:
        headers.append((b"content-type", content_type.encode()))

    return Request({
        "type": "http",
        "method": "POST",
        "path": "/auth/local/login",
        "headers": headers,
        "client": ("127.0.0.1", 45000),
        "server": ("127.0.0.1", 8010),
        "scheme": "http",
        "query_string": b""
    })

def check(condition, message):
    if not condition:
        raise AssertionError(message)

    print(f"[PASS] {message}", flush=True)

def status_of(callback):
    try:
        callback()
        return 200
    except HTTPException as exc:
        return exc.status_code

def main():
    auth = find_auth_module()

    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-04H")
    print("=" * 50)
    print("Mode: ISOLATED AUTHENTICATION SECURITY")
    print("Module:", auth.__name__)

    fake_settings = SimpleNamespace(base_url=BASE_URL, oauth_session_secret="synthetic-test-secret")

    with patch.object(auth, "get_auth_settings", return_value=fake_settings):
        print("\n--- REQUEST VALIDATION ---")
        cases = [
            ("Correct origin and JSON accepted", make_request(), 200),
            ("Foreign origin rejected", make_request(origin="https://untrusted.example"), 403),
            ("Missing origin rejected", make_request(origin=None), 403),
            ("Non-JSON content rejected", make_request(content_type="text/plain"), 415),
            ("JSON with charset accepted", make_request(content_type=("application/json; charset=utf-8")), 200)
        ]

        for name, request, expected in cases:
            actual = status_of(lambda r=request: auth.require_same_origin_json(r))
            check(actual == expected, f"{name}: HTTP {actual}")

        print("\n--- LOGIN RATE LIMITS ---")
        counters = defaultdict(int)

        def fake_increment(connection, bucket_hash, limit):
            counters[bucket_hash] += 1
            return counters[bucket_hash] <= limit

        with patch.object(auth, "_engine", return_value=FakeEngine()), patch.object(auth, "_increment_bucket", side_effect=fake_increment ):
            request = make_request()
            
            # Expected login limits from production code: 10 attempts per email; 30 per IP.
            for number in range(10):
                check(status_of(lambda: auth._throttle(request, "login", "synthetic@example.com", ip_limit=30, email_limit=10)) == 200, f"Login attempt {number + 1} permitted")

            try:
                auth._throttle(request, "login", "synthetic@example.com", ip_limit=30, email_limit=10)
                raise AssertionError("Email rate limit was not enforced.")
            except HTTPException as exc:
                check(exc.status_code == 429, "Eleventh email attempt blocked")
                check(exc.headers.get("Retry-After") == "3600", "Throttling returns Retry-After header")

            # Reset simulated counters to test IP-based throttling separately.
            counters.clear()

            for number in range(30):
                actual = status_of(lambda: auth._throttle(request, "login", ip_limit=30))
                check(actual == 200, f"IP attempt {number + 1} permitted")
            check(status_of(lambda: auth._throttle(request, "login", ip_limit=30)) == 429, "Thirty-first IP attempt blocked")

    print("\n" + "=" * 50)
    print("E2E-04H RESULT: PASS")
    print("=" * 50)
    print("Request-origin and throttling policies verified.")
    print("No real login attempts or database writes.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nE2E-04H RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)
