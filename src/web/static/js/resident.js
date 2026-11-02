"use strict";

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

//RESIDENT ACCOUNT VERIFICATION
let residentVerification = null;

//Display account verification information
function showResidentVerificationMessage(message, showLogin = false) {
    const notice = document.getElementById("residentVerificationNotice");
    const messageElement = document.getElementById("residentVerificationMessage");
    const loginLink = document.getElementById("residentVerificationLogin");

    if (!notice || !messageElement || !loginLink) {
        return;
    }

    notice.hidden = false;
    messageElement.textContent = message;
    loginLink.hidden = !showLogin;
}

//Update CSV button permissions
function updateResidentDownloadPermission() {
    const button = document.getElementById("downloadHistory");

    if (!button) {
        return;
    }

    const history = state.history;
    const hasHistory = Boolean((history?.predictions?.length || 0) + (history?.weather_readings?.length || 0));
    const canDownload = (residentVerification?.can_download_csv === true);

    button.disabled = (!state.selected || !hasHistory || !canDownload);
    button.title = canDownload ? "Download recorded monitoring history as CSV." : "CSV downloads require a verified Resident account.";
}

//Render resident verification notice
function renderResidentVerification() {
    if (!residentVerification) {
        return;
    }

    const notice = document.getElementById("residentVerificationNotice");

    if (!notice) {
        return;
    }

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

    if (status === "grace_expired_not_enforced" || remainingMilliseconds <= 0) {
        showResidentVerificationMessage("Your 48-hour email verification window has ended. " + "You can continue viewing public monitoring information " + "while email verification is being configured. " + "CSV downloads remain unavailable until your email " + "is verified.");
        return;
    }
    showResidentVerificationMessage(`Your email address has not been verified. ` + `Verification window: ${hours}h ${minutes}m remaining. ` + `You can continue viewing flood assessments and advisories, ` + `but CSV downloads require email verification.`);
}

//Retrieve verification status from FastAPI
async function loadResidentVerification() {
    try {const response = await fetch("/api/resident/account/verification", {method: "GET", credentials: "same-origin", cache: "no-store", headers: {Accept: "application/json"}});
        if (response.status === 401) {
            residentVerification = null;
            showResidentVerificationMessage("You can explore flood monitoring information " + "without signing in. To access verified-account " + "features such as CSV downloads, please sign in.", true);
            return;
        }

        if (!response.ok) {
            throw new Error(`Verification status unavailable (${response.status}).`);
        }

        residentVerification = await response.json();

        if (residentVerification.can_access_dashboard === false) {
            window.location.replace("/resident/account");
            return;
        }
        renderResidentVerification();
    } catch (error) {
        console.error("Unable to load Resident verification status:", error);
        residentVerification = null;
        showResidentVerificationMessage("Your account verification status is temporarily " + "unavailable. Flood monitoring remains accessible, " + "but CSV downloads are disabled.");
    } finally {
        updateResidentDownloadPermission();
    }
}

//APPLICATION UTILITIES
const colors = {LOW: "#16855B", MODERATE: "#D99A1B", HIGH: "#C63B44", NO_DATA: "#8B9BAE"};
const $ = (id) => document.getElementById(id);

function write(id, value) {
    $(id).textContent = String(value);
}

function riskOf(point) {
    return String(point?.risk_level || "NO_DATA").toUpperCase();
}

function number(value, digits = 1, unit = "") {
    if (value === null || value === undefined || value === "") {
        return "Not recorded";
    }

    const numeric = Number(value);

    return Number.isFinite(numeric) ? `${numeric.toFixed(digits)}${unit}` : "Not recorded";
}

//Format timestamps using Nairobi local time
function localTime(timestamp) {
    if (!timestamp) {
        return "Not recorded";
    }

    const date = new Date(timestamp);

    if (Number.isNaN(date.getTime())) {
        return "Invalid timestamp";
    }

    return new Intl.DateTimeFormat("en-KE", {timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "short"}).format(date) + " EAT";
}

//Determine assessment freshness

function assessmentFreshness(timestamp) {
    if (!timestamp) {
        return "No assessment stored";
    }

    const hours = (Date.now() - new Date(timestamp).getTime()) / 3600000;

    if (!Number.isFinite(hours)) {
        return "Assessment timestamp unavailable";
    }

    if (hours > 24) {
        return ("Historical assessment. " + "This is not a current warning");
    }

    if (hours < -1) {
        return "Check the assessment timestamp";
    }
    return "Recorded within the past 24 hours";
}

