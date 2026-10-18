
const state = {
    map: null,
    markerLayer: null,
    locations: [],
    selectedId: null,
};

const RISK_COLORS = {
    LOW: "#16855B",
    MODERATE: "#D99516",
    HIGH: "#D63D45",
    NO_DATA: "#8796A9",
};

function element(id) {
    return document.getElementById(id);
}

function setText(id, value) {
    // textContent prevents database values from being interpreted as HTML.
    element(id).textContent = value;
}

function riskLevel(location) {
    return String(location.risk_level || "NO_DATA").toUpperCase();
}

function formatNumber(value, decimals = 1, suffix = "") {
    if (value === null || value === undefined) {
        return "Not recorded";
    }

    const number = Number(value);

    if (!Number.isFinite(number)) {
        return "Not recorded";
    }

    return `${number.toFixed(decimals)}${suffix}`;
}

function formatTime(value) {
    if (!value) return "Not recorded";

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
        return "Invalid timestamp";
    }

    return new Intl.DateTimeFormat("en-KE", {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Africa/Nairobi",
    }).format(date) + " EAT";
}

async function fetchJSON(url) {
    const response = await fetch(url, {
        headers: { Accept: "application/json" },
        cache: "no-store",
    });

    if (!response.ok) {
        throw new Error(`API request failed: ${url} (${response.status})`);
    }

    return await response.json();
}

// GIS map initialization
function initializeMap() {
    if (typeof L === "undefined") {
        throw new Error("Leaflet could not load. Check the internet connection.");
    }

    // Nairobi center; actual monitoring markers come from PostgreSQL.
    state.map = L.map("map").setView([-1.286389, 36.817223], 11);

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    }).addTo(state.map);

    state.markerLayer = L.layerGroup().addTo(state.map);
}

// Dashboard metrics
function renderOverview(overview) {
    setText("statLocations", overview.monitored_locations);
    setText("statLow", overview.low);
    setText("statModerate", overview.moderate);
    setText("statHigh", overview.high);
    setText("statAlerts", overview.active_advisories);

    if (!overview.latest_prediction) {
        setText("statusText", "Connected · No assessments stored yet");
        return;
    }

    const predictionDate = new Date(overview.latest_prediction);
    const ageHours = (Date.now() - predictionDate.getTime()) / 3600000;
    const timestamp = formatTime(overview.latest_prediction);

    // Never present an old database assessment as a live warning.
    if (ageHours > 24) {
        setText("statusText", `Historical snapshot · Latest assessment: ${timestamp}`);
    } else {
        setText("statusText", `Connected · Latest assessment: ${timestamp}`);
    }
}

// Display the selected location's real database values.
function displayLocation(location) {
    state.selectedId = Number(location.location_id);

    element("detailPlaceholder").hidden = true;
    element("detailContent").hidden = false;

    const risk = riskLevel(location);

    setText("detailName", location.name);
    setText("detailLocationId", `Observation point #${location.location_id}`);
    setText("detailRisk", risk === "NO_DATA" ? "NOT ASSESSED" : `${risk} RISK`);
    element("detailRisk").className = `risk-badge ${risk}`;

    setText("detailScore", formatNumber(location.risk_score, 4));
    setText("detailPredictionTime", formatTime(location.prediction_time));
    setText("detailWeatherTime", formatTime(location.weather_timestamp));
    setText("detailTemperature", formatNumber(location.temperature, 1, " °C"));
    setText("detailHumidity", formatNumber(location.humidity, 1, " %"));
    setText("detailRainfall", formatNumber(location.rainfall, 1, " mm"));
    setText("detailHazard", formatNumber(location.flood_hazard_score, 3));

    if (location.alert_title) {
        setText("detailAlertTitle", location.alert_title);
        setText("detailAlertMessage", location.alert_message || "");
        setText("detailAlertInstructions", location.alert_instructions || "");
    } else {
        setText("detailAlertTitle", "No currently active advisory");
        setText("detailAlertMessage", "No valid model-generated advisory is associated with this assessment.");
        setText("detailAlertInstructions", "");
    }
}

function clearLocationDetails() {
    state.selectedId = null;
    element("detailPlaceholder").hidden = false;
    element("detailContent").hidden = true;
}

// Draw monitoring points and apply current search/risk filters.
function renderMapMarkers() {
    const search = element("locationSearch").value.trim().toLowerCase();
    const selectedRisk = element("riskFilter").value;

    const visible = state.locations.filter((location) => {
        const name = String(location.name || "").toLowerCase();
        const id = String(location.location_id);
        const matchesSearch = name.includes(search) || id.includes(search);
        const matchesRisk = selectedRisk === "ALL" || riskLevel(location) === selectedRisk;

        return matchesSearch && matchesRisk;
    });

    setText("visibleCount", `${visible.length} points`);

    if (state.markerLayer) {
        state.markerLayer.clearLayers();

        for (const location of visible) {
            const latitude = Number(location.latitude);
            const longitude = Number(location.longitude);

            if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
                continue;
            }

            const risk = riskLevel(location);

            const marker = L.circleMarker([latitude, longitude], {
                radius: 8,
                color: "#FFFFFF",
                weight: 2,
                fillColor: RISK_COLORS[risk] || RISK_COLORS.NO_DATA,
                fillOpacity: 0.9,
            }).addTo(state.markerLayer);

            // Use a text node to avoid interpreting location names as markup.
            const tooltip = document.createElement("span");
            tooltip.textContent = `${location.name} · ${risk}`;

            marker.bindTooltip(tooltip);

            marker.on("click", () => {
                displayLocation(location);
            });
        }
    }

    // Keep the details consistent with the currently visible filter.
    if (state.selectedId !== null) {
        const selected = visible.find(
            (location) => Number(location.location_id) === state.selectedId
        );

        if (selected) {
            displayLocation(selected);
        } else {
            clearLocationDetails();
        }
    }
}

// Fetch actual stored data. Refresh does not trigger model inference.
async function loadDashboard() {
    const refreshButton = element("refreshButton");
    refreshButton.disabled = true;
    element("errorBanner").hidden = true;

    setText("statusText", "Loading monitoring records from PostgreSQL...");

    try {
        const [overview, locations] = await Promise.all([
            fetchJSON("/api/overview"),
            fetchJSON("/api/locations"),
        ]);

        if (!Array.isArray(locations)) {
            throw new Error("The location API did not return a valid list.");
        }

        state.locations = locations;

        renderOverview(overview);
        renderMapMarkers();
    } catch (error) {
        console.error("FloodWatch dashboard error:", error);

        setText("statusText", "Monitoring data unavailable");
        setText(
            "errorBanner",
            "Unable to retrieve current FloodWatch records. Check the API and PostgreSQL connection."
        );

        element("errorBanner").hidden = false;
    } finally {
        refreshButton.disabled = false;
    }
}

// Initialize event handlers after the interface has loaded.
document.addEventListener("DOMContentLoaded", () => {
    element("locationSearch").addEventListener("input", renderMapMarkers);
    element("riskFilter").addEventListener("change", renderMapMarkers);
    element("refreshButton").addEventListener("click", loadDashboard);

    try {
        initializeMap();
    } catch (error) {
        console.error(error);
        setText("map", "Map unavailable. Check the Leaflet connection.");
    }

    loadDashboard();
});
