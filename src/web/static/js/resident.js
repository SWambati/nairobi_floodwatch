
"use strict";

// Resident dashboard state. Device coordinates remain in browser memory only.
const state = {
    locations: [], overview: null, selected: null, position: null,
    map: null, markers: null, locationMarker: null, accuracyCircle: null,
    riskChart: null, weatherChart: null, selectionSerial: 0, history: null,
};


// =========================================================
// RESIDENT ACCOUNT VERIFICATION
// =========================================================

let residentVerification = null;


// ---------------------------------------------------------
// Display account verification information
// ---------------------------------------------------------

function showResidentVerificationMessage(message, showLogin = false) {
    const notice = document.getElementById(
        "residentVerificationNotice"
    );

    const messageElement = document.getElementById(
        "residentVerificationMessage"
    );

    const loginLink = document.getElementById(
        "residentVerificationLogin"
    );

    if (!notice || !messageElement || !loginLink) {
        return;
    }

    notice.hidden = false;
    messageElement.textContent = message;
    loginLink.hidden = !showLogin;
}


// ---------------------------------------------------------
// Update CSV button permissions
// ---------------------------------------------------------

function updateResidentDownloadPermission() {
    const button = document.getElementById("downloadHistory");

    if (!button) {
        return;
    }

    const history = state.history;

    const hasHistory = Boolean(
        (history?.predictions?.length || 0) +
        (history?.weather_readings?.length || 0)
    );

    const canDownload = (
        residentVerification?.can_download_csv === true
    );

    button.disabled = (
        !state.selected ||
        !hasHistory ||
        !canDownload
    );

    button.title = canDownload
        ? "Download recorded monitoring history as CSV."
        : "CSV downloads require a verified Resident account.";
}

// Render the verification notice
function renderResidentVerification() {
    if (!residentVerification) {
        return;
    }

    const notice = document.getElementById("residentVerificationNotice");

    if (!notice) {
        return;
    }

    // Verified accounts do not require a reminder.
    if (residentVerification.email_verified === true) {
        notice.hidden = true;
        return;
    }

    const deadline = residentVerification.grace_expires_at;
    const remainingMilliseconds = deadline ? Math.max(0, new Date(deadline).getTime() - Date.now()) : 0;
    const remainingMinutes = Math.ceil(remainingMilliseconds / 60000);
    const hours = Math.floor(remainingMinutes / 60);
    const minutes = remainingMinutes % 60;
    const status = residentVerification.verification_status;

    if (
        status === "grace_expired_not_enforced" ||
        remainingMilliseconds <= 0
    ) {
        showResidentVerificationMessage(
            "Your 48-hour email verification window has ended. " +
            "You can continue viewing public monitoring information " +
            "while email verification is being configured. " +
            "CSV downloads remain unavailable until your email " +
            "is verified."
        );

        return;
    }

    showResidentVerificationMessage(
        `Your email address has not been verified. ` +
        `Verification window: ${hours}h ${minutes}m remaining. ` +
        `You can continue viewing flood assessments and advisories, ` +
        `but CSV downloads require email verification.`
    );
}
// Retrieve verification status from FastAPI
async function loadResidentVerification() {
    try {
        const response = await fetch(
            "/api/resident/account/verification",
            {
                method: "GET",
                credentials: "same-origin",
                cache: "no-store",
                headers: {"Accept": "application/json"}
            }
        );

        // Public visitors may browse monitoring information
        // but do not receive verified-account CSV privileges.
        if (response.status === 401) {
            residentVerification = null;

            showResidentVerificationMessage(
                "You can explore flood monitoring information " +
                "without signing in. To access verified-account " +
                "features such as CSV downloads, please sign in.",
                true
            );

            return;
        }

        if (!response.ok) {
            throw new Error(
                `Verification status unavailable (${response.status}).`
            );
        }

        residentVerification = await response.json();

        if (residentVerification.can_access_dashboard == false){
            window.location.replace("/resident/account");
            return;
        }
        renderResidentVerification();

    } catch (error) {
        console.error(
            "Unable to load Resident verification status:",
            error
        );

        residentVerification = null;

        showResidentVerificationMessage(
            "Your account verification status is temporarily " +
            "unavailable. Flood monitoring remains accessible, " +
            "but CSV downloads are disabled."
        );

    } finally {
        updateResidentDownloadPermission();
    }
}

