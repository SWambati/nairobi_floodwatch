
"use strict";

// Application state
// Store the current dashboard records, selected monitoring point,
// device location, map layers and chart instances.
const state = {
    locations: [],
    overview: null,
    selected: null,
    position: null,
    map: null,
    markers: null,
    locationMarker: null,
    accuracyCircle: null,
    riskChart: null,
    weatherChart: null,
    selectionSerial: 0,
    history: null,
};

// Risk classifications retain distinct colors for accessibility.
const colors = {
    LOW: "#16855B",
    MODERATE: "#D99A1B",
    HIGH: "#C63B44",
    NO_DATA: "#8B9BAE",
};


// General helpers
const $ = (id) => document.getElementById(id);

function write(id, value) {
    // textContent prevents database values from being interpreted as HTML.
    $(id).textContent = String(value);
}

function riskOf(point) {
    return String(point?.risk_level || "NO_DATA").toUpperCase();
}

function number(value, digits = 1, unit = "") {
    if (value === null || value === undefined || value === "") {
        return "Not recorded";
    }

    const result = Number(value);

    return Number.isFinite(result)
        ? `${result.toFixed(digits)}${unit}`
        : "Not recorded";
}

function localTime(timestamp) {
    if (!timestamp) return "Not recorded";

    const date = new Date(timestamp);

    if (Number.isNaN(date.getTime())) {
        return "Invalid timestamp";
    }

    return new Intl.DateTimeFormat("en-KE", {
        timeZone: "Africa/Nairobi",
        dateStyle: "medium",
        timeStyle: "short",
    }).format(date) + " EAT";
}

function assessmentFreshness(timestamp) {
    if (!timestamp) return "No assessment stored";

    const hours = (Date.now() - new Date(timestamp).getTime()) / 3600000;

    if (!Number.isFinite(hours)) {
        return "Assessment timestamp unavailable";
    }

    if (hours > 24) {
        return "Historical assessment. This is not a current warning";
    }

    if (hours < -1) {
        return "Check the assessment timestamp";
    }

    return "Recorded within the past 24 hours";
}


// Calculate the distance between two geographic coordinates.
// The Haversine formula accounts for the Earth's curvature.
function haversineKm(a, b) {
    const radius = 6371.0088;

    const lat1 = a.latitude * Math.PI / 180;
    const lat2 = b.latitude * Math.PI / 180;

    const latitudeDifference = (b.latitude - a.latitude) * Math.PI / 180;
    const longitudeDifference = (b.longitude - a.longitude) * Math.PI / 180;

    const h =
        Math.sin(latitudeDifference / 2) ** 2 +
        Math.cos(lat1) *
        Math.cos(lat2) *
        Math.sin(longitudeDifference / 2) ** 2;

    return 2 * radius * Math.asin(Math.min(1, Math.sqrt(h)));
}

function hasValidCoordinates(point) {
    if (point.latitude === null || point.longitude === null) {
        return false;
    }

    const latitude = Number(point.latitude);
    const longitude = Number(point.longitude);

    return Number.isFinite(latitude) &&
        Number.isFinite(longitude) &&
        latitude >= -90 &&
        latitude <= 90 &&
        longitude >= -180 &&
        longitude <= 180;
}


// API communication
// FastAPI obtains the monitoring data from PostgreSQL.
async function getJSON(url) {
    const response = await fetch(url, {
        headers: {
            Accept: "application/json",
        },
        cache: "no-store",
    });

    if (!response.ok) {
        throw new Error(
            `Failed to load ${url} (HTTP ${response.status})`
        );
    }

    return response.json();
}


// GIS map initialization
function initializeMap() {
    if (typeof L === "undefined") {
        write(
            "locationStatus",
            "Map services are unavailable. You can still select locations using search."
        );
        return;
    }

    // Center the map on Nairobi.
    // Individual observation-point coordinates come from PostgreSQL.
    state.map = L.map("residentMap", {
        scrollWheelZoom: false,
    }).setView([-1.286389, 36.817223], 11);

    L.tileLayer(
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        {
            maxZoom: 19,
            attribution: "© OpenStreetMap contributors",
        }
    ).addTo(state.map);

    state.markers = L.layerGroup().addTo(state.map);
}


