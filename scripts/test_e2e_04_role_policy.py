import sys
from fastapi import HTTPException

from src.auth.authorization import (_authorize_role, RESIDENT_ROLES, AGENCY_ROLES, ADMIN_ROLES)

def account(role, status="active", verified=True):
    return {
        "role": role,
        "account_status": status,
        "email_verified": verified,
    }

def authorize(user, allowed_roles):
    try:
        _authorize_role(user, allowed_roles)
        return 200
    except HTTPException as exc:
        return exc.status_code

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — E2E-04B")
    print("=" * 50)

    resident = account("resident")
    agency = account("agency")
    admin = account("admin")

    cases = [
        # Resident access
        ("Resident -> Resident", resident, RESIDENT_ROLES, 200),
        ("Resident -> Agency", resident, AGENCY_ROLES, 403),
        ("Resident -> Admin", resident, ADMIN_ROLES, 403),

        # Agency access
        ("Agency -> Resident", agency, RESIDENT_ROLES, 403),
        ("Agency -> Agency", agency, AGENCY_ROLES, 200),
        ("Agency -> Admin", agency, ADMIN_ROLES, 403),

        # Admin access
        ("Admin -> Resident", admin, RESIDENT_ROLES, 403),
        ("Admin -> Agency", admin, AGENCY_ROLES, 200),
        ("Admin -> Admin", admin, ADMIN_ROLES, 200),

        # Account status protection
        ("Inactive Resident denied", account("resident", status="inactive"), RESIDENT_ROLES, 401),
        ("Inactive Admin denied", account("admin", status="inactive"), ADMIN_ROLES, 401),

        # Privileged email verification
        ("Unverified Agency denied", account("agency", verified=False), AGENCY_ROLES, 403),
        ("Unverified Admin denied", account("admin", verified=False), ADMIN_ROLES, 403),

        # Resident verification is separately enforced by require_resident_dashboard_access() and require_verified_resident().
        ("Unverified Resident role accepted", account("resident", verified=False), RESIDENT_ROLES, 200),

        # Missing identity
        ("Missing authenticated user denied", None, ADMIN_ROLES, 401)
    ]

    passed = 0
    failed = 0

    for name, user, allowed_roles, expected in cases:
        actual = authorize(user, allowed_roles)
        if actual == expected:
            print(f"[PASS] {name}: " f"HTTP {actual}")
            passed += 1
        else:
            print(f"[FAIL] {name}: " f"expected HTTP {expected}, " f"got HTTP {actual}")
            failed += 1

    print("\n" + "=" * 50)
    print(f"Tests passed: {passed}/{len(cases)}")
    print(f"Tests failed: {failed}")

    if failed:
        print("E2E-04B RESULT: FAIL")
        return 1

    print("E2E-04B RESULT: PASS")
    print("=" * 50)
    print("Role authorization policy verified.")
    print("No database records were modified.")

    return 0

if __name__ == "__main__":
    sys.exit(main())