const colors = {
    LOW: "#16855B", MODERATE: "#D99A1B",
    HIGH: "#C63B44", NO_DATA: "#8B9BAE",
};

const $ = (id) => document.getElementById(id);
function write(id, value) { $(id).textContent = String(value); }
function riskOf(point) { return String(point?.risk_level || "NO_DATA").toUpperCase(); }

function number(value, digits = 1, unit = "") {
    if (value === null || value === undefined || value === "") return "Not recorded";
    const numeric = Number(value);
    return Number.isFinite(numeric) ? `${numeric.toFixed(digits)}${unit}` : "Not recorded";
}

function localTime(timestamp) {
    if (!timestamp) return "Not recorded";
    const date = new Date(timestamp);
    if (!Number.isFinite(date.getTime())) return "Invalid timestamp";
    return new Intl.DateTimeFormat("en-KE", {
        timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "short",
    }).format(date) + " EAT";
}

function assessmentFreshness(timestamp) {
    if (!timestamp) return "No assessment stored";
    const hours = (Date.now() - new Date(timestamp).getTime()) / 3600000;
    if (!Number.isFinite(hours)) return "Assessment timestamp unavailable";
    if (hours > 24) return "Historical assessment. This is not a current warning";
    if (hours < -1) return "Check the assessment timestamp";
    return "Recorded within the past 24 hours";
}

// These bands were derived from validation data, not chosen as probabilities.
function explainRiskScore(point) {
    const level = riskOf(point);
    const score = point?.risk_score;
    if (score === null || score === undefined || score === "" ||
        !["LOW", "MODERATE", "HIGH"].includes(level) || !Number.isFinite(Number(score))) {
        return "No valid model assessment is available for this monitoring point.";
    }
    const bands = {
        LOW: "below 0.0999",
        MODERATE: "from 0.0999 to below 0.6862",
        HIGH: "0.6862 or higher",
    };
    const explanations = {
        LOW: "a comparatively lower model-derived flood-risk signal",
        MODERATE: "an elevated model-derived flood-risk signal that warrants monitoring",
        HIGH: "a stronger model-derived flood-risk signal that warrants caution",
    };
    return `A score of ${Number(score).toFixed(3)} is classified as ${level.toLowerCase()} ` +
        `(${bands[level]}), indicating ${explanations[level]}. ` +
        "It is not a percentage chance of flooding or a prediction for your exact property.";
}

// Haversine distance from resident / searched place to an observation point.
function haversineKm(a, b) {
    const radians = Math.PI / 180;
    const dLat = (b.latitude - a.latitude) * radians;
    const dLon = (b.longitude - a.longitude) * radians;
    const lat1 = a.latitude * radians;
    const lat2 = b.latitude * radians;
    const h = Math.sin(dLat / 2) ** 2 +
        Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
    return 2 * 6371.0088 * Math.asin(Math.min(1, Math.sqrt(h)));
}

function hasValidCoordinates(point) {
    if (point.latitude === null || point.longitude === null ||
        point.latitude === undefined || point.longitude === undefined) return false;
    const lat = Number(point.latitude);
    const lon = Number(point.longitude);
    return Number.isFinite(lat) && Number.isFinite(lon) &&
        lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
}

async function getJSON(url) {
    const response = await fetch(url, {
        headers: { Accept: "application/json" }, cache: "no-store",
    });
    if (!response.ok) throw new Error(`Request failed: ${url} (HTTP ${response.status})`);
    return response.json();
}