// Search and risk filtering
function filteredPoints() {
    const search = $("areaSearch").value
        .toLowerCase()
        .trim();

    const selectedRisk = $("riskFilter").value;

    return state.locations.filter((point) => {
        const name = String(point.name || "").toLowerCase();
        const id = String(point.location_id);

        const matchesSearch =
            name.includes(search) || id.includes(search);

        const matchesRisk =
            selectedRisk === "ALL" ||
            riskOf(point) === selectedRisk;

        return matchesSearch && matchesRisk;
    });
}

function renderSearchResults(points) {
    const container = $("searchResults");
    container.replaceChildren();

    if (!$("areaSearch").value.trim()) {
        return;
    }

    // Show a small number of relevant results instead of
    // overwhelming the resident with all 237 monitoring points.
    points.slice(0, 7).forEach((point) => {
        const button = document.createElement("button");

        button.type = "button";
        button.className = "result-button";
        button.textContent =
            `${point.name} · #${point.location_id}`;

        button.addEventListener("click", () => {
            selectPoint(point);

            if (state.map && hasValidCoordinates(point)) {
                state.map.setView(
                    [
                        Number(point.latitude),
                        Number(point.longitude),
                    ],
                    14
                );
            }
        });

        container.append(button);
    });

    if (!points.length) {
        const notice = document.createElement("span");

        notice.className = "subtle-text";
        notice.textContent =
            "No matching monitoring points.";

        container.append(notice);
    }
}


// Render risk-colored observation points on the Leaflet map.
function renderMap() {
    const points = filteredPoints();

    write("pointCount", `${points.length} points`);
    renderSearchResults(points);

    if (!state.map || !state.markers) {
        return;
    }

    state.markers.clearLayers();

    points.forEach((point) => {
        if (!hasValidCoordinates(point)) {
            return;
        }

        const latitude = Number(point.latitude);
        const longitude = Number(point.longitude);

        const category = riskOf(point);

        const isSelected =
            Number(state.selected?.location_id) ===
            Number(point.location_id);

        const marker = L.circleMarker(
            [latitude, longitude],
            {
                radius: isSelected ? 10 : 6,
                color: isSelected ? "#0B1F3A" : "#FFFFFF",
                weight: isSelected ? 3 : 1.6,
                fillColor:
                    colors[category] || colors.NO_DATA,
                fillOpacity: 0.94,
            }
        ).addTo(state.markers);

        // Use a text node so stored location names are treated as text.
        const label = document.createElement("span");

        label.textContent =
            `${point.name} · ${category.replace("_", " ")}`;

        marker.bindTooltip(label);

        marker.on("click", () => {
            selectPoint(point);
        });
    });
}


// Advisory information
function setAdvisory(point) {
    if (point.alert_title) {
        write("advisoryStatus", point.alert_title);
        write(
            "advisoryMessage",
            point.alert_message || ""
        );
        write(
            "advisoryInstructions",
            point.alert_instructions || ""
        );
    } else {
        write(
            "advisoryStatus",
            "No currently active advisory"
        );

        write(
            "advisoryMessage",
            "No unexpired model-generated advisory is associated with this observation point."
        );

        write("advisoryInstructions", "");
    }
}


// Selected monitoring-point information
function setPointDetails(point) {
    write("areaTitle", point.name);

    const category = riskOf(point);
    const badge = $("areaRisk");

    badge.className = (`risk-pill ${category === "NO_DATA" ? "neutral" : category}`);

    badge.textContent = category === "NO_DATA"
        ? "Not assessed"
        : `${category} risk`;

    write("areaScore", number(point.risk_score, 3));
    write("assessmentTime", localTime(point.prediction_time));

    write(
        "assessmentAge",
        assessmentFreshness(point.prediction_time)
    );

    write(
        "temperature",
        number(point.temperature, 1, " °C")
    );

    write(
        "humidity",
        number(point.humidity, 1, "%")
    );

    write(
        "rainfall",
        number(point.rainfall, 1, " mm")
    );

    write(
        "weatherTime",
        `Weather timestamp: ${localTime(point.weather_timestamp)}`
    );

    // The nearest observation point is not an exact prediction
    // for the user's home, property or street.
    write(
        "assessmentCaveat",
        "This score describes a monitored observation point, not your exact home or property. Use official local warnings for safety decisions."
    );

    setAdvisory(point);
}


// Historical chart management
function destroyCharts() {
    if (state.riskChart) {
        state.riskChart.destroy();
    }

    if (state.weatherChart) {
        state.weatherChart.destroy();
    }

    state.riskChart = null;
    state.weatherChart = null;
}

