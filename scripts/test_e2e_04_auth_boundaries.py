import os
import sys
import requests

BASE_URL = os.environ.get("FLOODWATCH_BASE_URL", "http://127.0.0.1:8010").rstrip("/")

# Verified public endpoints.
PUBLIC_ENDPOINTS = ("/", "/resident", "/api/overview", "/api/locations")

# Protected dashboard entry points should redirect an anonymous visitor to authentication.
PROTECTED_PAGES = ("/agency", "/admin")

# Protected administrative APIs should reject unauthenticated requests.
PROTECTED_APIS = ("/api/admin/health", "/api/admin/overview", "/api/admin/runs")

def check(condition, description):
    if not condition:
        raise AssertionError(description)

    print(f"[PASS] {description}", flush=True)

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-04A")
    print("=" * 50)
    print("Mode: ANONYMOUS / READ-ONLY")
    print("Server:", BASE_URL)

    with requests.Session() as session:
        check(len(session.cookies) == 0, "Test session starts without authentication cookies")

        print("\n--- PUBLIC ENDPOINTS ---")
        for path in PUBLIC_ENDPOINTS:
            response = session.get(BASE_URL + path, timeout=15, allow_redirects=False)
            check(response.status_code == 200, f"{path}: publicly accessible (HTTP 200)")

        print("\n--- PROTECTED DASHBOARD PAGES ---")
        for path in PROTECTED_PAGES:
            response = session.get(BASE_URL + path, timeout=15, allow_redirects=False)
            check(response.status_code in (302, 303, 307, 308) and bool(response.headers.get("Location")), f"{path}: anonymous visitor redirected " f"(HTTP {response.status_code})")

        print("\n--- PROTECTED ADMINISTRATIVE APIs ---")
        for path in PROTECTED_APIS:
            response = session.get(BASE_URL + path, timeout=15, allow_redirects=False)
            check(response.status_code in (401, 403), f"{path}: anonymous access denied " f"(HTTP {response.status_code})")

        check(len(session.cookies) == 0, "Anonymous requests did not establish a login session")

    print("\n" + "=" * 50)
    print("E2E-04A RESULT: PASS")
    print("=" * 50)
    print("Public and protected access boundaries verified.")
    print("No database writes were performed.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nE2E-04A RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)