//FLOOD RISK INTERPRETATION
// These bands were derived from validation data. Scores are not probabilities of flooding.
function explainRiskScore(point) {
    const level = riskOf(point);
    const score = point?.risk_score;

    if (score === null || score === undefined || score === "" || !["LOW", "MODERATE", "HIGH"].includes(level) || !Number.isFinite(Number(score))) {
        return ("No valid model assessment is available " + "for this monitoring point.");
    }

    const bands = {LOW: "below 0.0999", MODERATE: "from 0.0999 to below 0.6862", HIGH: "0.6862 or higher"};
    const explanations = {
        LOW: ("a comparatively lower model-derived " + "flood-risk signal"),
        MODERATE: ("an elevated model-derived flood-risk " + "signal that warrants monitoring"),
        HIGH: ("a stronger model-derived flood-risk " + "signal that warrants caution")
    };
    return (`A score of ${Number(score).toFixed(3)} ` + `is classified as ${level.toLowerCase()} ` + `(${bands[level]}), indicating ${explanations[level]}. ` + "It is not a percentage chance of flooding or " + "a prediction for your exact property.");
}

//LOCATION UTILITIES
function haversineKm(a, b) {
    const radians = Math.PI / 180;
    const dLat = (b.latitude - a.latitude) * radians;
    const dLon = (b.longitude - a.longitude) * radians;
    const lat1 = a.latitude * radians;
    const lat2 = b.latitude * radians;
    const h = (Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2);

    return (2 * 6371.0088 * Math.asin(Math.min(1, Math.sqrt(h))));
}

function hasValidCoordinates(point) {
    if (point.latitude === null || point.longitude === null || point.latitude === undefined || point.longitude === undefined) {
        return false;
    }

    const lat = Number(point.latitude);
    const lon = Number(point.longitude);

    return (Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180);
}

//API REQUESTS
async function getJSON(url) {
    const response = await fetch(url, {headers: {Accept: "application/json"}, cache: "no-store"});

    if (!response.ok) {
        throw new Error(`Request failed: ${url} (HTTP ${response.status})`);
    }
    return response.json();
}

//RESIDENT MONITORING MAP
function initializeMap() {
    if (typeof L === "undefined") {
        write("locationStatus", "Map services are unavailable. " + "You can still search for monitoring points.");
        return;
    }

    state.map = L.map("residentMap", {scrollWheelZoom: false}).setView([-1.286389, 36.817223], 11);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 19, attribution: "© OpenStreetMap contributors"}).addTo(state.map);
    state.markers = L.layerGroup().addTo(state.map);
}

//MONITORING LOCATION SEARCH
function filteredPoints() {
    const query = $("areaSearch").value.trim().toLowerCase();
    const filter = $("riskFilter").value;

    return state.locations.filter(point => {
        const localMatch = (String(point.name || "").toLowerCase().includes(query) || String(point.location_id).includes(query));    
        return (localMatch && (filter === "ALL" || riskOf(point) === filter));
    });
}