function drawChart(
    canvasId,
    rows,
    valueKey,
    label,
    color,
    yMax
) {
    if (typeof Chart === "undefined" || rows.length === 0) {
        return null;
    }

    return new Chart($(canvasId), {
        type: "line",

        data: {
            labels: rows.map(
                (row) => String(row.local_date)
            ),

            datasets: [
                {
                    label,
                    data: rows.map(
                        (row) => row[valueKey]
                    ),
                    borderColor: color,
                    backgroundColor: `${color}1C`,
                    fill: true,
                    tension: 0.25,
                    pointRadius: 4,
                    spanGaps: false,
                },
            ],
        },

        options: {
            responsive: true,
            maintainAspectRatio: false,

            plugins: {
                legend: {
                    display: false,
                },

                tooltip: {
                    mode: "index",
                    intersect: false,
                },
            },

            scales: {
                x: {
                    ticks: {
                        maxTicksLimit: 6,
                    },
                },

                y: {
                    beginAtZero: true,
                    ...(yMax !== null ? {max: yMax} : {}),
                    ticks: {
                        precision: 2,
                    },
                },
            },
        },
    });
}


// Retrieve historical information for a selected observation point.
// The backend returns recorded predictions and weather snapshots,
// not fabricated historical observations.
async function loadHistory(locationId, serial) {
    destroyCharts();

    state.history = null;
    $("downloadHistory").disabled = true;

    write(
        "riskHistoryFallback",
        "Loading available records…"
    );

    write(
        "weatherHistoryFallback",
        "Loading available records…"
    );

    try {
        const history = await getJSON(
            `/api/resident/locations/${locationId}/history?days=30`
        );

        // Ignore responses belonging to an earlier selection.
        if (serial !== state.selectionSerial) {
            return;
        }

        const predictions = history.predictions || [];
        const readings = history.weather_readings || [];

        state.history = history;

        $("downloadHistory").disabled =
            !predictions.length && !readings.length;

        state.riskChart = drawChart(
            "riskHistoryChart",
            predictions,
            "risk_score",
            "Model score",
            "#1456B8",
            1
        );

        state.weatherChart = drawChart(
            "weatherHistoryChart",
            readings,
            "rainfall",
            "Snapshot precipitation (mm)",
            "#087EAC",
            null
        );

        write(
            "riskHistoryFallback",
            predictions.length
                ? (
                    state.riskChart
                        ? ""
                        : "Chart library unavailable."
                )
                : "No stored risk history for this point."
        );

        write(
            "weatherHistoryFallback",
            readings.length
                ? (
                    state.weatherChart
                        ? ""
                        : "Chart library unavailable."
                )
                : "No stored precipitation readings for this point."
        );

    } catch (error) {
        if (serial !== state.selectionSerial) {
            return;
        }

        console.error("Historical data error:", error);

        write(
            "riskHistoryFallback",
            "Could not load stored history."
        );

        write(
            "weatherHistoryFallback",
            "Could not load stored weather readings."
        );
    }
}


// Location selection
function selectPoint(point) {
    state.selected = point;
    state.selectionSerial += 1;

    setPointDetails(point);
    renderMap();

    loadHistory(
        point.location_id,
        state.selectionSerial
    );
}

function clearSelection() {
    state.selected = null;
    state.selectionSerial += 1;

    destroyCharts();

    state.history = null;
    $("downloadHistory").disabled = true;

    write("areaTitle", "Select a monitoring point");

    $("areaRisk").className = "risk-pill neutral";
    write("areaRisk", "Not selected");

    write("areaScore", "—");
    write("assessmentTime", "No location selected");
    write("assessmentAge", "");

    write("temperature", "—");
    write("humidity", "—");
    write("rainfall", "—");

    write("weatherTime", "Weather timestamp: —");

    write(
        "assessmentCaveat",
        "Choose a point using search, the map or optional device location. An observation-point assessment is not a property-level prediction."
    );

    write("advisoryStatus", "No point selected");

    write(
        "advisoryMessage",
        "Select a monitoring point to view unexpired model-generated advisories, if any."
    );

    write("advisoryInstructions", "");

    write(
        "riskHistoryFallback",
        "Select a point to view recorded history."
    );

    write(
        "weatherHistoryFallback",
        "Select a point to view stored weather readings."
    );

    renderMap();
}