// Leaflet map: model risk belongs to points, never to entire wards.
function initializeMap() {
    if (typeof L === "undefined") {
        write("locationStatus", "Map services are unavailable. You can still search for monitoring points.");
        return;
    }
    state.map = L.map("residentMap", { scrollWheelZoom: false })
        .setView([-1.286389, 36.817223], 11);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, attribution: "© OpenStreetMap contributors",
    }).addTo(state.map);
    state.markers = L.layerGroup().addTo(state.map);
   
}

function filteredPoints() {
    const query = $("areaSearch").value.trim().toLowerCase();
    const filter = $("riskFilter").value;
    return state.locations.filter((point) => {
        const localMatch = String(point.name || "").toLowerCase().includes(query) ||
            String(point.location_id).includes(query);
        return localMatch && (filter === "ALL" || riskOf(point) === filter);
    });
}

function renderSearchResults(points) {
    const container = $("searchResults");
    container.replaceChildren();
    if (!$("areaSearch").value.trim()) return;
    for (const point of points.slice(0, 7)) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "result-button";
        button.textContent = `${point.name} · Point #${point.location_id}`;
        button.addEventListener("click", () => {
            selectPoint(point);
            if (state.map && hasValidCoordinates(point)) {
                state.map.setView([Number(point.latitude), Number(point.longitude)], 14);
            }
        });
        container.append(button);
    }
    if (!points.length) {
        const message = document.createElement("span");
        message.className = "subtle-text";
        message.textContent = "No matching monitoring points.";
        container.append(message);
    }
}


function renderMap() {
    const points = filteredPoints();

    write("pointCount", `${points.length} points`);
    renderSearchResults(points);

    if (!state.map || !state.markers) {
        return;
    }

    // Clear existing markers before rendering the current risk filter.
    state.markers.clearLayers();

    for (const point of points) {
        if (!hasValidCoordinates(point)) {
            continue;
        }

        const latitude = Number(point.latitude);
        const longitude = Number(point.longitude);

        const category = riskOf(point);

        const selected = (
            Number(point.location_id) ===
            Number(state.selected?.location_id)
        );

        // The color comes from our fixed risk palette, never from
        // user-generated text or arbitrary database CSS.
        const markerColor = colors[category] || colors.NO_DATA;

        // MarkerCluster is designed to work with Leaflet markers.
        // DivIcon lets us preserve our familiar circular risk colors.
        const icon = L.divIcon({
            className: (
                "floodwatch-point-marker" +
                (selected ? " is-selected" : "")
            ),
            html: (
                `<span class="point-marker-core" ` +
                `style="background:${markerColor}"></span>`
            ),
            iconSize: [22, 22],
            iconAnchor: [11, 11],
        });

        const marker = L.marker(
            [latitude, longitude],
            {
                icon,
                keyboard: true,
                title: `${point.name} - ${category}`,
                zIndexOffset: selected ? 1000 : 0,
            }
        );

        // Use textContent so location names cannot inject HTML.
        const tooltip = document.createElement("span");

        tooltip.textContent = (
            `${point.name} · ${category.replace("_", " ")}`
        );

        marker.bindTooltip(tooltip);

        marker.on("click", () => {
            selectPoint(point);
        });

        state.markers.addLayer(marker);
    }
}


function setAdvisory(point) {
    if (point.alert_title) {
        write("advisoryStatus", point.alert_title);
        write("advisoryMessage", point.alert_message || "");
        write("advisoryInstructions", point.alert_instructions || "");
    } else {
        write("advisoryStatus", "No currently active advisory");
        write("advisoryMessage", "No unexpired model-generated advisory is associated with this observation point.");
        write("advisoryInstructions", "");
    }
}