function renderSearchResults(points) {
    const container = $("searchResults");
    container.replaceChildren();

    if (!$("areaSearch").value.trim()) {
        return;
    }

    for (const point of points.slice(0, 7)) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "result-button";
        button.textContent = (`${point.name} · Point #${point.location_id}`);

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

//MAP MARKERS AND RISK DISPLAY
function renderMap() {
    const points = filteredPoints();
    write("pointCount", `${points.length} points`);
    renderSearchResults(points);

    if (!state.map || !state.markers) {
        return;
    }

    state.markers.clearLayers();

    for (const point of points) {
        if (!hasValidCoordinates(point)) {
            continue;
        }

        const latitude = Number(point.latitude);
        const longitude = Number(point.longitude);
        const category = riskOf(point);
        const selected = (Number(point.location_id) === Number(state.selected?.location_id));
        const markerColor = (colors[category] || colors.NO_DATA);

        const icon = L.divIcon({
            className: ("floodwatch-point-marker" + (selected ? " is-selected" : "")),
            html: (`<span class="point-marker-core" ` + `style="background:${markerColor}"></span>`),
            iconSize: [22, 22],
            iconAnchor: [11, 11],
        });

        const marker = L.marker([latitude, longitude], {icon, keyboard: true, title: `${point.name} - ${category}`, zIndexOffset: selected ? 1000 : 0});
        const tooltip = document.createElement("span");

        tooltip.textContent = (`${point.name} · ${category.replace("_", " ")}`);
        marker.bindTooltip(tooltip);
        marker.on("click", () => {
            selectPoint(point);
        });
        state.markers.addLayer(marker);
    }
}

//FLOOD ADVISORIES
function setAdvisory(point) {
    if (point.alert_title) {
        write("advisoryStatus", point.alert_title);
        write("advisoryMessage", point.alert_message || "");
        write("advisoryInstructions", point.alert_instructions || "");
    } else {
        write("advisoryStatus", "No currently active advisory");
        write("advisoryMessage", "No unexpired model-generated advisory " + "is associated with this observation point.");
        write("advisoryInstructions", "");
    }
}

//SELECTED MONITORING POINT DETAILS
function setPointDetails(point) {
    write("areaTitle", point.name);

    const category = riskOf(point);
    const badge = $("areaRisk");

    badge.className = (`risk-pill ${category === "NO_DATA" ? "neutral" : category}`);
    badge.textContent = (category === "NO_DATA" ? "Not assessed" : `${category} risk`);

    write("areaScore", number(point.risk_score, 3));
    write("scoreMeaning", explainRiskScore(point));
    write("assessmentTime", localTime(point.prediction_time));
    write("assessmentAge", assessmentFreshness(point.prediction_time));
    write("temperature", number(point.temperature, 1, " °C"));
    write("humidity", number(point.humidity, 1, "%"));
    write("rainfall", number(point.rainfall, 1, " mm"));
    write("weatherTime", `Weather timestamp: ${localTime(point.weather_timestamp)}`);
    write("assessmentCaveat", "This score describes a monitored observation point, " + "not your exact property. " + "Follow official local warnings for safety decisions.");

    setAdvisory(point);
}

//HISTORICAL CHART MANAGEMENT
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

//PRESERVE MISSING HISTORICAL DATES
// The database stores assessments on selected dates.
// We insert null placeholders between recorded dates.
// No rainfall or model scores are estimated for those missing dates.
// Chart.js uses these placeholders to distinguish consecutive records from historical gaps.

function expandRecordedDates(rows) {
    if (!rows.length) {
        return [];
    }

    const byDate = new Map(rows.filter(row => /^\d{4}-\d{2}-\d{2}$/.test(String(row.local_date))).map(row => [row.local_date, row]));
    const dates = [...byDate.keys()].sort();

    if (!dates.length) {
        return [];
    }

    const first = Date.parse(`${dates[0]}T00:00:00Z`);
    const last = Date.parse(`${dates[dates.length - 1]}T00:00:00Z`);
    const expanded = [];

    for (let time = first; time <= last; time += 86400000) {
        const date = new Date(time).toISOString().slice(0, 10);
        expanded.push(byDate.get(date) || {local_date: date});
    }
    return expanded;
}

//HISTORICAL CHART RENDERING
// Improvements:
// 1. Connect actual recorded assessments.
// 2. Dashed lines indicate missing intervening dates.
// 3. Do not generate rainfall measurements.
// 4. Do not shade unmeasured intervals.
// 5. Preserve separate rainfall and risk-score units.
// 6. Retain a consistent date axis.

function drawChart(canvasId, rows, valueKey, label, color, yMax) {
    if (typeof Chart === "undefined" || !rows.length) {
        return null;
    }

    const chartRows = expandRecordedDates(rows);
    const values = chartRows.map(row => {
        const value = row[valueKey];
        if (value === null || value === undefined || value === "") {
            return null;
        }
        const parsed = Number(value);

        return Number.isFinite(parsed) ? parsed : null;
    });

    if (!values.some(value => value !== null)) {
        return null;
    }

    const isRainfall = valueKey.startsWith("rain_");

    return new Chart(
        $(canvasId),
        {
            type: "line",
            data: {
                labels: chartRows.map(row => row.local_date),
                datasets: [{
                    label,
                    data: values,
                    borderColor: color,
                    backgroundColor: `${color}1C`,
                    borderWidth: 2,

                    // Do not shade missing historical intervals.
                    fill: false,

                    // Straight connections between observations.
                    tension: 0,
                    pointRadius: 4,
                    pointHoverRadius: 6,

                    // Connect actual observations across gaps.
                    spanGaps: true,

                    // Dashed lines indicate dates without recorded operational assessments.
                    segment: {
                        borderDash: context => {
                            const gap = (context.p1DataIndex - context.p0DataIndex);
                            return gap > 1 ? [6, 5] : [];
                        },
                    },
                }],
            },

            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {display: false},
                    tooltip: {
                        mode: "index",
                        intersect: false,

                        callbacks: {
                            label(context) {
                                const value = context.parsed.y;

                                return isRainfall
                                    ? (`${label}: ` + `${value.toFixed(2)} mm`)
                                    : (`${label}: ` + `${value.toFixed(3)}`);
                            },
                        },
                    },
                },

                scales: {
                    x: {
                        ticks: {maxTicksLimit: 6,},
                    },

                    y: {
                        beginAtZero: true,

                        ...(
                            yMax !== null ? {max: yMax} : {}
                        ),

                        ticks: {precision: 2,},
                        title: {
                            display: isRainfall,
                            text: isRainfall
                                ? "Accumulated precipitation (mm)"
                                : "",
                        },
                    },
                },
            },
        }
    );
}