// Nearby observation points
function renderNearby() {
    const container = $("nearbyList");
    container.replaceChildren();

    if (!state.position) {
        return;
    }

    const ranked = state.locations
        .filter(hasValidCoordinates)
        .map((point) => ({
            point,
            km: haversineKm(
                state.position,
                {
                    latitude: Number(point.latitude),
                    longitude: Number(point.longitude),
                }
            ),
        }))
        .sort((a, b) => a.km - b.km)
        .slice(0, 3);

    if (!ranked.length) {
        write(
            "nearbyIntro",
            "No monitoring points with valid coordinates are available."
        );
        return;
    }

    // A nearby reference point is not an exact assessment
    // of the user's current geographic position.
    write(
        "nearbyIntro",
        ranked[0].km > 5
            ? "No monitored point is within 5 km. These are the closest available reference points, not an assessment of your position."
            : "Closest monitored observation points. Distance does not imply the same risk at your property."
    );

    ranked.forEach(({point, km}) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "nearby-item";

        const info = document.createElement("span");

        const name = document.createElement("span");
        name.className = "nearby-name";
        name.textContent = point.name;

        const distance = document.createElement("small");
        distance.textContent = `${km.toFixed(1)} km away`;

        info.append(name, distance);

        const risk = document.createElement("span");
        risk.className = "nearby-risk";
        risk.textContent = riskOf(point).replace("_", " ");

        button.append(info, risk);

        button.addEventListener("click", () => {
            selectPoint(point);

            if (state.map) {
                state.map.setView(
                    [
                        Number(point.latitude),
                        Number(point.longitude),
                    ],
                    14
                );
            }
        });

        container.append(button);
    });
}


// Clear device location from the current browser page.
function clearLocation() {
    state.position = null;

    if (state.map && state.locationMarker) {
        state.map.removeLayer(state.locationMarker);
    }

    if (state.map && state.accuracyCircle) {
        state.map.removeLayer(state.accuracyCircle);
    }

    state.locationMarker = null;
    state.accuracyCircle = null;

    $("clearLocation").hidden = true;

    write(
        "locationStatus",
        "Your position has been cleared from this page. Browser permissions are managed in browser settings."
    );

    write(
        "nearbyIntro",
        "Use your location to discover nearby monitored points, or select any point manually."
    );

    $("nearbyList").replaceChildren();

    clearSelection();
}


// Consent-based browser geolocation
function locateMe() {
    if (!navigator.geolocation) {
        write(
            "locationStatus",
            "Your browser does not support location services. Choose an area manually."
        );
        return;
    }

    $("useLocation").disabled = true;

    write(
        "locationStatus",
        "Waiting for browser location permission…"
    );

    navigator.geolocation.getCurrentPosition(
        (result) => {
            $("useLocation").disabled = false;

            const {
                latitude,
                longitude,
                accuracy,
            } = result.coords;

            // Coordinates remain in JavaScript memory.
            // They are not sent to the FastAPI backend.
            state.position = {
                latitude,
                longitude,
            };

            $("clearLocation").hidden = false;

            write(
                "locationStatus",
                `Device position obtained (reported accuracy ±${Math.round(accuracy)} m). Used only in this browser page.`
            );

            if (state.map) {
                if (state.locationMarker) {
                    state.map.removeLayer(state.locationMarker);
                }

                if (state.accuracyCircle) {
                    state.map.removeLayer(state.accuracyCircle);
                }

                state.locationMarker = L.circleMarker(
                    [latitude, longitude],
                    {
                        radius: 8,
                        color: "#1456B8",
                        fillColor: "#38BDF8",
                        fillOpacity: 1,
                        weight: 3,
                    }
                ).addTo(state.map);

                state.locationMarker.bindTooltip(
                    "Your device-reported position"
                );

                state.accuracyCircle = L.circle(
                    [latitude, longitude],
                    {
                        radius: accuracy,
                        color: "#1456B8",
                        fillOpacity: 0.06,
                        weight: 1,
                    }
                ).addTo(state.map);

                state.map.setView(
                    [latitude, longitude],
                    13
                );
            }

            renderNearby();

            // Automatically display the nearest monitored point
            // only when it is within our 5 km proximity limit.
            const nearest = state.locations
                .filter(hasValidCoordinates)
                .map((point) => ({
                    point,
                    distance: haversineKm(
                        state.position,
                        {
                            latitude: Number(point.latitude),
                            longitude: Number(point.longitude),
                        }
                    ),
                }))
                .sort((a, b) => a.distance - b.distance)[0];

            if (nearest && nearest.distance <= 5) {
                selectPoint(nearest.point);
            } else {
                clearSelection();
            }
        },

        (error) => {
            $("useLocation").disabled = false;

            const messages = {
                1: "Location permission was denied. You can choose an area manually.",
                2: "Position unavailable. Select a monitoring point manually.",
                3: "Location request timed out. Try again or choose an area.",
            };

            write(
                "locationStatus",
                messages[error.code] ||
                "Location could not be retrieved."
            );
        },

        {
            enableHighAccuracy: true,
            timeout: 12000,
            maximumAge: 60000,
        }
    );
}