function setPointDetails(point) {
    write("areaTitle", point.name);
    const category = riskOf(point);
    const badge = $("areaRisk");
    badge.className = `risk-pill ${category === "NO_DATA" ? "neutral" : category}`;
    badge.textContent = category === "NO_DATA" ? "Not assessed" : `${category} risk`;
    write("areaScore", number(point.risk_score, 3));
    write("scoreMeaning", explainRiskScore(point));
    write("assessmentTime", localTime(point.prediction_time));
    write("assessmentAge", assessmentFreshness(point.prediction_time));
    write("temperature", number(point.temperature, 1, " °C"));
    write("humidity", number(point.humidity, 1, "%"));
    write("rainfall", number(point.rainfall, 1, " mm"));
    write("weatherTime", `Weather timestamp: ${localTime(point.weather_timestamp)}`);
    write("assessmentCaveat",
        "This score describes a monitored observation point, not your exact property. Follow official local warnings for safety decisions.");
    setAdvisory(point);
}

function destroyCharts() {
    if (state.riskChart) state.riskChart.destroy();
    if (state.weatherChart) state.weatherChart.destroy();
    state.riskChart = null;
    state.weatherChart = null;
}

function drawChart(canvasId, rows, valueKey, label, color, yMax = null) {
    if (typeof Chart === "undefined" || !rows.length) return null;
    return new Chart($(canvasId), {
        type: "line",
        data: {
            labels: rows.map((row) => String(row.local_date)),
            datasets: [{
                label, data: rows.map((row) => row[valueKey]),
                borderColor: color, backgroundColor: `${color}16`,
                fill: true, tension: .22, pointRadius: 4, spanGaps: false,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false } },
            scales: {
                x: { ticks: { maxTicksLimit: 6 } },
                y: { beginAtZero: true, ...(yMax !== null ? { max: yMax } : {}) },
            },
        },
    });
}

// Charts show recorded days only; no historical values are invented.
async function loadHistory(locationId, serial) {
    destroyCharts();
    state.history = null;
    $("downloadHistory").disabled = true;
    write("riskHistoryFallback", "Loading recorded assessments…");
    write("weatherHistoryFallback", "Loading recorded precipitation…");
    try {
        const history = await getJSON(`/api/resident/locations/${locationId}/history?days=30`);
        if (serial !== state.selectionSerial) return;
        const predictions = history.predictions || [];
        const readings = history.weather_readings || [];
        state.history = history;
        updateResidentDownloadPermission();

        state.riskChart = drawChart("riskHistoryChart", predictions, "risk_score", "Model score", "#1755BA", 1);
        state.weatherChart = drawChart("weatherHistoryChart", readings, "rainfall", "Recorded precipitation (mm)", "#087EAC");
        write("riskHistoryFallback", predictions.length
            ? (state.riskChart ? "" : "Chart library unavailable.")
            : "No stored risk history for this point.");
        write("weatherHistoryFallback", readings.length
            ? (state.weatherChart ? "" : "Chart library unavailable.")
            : "No stored precipitation readings for this point.");
    } catch (error) {
        if (serial !== state.selectionSerial) return;
        console.error("Unable to load resident history:", error);
        write("riskHistoryFallback", "Could not load stored risk history.");
        write("weatherHistoryFallback", "Could not load recorded precipitation.");
    }
}

function selectPoint(point) {
    state.selected = point;
    state.selectionSerial++;
    setPointDetails(point);
    renderMap();
    loadHistory(point.location_id, state.selectionSerial);
}

function clearSelection() {
    state.selected = null;
    state.selectionSerial++;
    destroyCharts();
    state.history = null;
    $("downloadHistory").disabled = true;
    write("areaTitle", "Select a monitoring point");
    $("areaRisk").className = "risk-pill neutral";
    write("areaRisk", "Not selected");
    write("areaScore", "—");
    write("scoreMeaning", "Select a monitoring point to understand its model-derived risk assessment.");
    write("assessmentTime", "No location selected");
    write("assessmentAge", "");
    write("temperature", "—");
    write("humidity", "—");
    write("rainfall", "—");
    write("weatherTime", "Weather timestamp: —");
    write("assessmentCaveat", "Choose a point using search, the map or optional device location. Assessments describe monitored points, not individual properties.");
    write("advisoryStatus", "No point selected");
    write("advisoryMessage", "Select a monitoring point to view unexpired model-generated advisories, if any.");
    write("advisoryInstructions", "");
    write("riskHistoryFallback", "Select a point to view recorded history.");
    write("weatherHistoryFallback", "Select a point to view stored weather readings.");
    renderMap();
}

