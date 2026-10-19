from html.parser import HTMLParser
from fastapi.testclient import TestClient
from app import app

# These elements connect the HTML interface to our existing JavaScript.
REQUIRED_IDS = {
    "main",
    "freshness",
    "useLocation",
    "chooseArea",
    "clearLocation",
    "areaSearch",
    "riskFilter",
    "areaTitle",
    "areaRisk",
    "areaScore",
    "assessmentTime",
    "temperature",
    "humidity",
    "rainfall",
    "residentMap",
    "nearbyList",
    "advisoryStatus",
    "riskHistoryChart",
    "weatherHistoryChart",
    "downloadHistory",
    "geoSearchPanel",
    "geoSearchButton",
    "geoSearchLoading",
    "geoMatches",
    "geoContext",
}


class HTMLInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.duplicate_ids = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get("id")

        if element_id:
            if element_id in self.ids:
                self.duplicate_ids.add(element_id)

            self.ids.add(element_id)


def main():
    print("Nairobi FloodWatch Resident UI Regression Test")
    print("=" * 50)

    with TestClient(app) as client:
        # Confirm the redesigned resident HTML is available.
        response = client.get("/resident")

        assert response.status_code == 200, response.text

        html = response.text
        inspector = HTMLInspector()
        inspector.feed(html)

        print("PASS: Resident HTML served")

        # Existing JavaScript expects these exact HTML identifiers.
        missing = REQUIRED_IDS - inspector.ids

        assert not missing, f"Missing interface IDs: {sorted(missing)}"
        assert not inspector.duplicate_ids, (f"Duplicate HTML IDs: {sorted(inspector.duplicate_ids)}")

        print("PASS: Required interactive elements preserved")

        # Verify the custom stylesheet and JavaScript are accessible.
        resources = (
            "/static/css/resident.css",
            "/static/js/resident.js",
            "/static/js/place_search.js",
            "/static/images/logo.png",
        )

        for path in resources:
            resource = client.get(path)

            assert resource.status_code == 200, (f"Unavailable resource: {path}")

        print("PASS: Stylesheet, scripts and branding available")

        # Check that essential design and accessibility rules remain.
        stylesheet = client.get("/static/css/resident.css" ).text

        for selector in (
            ":focus-visible",
            ".sidebar",
            ".page-heading",
            ".location-prompt",
            ".area-card",
            ".map-card",
            ".geo-search-loading",
            ".history-grid",
            "prefers-reduced-motion",
        ):
            assert selector in stylesheet, (f"Missing required CSS rule: {selector}")

        print("PASS: Core layout and accessibility styles present")

        assert 'href="#main"' in html
        assert 'id="main"' in html
        assert 'href="/static/images/logo.png"' in html

        print("PASS: Skip navigation and logo favicon preserved")

    print()
    print("Resident UI regression tests: ALL PASSED")


if __name__ == "__main__":
    main()