// Export historical monitoring records as CSV.
function downloadHistoryCSV() {
    if (!state.history || !state.selected) {
        return;
    }

    // Combine records by local calendar date.
    // Missing values remain blank rather than being estimated.
    const daily = new Map();

    for (const prediction of state.history.predictions || []) {
        daily.set(prediction.local_date, {
            score: prediction.risk_score,
            risk: prediction.risk_level,
        });
    }

    for (const weather of state.history.weather_readings || []) {
        const entry = daily.get(weather.local_date) || {};

        entry.rain = weather.rainfall;
        entry.temp = weather.temperature;

        daily.set(weather.local_date, entry);
    }

    const rows = [[
        "date",
        "model_score",
        "risk_level",
        "precipitation_snapshot_mm",
        "temperature_snapshot_c",
    ]];

    const serialize = (value) => {
        if (value === null || value === undefined) {
            return "";
        }

        // Escape text so commas and quotation marks do not corrupt CSV.
        const text = String(value);

        return /[",\r\n]/.test(text)
            ? `"${text.replaceAll('"', '""')}"`
            : text;
    };

    const sortedRecords = [...daily.entries()]
        .sort(([a], [b]) => a.localeCompare(b));

    for (const [date, record] of sortedRecords) {
        rows.push([
            date,
            record.score,
            record.risk,
            record.rain,
            record.temp,
        ]);
    }

    const csv = rows
        .map((columns) => columns.map(serialize).join(","))
        .join("\r\n");

    const url = URL.createObjectURL(
        new Blob([csv], {
            type: "text/csv;charset=utf-8",
        })
    );

    const link = document.createElement("a");

    link.href = url;
    link.download = (
        `floodwatch_location_${state.selected.location_id}_history.csv`
    );

    document.body.append(link);
    link.click();
    link.remove();

    // Release the temporary object URL after initiating the download.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}


// Load latest operational monitoring records.
async function loadDashboard() {
    try {
        const [overview, locations] = await Promise.all([
            getJSON("/api/overview"),
            getJSON("/api/locations"),
        ]);

        if (!Array.isArray(locations)) {
            throw new Error(
                "Location API response is invalid."
            );
        }

        state.overview = overview;
        state.locations = locations;

        write(
            "freshness",
            `${assessmentFreshness(overview.latest_prediction)} · ${localTime(overview.latest_prediction)}`
        );

        // Refresh the selected observation point after loading updated
        // database records. Preserve manual selection where possible.
        if (state.selected) {
            const updated = locations.find(
                (point) =>
                    Number(point.location_id) ===
                    Number(state.selected.location_id)
            );

            if (updated) {
                selectPoint(updated);
            } else {
                clearSelection();
            }
        }

        renderMap();
        renderNearby();

        $("errorNotice").hidden = true;

    } catch (error) {
        console.error(
            "FloodWatch dashboard error:",
            error
        );

        write(
            "errorNotice",
            "Monitoring data is unavailable. Check the connection and refresh the page."
        );

        $("errorNotice").hidden = false;

        write(
            "freshness",
            "Connection unavailable"
        );
    }
}


// Initialize the resident dashboard.
document.addEventListener("DOMContentLoaded", () => {
    initializeMap();

    $("areaSearch").addEventListener(
        "input",
        renderMap
    );

    $("riskFilter").addEventListener(
        "change",
        renderMap
    );

    $("chooseArea").addEventListener(
        "click",
        () => $("areaSearch").focus()
    );

    $("useLocation").addEventListener(
        "click",
        locateMe
    );

    $("clearLocation").addEventListener(
        "click",
        clearLocation
    );

    $("downloadHistory").addEventListener(
        "click",
        downloadHistoryCSV
    );

    loadDashboard();
});
