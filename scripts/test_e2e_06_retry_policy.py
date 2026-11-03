import sys
from unittest.mock import Mock, patch
from requests.exceptions import Timeout

from src.acquisition.open_meteo import OpenMeteoClient

def response(status, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    result = Mock(status_code=status, headers=headers)
    result.json.return_value = {"test": "success"}
    return result

def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}")

def test_scenario(name, responses, expected_calls, expected_sleeps, expected_success):
    client = OpenMeteoClient(max_attempts=4)

    try:
        with patch.object(client.session, "get", side_effect=responses) as get:
            with patch("src.acquisition.open_meteo.time.sleep") as sleep:
                succeeded = False

                try:
                    result = client._request("https://example.invalid/test", {})
                    succeeded = result == {"test": "success"}
                except RuntimeError:
                    pass

                check(succeeded == expected_success, f"{name}: expected outcome")
                check(get.call_count == expected_calls, f"{name}: request count")
                check(sleep.call_count == expected_sleeps, f"{name}: retry delays")
    finally:
        client.session.close()

def main():
    print("=" * 50)
    print("NAIROBI FLOODWATCH — RETRY POLICY")
    print("=" * 50)
    print("Mode: MOCKED HTTP REQUESTS")

    scenarios = [
        ("429 WITHOUT RETRY-AFTER", [response(429)], 1, 0, False),
        ("429 WITH SHORT RETRY-AFTER", [response(429, 1), response(200)], 2, 1, True),
        ("429 WITH LONG RETRY-AFTER", [response(429, 90)], 1, 0, False),
        ("503 THEN SUCCESS", [response(503), response(200)], 2, 1, True),
        ("TIMEOUT THEN SUCCESS", [Timeout("Synthetic timeout"), response(200)], 2, 1, True),
        ("400 BAD REQUEST", [response(400)], 1, 0, False),
    ]

    for scenario in scenarios:
        test_scenario(*scenario)

    print("\n" + "=" * 50)
    print(f"Scenarios passed: {len(scenarios)}/{len(scenarios)}")
    print("RETRY POLICY RESULT: PASS")
    print("=" * 50)
    print("No live Open-Meteo requests sent.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nRETRY POLICY RESULT: FAIL")
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)