function rankedNearby(position) {
    return state.locations.filter(hasValidCoordinates).map((point) => ({
        point,
        km: haversineKm(position, {
            latitude: Number(point.latitude), longitude: Number(point.longitude),
        }),
    })).sort((a, b) => a.km - b.km);
}

function renderNearby() {
    const container = $("nearbyList");
    container.replaceChildren();
    if (!state.position) return;
    const nearest = rankedNearby(state.position).slice(0, 3);
    if (!nearest.length) {
        write("nearbyIntro", "No nearby monitoring coordinates are available.");
        return;
    }
    write("nearbyIntro", nearest[0].km > 5
        ? "No monitored point is within 5 km. These are the nearest reference points, not an assessment of your property."
        : "Three closest monitored observation points. Proximity does not imply identical flood risk.");
    for (const {point, km} of nearest) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "nearby-item";
        const info = document.createElement("span");
        const name = document.createElement("span");
        name.className = "nearby-name";
        name.textContent = point.name;
        const distance = document.createElement("small");
        distance.textContent = `${km.toFixed(1)} km away · Point #${point.location_id}`;
        info.append(name, distance);
        const risk = document.createElement("span");
        risk.className = `nearby-risk ${riskOf(point)}`;
        risk.textContent = riskOf(point).replace("_", " ");
        button.append(info, risk);
        button.addEventListener("click", () => {
            selectPoint(point);
            if (state.map) state.map.setView([Number(point.latitude), Number(point.longitude)], 14);
        });
        container.append(button);
    }
}

function clearLocation() {
    state.position = null;
    if (state.map && state.locationMarker) state.map.removeLayer(state.locationMarker);
    if (state.map && state.accuracyCircle) state.map.removeLayer(state.accuracyCircle);
    state.locationMarker = null;
    state.accuracyCircle = null;
    $("clearLocation").hidden = true;
    write("locationStatus", "Your position has been cleared from this page. Browser permissions are managed in your browser settings.");
    write("nearbyIntro", "Use your location to find nearby monitored points, or choose one manually.");
    $("nearbyList").replaceChildren();
    clearSelection();
}

function locateMe() {
    if (!navigator.geolocation) {
        write("locationStatus", "Your browser doesn't support location sharing. You can choose an area manually.");
        return;
    }
    $("useLocation").disabled = true;
    write("locationStatus", "Waiting for browser location permission…");
    navigator.geolocation.getCurrentPosition((result) => {
        $("useLocation").disabled = false;
        const {latitude, longitude, accuracy} = result.coords;
        state.position = {latitude, longitude};
        $("clearLocation").hidden = false;
        write("locationStatus", `Device position obtained (reported accuracy ±${Math.round(accuracy)} m). Used only on this page.`);
        if (state.map) {
            if (state.locationMarker) state.map.removeLayer(state.locationMarker);
            if (state.accuracyCircle) state.map.removeLayer(state.accuracyCircle);
            state.locationMarker = L.circleMarker([latitude, longitude], {
                radius: 8, color: "#1755BA", fillColor: "#38BDF8", fillOpacity: 1, weight: 3,
            }).addTo(state.map).bindTooltip("Your device-reported position");
            state.accuracyCircle = L.circle([latitude, longitude], {
                radius: accuracy, color: "#1755BA", fillOpacity: .06, weight: 1,
            }).addTo(state.map);
            state.map.setView([latitude, longitude], 13);
        }
        renderNearby();
        const nearest = rankedNearby(state.position)[0];
        if (nearest && nearest.km <= 5) selectPoint(nearest.point);
        else clearSelection();
    }, (error) => {
        $("useLocation").disabled = false;
        const messages = {
            1: "Location permission denied. You can choose an area manually.",
            2: "Device position unavailable. Choose an area manually.",
            3: "Location request timed out. Please try again.",
        };
        write("locationStatus", messages[error.code] || "Unable to retrieve location.");
    }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 });
}