// RETRIEVE HISTORICAL MONITORING RECORDS
// Model-score history: flood_risk_prediction.risk_score
// Rainfall history: flood_risk_prediction.prediction_data.predictors.rain_1d
// Each rainfall value is an antecedent 24-hour accumulation stored with its operational prediction.
async function loadHistory(locationId, serial) {
    destroyCharts();

    state.history = null;

    updateResidentDownloadPermission();

    write("riskHistoryFallback", "Loading recorded assessments…");
    write("weatherHistoryFallback", "Loading recorded rainfall accumulations…");

    try {
        const history = await getJSON(`/api/resident/locations/${locationId}/history?days=30`);

        // Ignore responses from previous selections.
        if (serial !== state.selectionSerial) {
            return;
        }

        const predictions = Array.isArray(history.predictions) ? history.predictions : [];
        const readings = Array.isArray(history.weather_readings) ? history.weather_readings : [];

        state.history = history;

        // Preserve verified-account download permissions.
        updateResidentDownloadPermission();

        //MODEL RISK SCORE HISTORY
        state.riskChart = drawChart("riskHistoryChart", predictions, "risk_score", "Model score", "#1456B8", 1);

        //RECORDED 24-HOUR RAINFALL ACCUMULATION
        state.weatherChart = drawChart("weatherHistoryChart", predictions, "rain_1d", "24-hour accumulated rainfall", "#087EAC", null);
        const hasRainfall = predictions.some(row => (row.rain_1d !== null && row.rain_1d !== undefined && row.rain_1d !== "" && Number.isFinite(Number(row.rain_1d))));

        //CHART FALLBACK MESSAGES
        write("riskHistoryFallback", predictions.length ? (state.riskChart ? "" : "Chart unavailable or no valid model scores.") : "No stored risk history for this point.");
        write("weatherHistoryFallback", hasRainfall ? (state.weatherChart ? "" : "Chart library unavailable.") : ("No recorded 24-hour rainfall " + "accumulation for this point."));
    } catch (error) {
        if (serial !== state.selectionSerial) {
            return;
        }
        console.error("History loading failed:", error);

        write("riskHistoryFallback", "Could not load recorded risk history.");
        write("weatherHistoryFallback", "Could not load recorded rainfall accumulation.");
    }
}

//SELECT MONITORING POINT
function selectPoint(point) {
    state.selected = point;
    state.selectionSerial++;

    setPointDetails(point);
    renderMap();
    loadHistory(point.location_id, state.selectionSerial);
}

