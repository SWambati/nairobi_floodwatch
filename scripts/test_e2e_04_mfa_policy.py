import os
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from starlette.requests import Request

import src.auth.authorization as authorization

class FakeEngine:
    def connect(self):
        return nullcontext(object())

def decision(allowed=False, reason=None, setup_required=False):
    return SimpleNamespace(allowed=allowed, reason=reason, setup_required=setup_required)

def test_case(name, environment, result, expected_status, expected_evaluation, error=None):
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/admin/health",
        "headers": [],
    })

    user = {
        "id": "synthetic-test-user",
        "role": "admin",
        "account_status": "active",
        "email_verified": True,
    }

    with patch.dict(os.environ, environment):
        with patch.object(authorization, "_engine", return_value=FakeEngine()):
            with patch.object(authorization, "evaluate_request_mfa", return_value=result, side_effect=error) as evaluator:
                try:
                    authorization._authorize_mfa(request, user)
                    actual_status = 200

                except HTTPException as exc:
                    actual_status = exc.status_code

                evaluated = evaluator.call_count

    passed = (actual_status == expected_status and evaluated == expected_evaluation)
    status = "PASS" if passed else "FAIL"

    print(f"[{status}] {name}: " f"HTTP {actual_status}, " f"MFA checks={evaluated}")

    return passed

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-04E")
    print("=" * 50)
    print("Mode: ISOLATED MFA POLICY TEST")

    development_off = {
        "FLOODWATCH_ENV": "development",
        "FLOODWATCH_MFA_ENFORCEMENT": "false",
    }

    development_on = {
        "FLOODWATCH_ENV": "development",
        "FLOODWATCH_MFA_ENFORCEMENT": "true",
    }

    production = {
        "FLOODWATCH_ENV": "production",
        "FLOODWATCH_MFA_ENFORCEMENT": "false",
    }

    cases = [
        ("Development opt-out", development_off, None, 200, 0, None),
        ("Development MFA enabled", development_on, decision(allowed=True), 200, 1, None),
        ("Production requires MFA", production, decision(allowed=True), 200, 1, None),
        ("Incomplete MFA challenge rejected", production, decision(reason="challenge_required"), 403, 1, None),
        ("Missing MFA setup rejected", production, decision(setup_required=True), 403, 1, None),
        ("Invalid session rejected", production, decision(reason="invalid_session"), 401, 1, None),
        ("Inactive account rejected", production, decision(reason="inactive_account"), 401, 1, None),
        ("MFA service failure denied", production, None, 503, 1, RuntimeError("Simulated MFA service error"))
    ]

    passed = sum(test_case(*case) for case in cases)
    total = len(cases)

    print("\n" + "=" * 50)
    print(f"Tests passed: {passed}/{total}")
    print(f"Tests failed: {total - passed}")

    if passed == total:
        print("E2E-04E RESULT: PASS")
        print("MFA enforcement and fail-closed behavior verified.")
        print("No real MFA challenges or database records were modified.")
        return 0

    print("E2E-04E RESULT: FAIL")
    return 1

if __name__ == "__main__":
    sys.exit(main())