// =========================================================
// VERIFIED RESIDENT CSV EXPORT
// =========================================================

async function downloadHistoryCSV() {
    if (!state.selected || !state.history) {
        return;
    }

    // Frontend permission check for user experience.
    // FastAPI independently verifies authorization.
    if (residentVerification?.can_download_csv !== true) {
        showResidentVerificationMessage(
            "Please verify your email address before " +
            "downloading monitoring history CSV files."
        );

        return;
    }

    const locationId = Number(
        state.selected.location_id
    );

    if (!Number.isSafeInteger(locationId) || locationId <= 0) {
        console.error("Invalid monitoring location ID.");
        return;
    }

    const button = document.getElementById(
        "downloadHistory"
    );

    button.disabled = true;

    try {
        const response = await fetch(
            `/api/resident/locations/${locationId}/history.csv?days=30`,
            {
                method: "GET",
                credentials: "same-origin",
                cache: "no-store",
                headers: {"Accept": "text/csv"}
            }
        );

        if (!response.ok) {
            let message = "Unable to download monitoring history.";

            try {
                const result = await response.json();

                if (typeof result.detail === "string") {
                    message = result.detail;
                }
            } catch {
                // Preserve the generic error message.
            }

            // A session may expire or account permissions may change.
            if (response.status === 401 || response.status === 403) {
                await loadResidentVerification();
            }

            throw new Error(message);
        }

        // Only use CSV returned by the authorized backend.
        const file = await response.blob();
        const objectUrl = URL.createObjectURL(file);
        const link = document.createElement("a");

        link.href = objectUrl;
        link.download = (`floodwatch_location_${locationId}_history.csv`);
        document.body.appendChild(link);
        link.click();
        link.remove();

        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);

    } catch (error) {
        console.error("Resident CSV download failed:", error);
        showResidentVerificationMessage(error.message || "Unable to download monitoring history.");

    } finally {
        updateResidentDownloadPermission();
    }
}


async function loadDashboard() {
    try {
        const [overview, locations] = await Promise.all([
            getJSON("/api/overview"), getJSON("/api/locations"),
        ]);
        if (!Array.isArray(locations)) throw new Error("Invalid monitoring-location response.");
        state.overview = overview;
        state.locations = locations;
        write("freshness", `${assessmentFreshness(overview.latest_prediction)} · ${localTime(overview.latest_prediction)}`);
        if (state.selected) {
            const updated = locations.find((point) => Number(point.location_id) === Number(state.selected.location_id));
            if (updated) selectPoint(updated);
            else clearSelection();
        }
        renderMap();
        renderNearby();
        $("errorNotice").hidden = true;
    } catch (error) {
        console.error("FloodWatch monitoring request failed:", error);
        write("errorNotice", "Monitoring data is unavailable. Please check the connection and try again.");
        $("errorNotice").hidden = false;
        write("freshness", "Monitoring data unavailable");
    }
}

// Leave place_search.js responsible for geographic fallback and its debounce.
document.addEventListener("DOMContentLoaded", () => {
    initializeMap();
    loadResidentVerification();
    window.setInterval(() => {
        if (residentVerification) {
            renderResidentVerification();
        }
    }, 60000);
    $("areaSearch").addEventListener("input", renderMap);
    $("riskFilter").addEventListener("change", renderMap);
    $("chooseArea").addEventListener("click", () => $("areaSearch").focus());
    $("useLocation").addEventListener("click", locateMe);
    $("clearLocation").addEventListener("click", clearLocation);
    $("downloadHistory").addEventListener("click", downloadHistoryCSV);
    loadDashboard();
});