//CLEAR SELECTED MONITORING POINT
function clearSelection() {
    state.selected = null;
    state.selectionSerial++;

    destroyCharts();

    state.history = null;

    updateResidentDownloadPermission();

    write("areaTitle", "Select a monitoring point");

    $("areaRisk").className = "risk-pill neutral";

    write("areaRisk", "Not selected");
    write("areaScore", "—");
    write("scoreMeaning", "Select a monitoring point to understand " + "its model-derived risk assessment.");
    write("assessmentTime", "No location selected");
    write("assessmentAge", "");
    write("temperature", "—");
    write("humidity", "—");
    write("rainfall", "—");
    write("weatherTime", "Weather timestamp: —");
    write("assessmentCaveat", "Choose a point using search, the map or " + "optional device location. Assessments describe " + "monitored points, not individual properties.");
    write("advisoryStatus", "No point selected");
    write("advisoryMessage", "Select a monitoring point to view unexpired " + "model-generated advisories, if any.");
    write("advisoryInstructions", "");
    write("riskHistoryFallback", "Select a point to view recorded history.");
    write("weatherHistoryFallback", "Select a point to view recorded " + "24-hour rainfall accumulations.");

    renderMap();
}

//NEARBY MONITORING POINTS
function rankedNearby(position) {
    return state.locations.filter(hasValidCoordinates).map(point => ({
            point,
            km: haversineKm(
                position,
                {
                    latitude: Number(point.latitude),
                    longitude: Number(point.longitude),
                }
            ),
        })).sort((a, b) => a.km - b.km);
}

function renderNearby() {
    const container = $("nearbyList");
    container.replaceChildren();

    if (!state.position) {
        return;
    }

    const nearest = rankedNearby(state.position).slice(0, 3);

    if (!nearest.length) {
        write("nearbyIntro", "No nearby monitoring coordinates are available.");
        return;
    }

    write(
        "nearbyIntro",
        nearest[0].km > 5 ? ("No monitored point is within 5 km. " + "These are the nearest reference points, " + "not an assessment of your property.") : ("Three closest monitored observation points. " + "Proximity does not imply identical flood risk."));

    for (const {point, km} of nearest) {
        const button = document.createElement("button");

        button.type = "button";
        button.className = "nearby-item";

        const info = document.createElement("span");
        const name = document.createElement("span");

        name.className = "nearby-name";
        name.textContent = point.name;

        const distance = document.createElement("small");

        distance.textContent = (`${km.toFixed(1)} km away · ` + `Point #${point.location_id}`);
        info.append(name, distance);
        const risk = document.createElement("span");

        risk.className = (`nearby-risk ${riskOf(point)}`);
        risk.textContent = (riskOf(point).replace("_", " "));

        button.append(info, risk);
        button.addEventListener("click", () => {
            selectPoint(point);

            if (state.map) {
                state.map.setView([Number(point.latitude), Number(point.longitude)], 14);
            }
        });
        container.append(button);
    }
}

//CLEAR DEVICE LOCATION
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
    write("locationStatus", "Your position has been cleared from this page. " + "Browser permissions are managed in your browser settings.");
    write("nearbyIntro", "Use your location to find nearby monitored points, " + "or choose one manually.");
    $("nearbyList").replaceChildren();
    clearSelection();
}

// DEVICE GEOLOCATION
function locateMe() {
    if (!navigator.geolocation) {
        write("locationStatus", "Your browser doesn't support location sharing. " + "You can choose an area manually.");
        return;
    }

    $("useLocation").disabled = true;
    write("locationStatus", "Waiting for browser location permission…");

    navigator.geolocation.getCurrentPosition(
        result => {
            $("useLocation").disabled = false;
            const {latitude, longitude, accuracy} = result.coords;
            state.position = {latitude, longitude};
            $("clearLocation").hidden = false;
            write("locationStatus", `Device position obtained ` + `(reported accuracy ±${Math.round(accuracy)} m). ` + "Used only on this page.");

            if (state.map) {
                if (state.locationMarker) {
                    state.map.removeLayer(state.locationMarker);
                }

                if (state.accuracyCircle) {
                    state.map.removeLayer(state.accuracyCircle);
                }

                state.locationMarker = L.circleMarker([latitude, longitude], {radius: 8, color: "#1755BA", fillColor: "#38BDF8", fillOpacity: 1, weight: 3}).addTo(state.map).bindTooltip("Your device-reported position");
                state.accuracyCircle = L.circle([latitude, longitude], {radius: accuracy, color: "#1755BA", fillOpacity: 0.06, weight: 1}).addTo(state.map);
                state.map.setView([latitude, longitude], 13);
            }

            renderNearby();
            const nearest = rankedNearby(state.position)[0];

            if (nearest && nearest.km <= 5) {
                selectPoint(nearest.point);
            } else {
                clearSelection();
            }
        },

        error => {
            $("useLocation").disabled = false;
            const messages = {
                1: ("Location permission denied. " + "You can choose an area manually."),
                2: ("Device position unavailable. " + "Choose an area manually."),
                3: ("Location request timed out. " + "Please try again."),
            };
            write("locationStatus", messages[error.code] || "Unable to retrieve location.");
        },

        {
            enableHighAccuracy: true,
            timeout: 12000,
            maximumAge: 60000
        }
    );
}

//VERIFIED RESIDENT CSV EXPORT
// The export includes stored rainfall accumulations, model risk scores and optional weather snapshots.
// Missing values are exported as empty cells.
// This client-side permission check complements server-side authorization.

function downloadHistoryCSV() {
    if (residentVerification?.can_download_csv !== true) {
        updateResidentDownloadPermission();
        return;
    }

    if (!state.history || !state.selected) {
        return;
    }

    const daily = new Map();

    // MODEL ASSESSMENTS AND RAINFALL ACCUMULATIONS
    for (const p of state.history.predictions || []) {
        daily.set(p.local_date, {score: p.risk_score, risk: p.risk_level, rain1d: p.rain_1d, rain3d: p.rain_3d, rain7d: p.rain_7d});
    }

    //EXISTING WEATHER SNAPSHOTS
    for (const w of state.history.weather_readings || []) {
        const entry = (daily.get(w.local_date) || {});
        entry.snapshotRain = w.rainfall;
        entry.temperature = w.temperature;
        daily.set(w.local_date, entry);
    }

    const serialize = value => (value === null || value === undefined ? "" : String(value));
    const rows = [["date", "model_score", "risk_level", "rain_1d_mm", "rain_3d_mm", "rain_7d_mm", "precipitation_snapshot_mm", "temperature_snapshot_c"]];

    for (const [date, record] of [...daily.entries()].sort(([a], [b]) => a.localeCompare(b))) {
        rows.push([date, serialize(record.score), serialize(record.risk), serialize(record.rain1d), serialize(record.rain3d), serialize(record.rain7d), serialize(record.snapshotRain), serialize(record.temperature)]);
    }

    const csv = rows.map(cells => cells.join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
    const link = document.createElement("a");

    link.href = url;
    link.download = (`floodwatch_location_${state.selected.location_id}_history.csv`);
    link.click();
    URL.revokeObjectURL(url);
}

//LOAD OPERATIONAL MONITORING DATA
async function loadDashboard() {
    try {
        const [overview, locations] = await Promise.all([getJSON("/api/overview"), getJSON("/api/locations")]);

        if (!Array.isArray(locations)) {
            throw new Error("Invalid monitoring-location response.");
        }

        state.overview = overview;
        state.locations = locations;

        write("freshness", `${assessmentFreshness(overview.latest_prediction)} · ` + `${localTime(overview.latest_prediction)}`);

        if (state.selected) {
            const updated = locations.find(
                point => (Number(point.location_id) === Number(state.selected.location_id)));

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
        console.error("FloodWatch monitoring request failed:", error);
        write("errorNotice", "Monitoring data is unavailable. " + "Please check the connection and try again.");
        $("errorNotice").hidden = false;
        write("freshness", "Monitoring data unavailable");
    }
}

//INITIALIZE RESIDENT DASHBOARD
// place_search.js remains responsible for geographic search fallback and its debounce.
document.addEventListener(
    "DOMContentLoaded",
    () => {
        initializeMap();
        loadResidentVerification();

        window.setInterval(() => {if (residentVerification) {renderResidentVerification();}}, 60000);

        $("areaSearch").addEventListener("input", renderMap);
        $("riskFilter").addEventListener("change", renderMap);
        $("chooseArea").addEventListener("click", () => $("areaSearch").focus());
        $("useLocation").addEventListener("click", locateMe);
        $("clearLocation").addEventListener("click", clearLocation);
        $("downloadHistory").addEventListener("click", downloadHistoryCSV);

        loadDashboard();
    }
);
