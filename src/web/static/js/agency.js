"use strict";

// use existing monitoring APIs; agency trends require a separate backend endpoint.
const AGENCY_API = {
    overview: "/api/overview",
    locations: "/api/locations",
    history: (id) => `/api/resident/locations/${encodeURIComponent(id)}/history?days=30`,
    trends: "/api/agency/trends",
    places: "/api/places/search",
};

const AGENCY_GIS = {
    wards: {url: "/static/gis/nairobi_wards.geojson", expected: 85, field: "ward_name"},
    subcounties: {url: "/static/gis/nairobi_subcounties.geojson", expected: 17, field: "subcounty_name"},
    roads: "/static/gis/nairobi_road_inundation_exposure.geojson",
};

const RISK_COLORS = {
    LOW: "#16855B",
    MODERATE: "#D99A1B",
    HIGH: "#C63B44",
    NO_DATA: "#8B9BAE",
};

const ROAD_RADIUS_KM = 3;
const MAX_NEARBY_ROADS = 5;

const agencyState = {
    points: [],
    overview: null,
    selected: null,
    selectionSerial: 0,
    wards: null,
    subcounties: null,
    map: null,
    markers: null,
    wardLayer: null,
    subcountyLayer: null,
    roadData: null,
    roadLayer: null,
    roadLoading: false,
    pointChart: null,
    trendChart: null,
    trendSerial: 0,
    trendsUnsupported: false,
    loading: false,
    geoSelection: {map: null, report: null},
    geoSerial: {map: 0, report: 0},
    placeMarker: null,
    reportPage: 1,
    reportPageSize: 12,
};


const agencyRainfallState = {
    chart: null,
    rows: [],
    windowKey: "rain_1d",
};

const AGENCY_RAINFALL_WINDOWS = {
    rain_1d: {hours: 24, label: "24-hour accumulated rainfall"},
    rain_3d: {hours: 72, label: "72-hour accumulated rainfall"},
    rain_7d: {hours: 168, label: "168-hour accumulated rainfall"}
};
const $ = (id) => document.getElementById(id);
const write = (id, value) => { $(id).textContent = String(value); };

function normalizeRisk(value) {
    const risk = String(value || "NO_DATA").trim().toUpperCase();
    return Object.hasOwn(RISK_COLORS, risk) ? risk : "NO_DATA";
}

const riskOf = (point) => normalizeRisk(point?.risk_level);
const riskLabel = (risk) => risk === "NO_DATA" ? "Not assessed" : risk[0] + risk.slice(1).toLowerCase();

function number(value, digits = 1, unit = "") {
    if (value === null || value === undefined || value === "") return "Not recorded";
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(digits)}${unit}` : "Not recorded";
}

function localTime(timestamp) {
    if (!timestamp) return "Not recorded";
    const date = new Date(timestamp);
    if (!Number.isFinite(date.getTime())) return "Invalid timestamp";
    return new Intl.DateTimeFormat("en-KE", {
        timeZone: "Africa/Nairobi",
        dateStyle: "medium",
        timeStyle: "short",
    }).format(date) + " EAT";
}

function assessmentFreshness(timestamp) {
    if (!timestamp) return "No assessment stored";
    const hours = (Date.now() - new Date(timestamp).getTime()) / 3600000;
    if (!Number.isFinite(hours)) return "Assessment timestamp unavailable";
    if (hours > 24) return "Historical assessment — not a current warning";
    if (hours < -1) return "Check assessment timestamp";
    return "Recorded within the past 24 hours";
}

function hasValidCoordinates(point) {
    const lat = point?.latitude, lon = point?.longitude;
    if (lat == null || lon == null || lat === "" || lon === "") return false;
    return Number.isFinite(Number(lat)) && Number.isFinite(Number(lon)) &&
        Math.abs(Number(lat)) <= 90 && Math.abs(Number(lon)) <= 180;
}

async function getJSON(url) {
    const response = await fetch(url, {
        headers: {Accept: "application/json"},
        credentials: "same-origin",
        cache: "no-store",
    });

    if (!response.ok) {
        const error = new Error(`Request failed: ${url} (HTTP ${response.status})`);
        error.status = response.status;
        throw error;
    }

    return response.json();
}

// GeoJSON coordinates are [longitude, latitude], unlike Leaflet's [latitude, longitude].
function inRing(lon, lat, ring) {
    if (!Array.isArray(ring) || ring.length < 4) return false;
    let inside = false;

    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const a = ring[i], b = ring[j];
        if ((a[1] > lat) !== (b[1] > lat) &&
            lon < (b[0] - a[0]) * (lat - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
    }

    return inside;
}

function inPolygon(lon, lat, rings) {
    return Array.isArray(rings) && rings.length > 0 && inRing(lon, lat, rings[0]) &&
        !rings.slice(1).some((hole) => inRing(lon, lat, hole));
}

function pointInGeometry(lon, lat, geometry) {
    if (!geometry) return false;
    if (geometry.type === "Polygon") return inPolygon(lon, lat, geometry.coordinates);
    if (geometry.type === "MultiPolygon") {
        return geometry.coordinates.some((polygon) => inPolygon(lon, lat, polygon));
    }
    return false;
}

function assignAdministrativeAreas(points, wards, subcounties) {
    const wardFeatures = Array.isArray(wards) ? wards : wards?.features || [];
    const subcountyFeatures = Array.isArray(subcounties) ? subcounties : subcounties?.features || [];

    return points.map((point) => {
        let ward = null, subcounty = null;

        if (hasValidCoordinates(point)) {
            const lon = Number(point.longitude), lat = Number(point.latitude);
            const w = wardFeatures.find((f) => pointInGeometry(lon, lat, f.geometry));
            const s = subcountyFeatures.find((f) => pointInGeometry(lon, lat, f.geometry));

            ward = w?.properties?.ward_name || null;
            subcounty = s?.properties?.subcounty_name || null;
        }

        return {...point, _ward: ward, _subcounty: subcounty};
    });
}

async function fetchBoundary(level) {
    const source = AGENCY_GIS[level], data = await getJSON(source.url);

    if (data?.type !== "FeatureCollection" || !Array.isArray(data.features) ||
        data.features.length !== source.expected) {
        throw new Error(`${level}: expected ${source.expected} GeoJSON features`);
    }

    for (const feature of data.features) {
        if (!feature.properties?.[source.field] ||
            !["Polygon", "MultiPolygon"].includes(feature.geometry?.type)) {
            throw new Error(`${level}: missing name or invalid geometry`);
        }
    }

    return data;
}

function boundaryStyle(level) {
    return level === "wards"
        ? {color: "#4188C5", weight: 1.2, opacity: 0.7, fillColor: "#74B7E8", fillOpacity: 0.035}
        : {color: "#164C80", weight: 2.5, opacity: 0.9, fillColor: "#74B7E8", fillOpacity: 0.015};
}

function createBoundaryLayer(data, level) {
    const source = AGENCY_GIS[level], normal = boundaryStyle(level);

    return L.geoJSON(data, {
        pane: "agency-boundaries",
        style: () => ({...normal}),

        onEachFeature(feature, layer) {
            const name = String(feature.properties[source.field]);
            const tooltip = document.createElement("span");
            tooltip.textContent = name;

            layer.bindTooltip(tooltip, {sticky: true, direction: "auto"});
            layer.on("mouseover", () => layer.setStyle({
                ...normal, weight: normal.weight + 1.4, fillOpacity: 0.11,
            }));
            layer.on("mouseout", () => layer.setStyle(normal));

            layer.on("click", () => {
                agencyState.map.fitBounds(layer.getBounds(), {
                    padding: [25, 25], maxZoom: 14,
                });

                write("layerStatus", `${level === "wards" ? "Ward" : "Subcounty"}: ${name}. ` +
                    "Administrative boundary only; model risk belongs to observation points.");
            });
        },
    });
}

async function loadBoundaries() {
    const levels = ["wards", "subcounties"];
    const results = await Promise.allSettled(levels.map((level) =>
        agencyState[level] ? Promise.resolve(agencyState[level]) : fetchBoundary(level)));

    results.forEach((result, index) => {
        const level = levels[index];
        const checkbox = $(level === "wards" ? "showWards" : "showSubcounties");

        if (result.status === "fulfilled") {
            agencyState[level] = result.value;
            checkbox.disabled = false;

            const key = level === "wards" ? "wardLayer" : "subcountyLayer";
            if (agencyState.map && !agencyState[key]) {
                agencyState[key] = createBoundaryLayer(result.value, level);
            }
        } else {
            checkbox.checked = false;
            checkbox.disabled = true;
            console.warn(`${level} GIS unavailable:`, result.reason);
        }
    });

    const count = levels.filter((level) => Boolean(agencyState[level])).length;

    write("layerStatus", count === 2
        ? "85 wards and 17 subcounties loaded. Boundaries do not indicate area-wide flood risk."
        : `${count}/2 administrative layers available. Some comparisons cannot be displayed.`);

    toggleBoundaries();
}

function initializeMap() {
    if (typeof L === "undefined") {
        write("layerStatus", "Map unavailable. Monitoring records remain accessible.");
        return;
    }

    agencyState.map = L.map("agencyMap", {
        scrollWheelZoom: false, preferCanvas: true,
    }).setView([-1.286389, 36.817223], 11);

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "© OpenStreetMap contributors",
    }).addTo(agencyState.map);

    agencyState.map.createPane("agency-boundaries").style.zIndex = "350";
    agencyState.map.createPane("agency-roads").style.zIndex = "390";
    agencyState.markers = L.layerGroup().addTo(agencyState.map);

    $("showRoadExposure").disabled = false;

    agencyState.map.on("moveend", () => {
        if ($("showRoadExposure").checked && agencyState.roadData) renderNearbyRoads();
    });
}

function toggleBoundaries() {
    if (!agencyState.map) return;

    for (const [id, layer] of [
        ["showWards", agencyState.wardLayer],
        ["showSubcounties", agencyState.subcountyLayer],
    ]) {
        if (!layer) continue;
        if ($(id).checked && !agencyState.map.hasLayer(layer)) layer.addTo(agencyState.map);
        else if (!$(id).checked && agencyState.map.hasLayer(layer)) agencyState.map.removeLayer(layer);
    }
}

function renderMap() {
    const points = filteredPoints();
    write("visibleCount", `${points.length} points`);

    if (!agencyState.map || !agencyState.markers) return;
    agencyState.markers.clearLayers();

    for (const point of points) {
        if (!hasValidCoordinates(point)) continue;

        const risk = riskOf(point);
        const selected = String(point.location_id) === String(agencyState.selected?.location_id);

        const icon = L.divIcon({
            className: "floodwatch-point-marker" + (selected ? " is-selected" : ""),
            html: `<span class="point-marker-core" style="--point-color:${RISK_COLORS[risk]}"></span>`,
            iconSize: [22, 22],
            iconAnchor: [11, 11],
        });

        const marker = L.marker([Number(point.latitude), Number(point.longitude)], {
            icon,
            keyboard: true,
            title: String(point.name || `Point #${point.location_id}`),
            zIndexOffset: selected ? 1000 : 0,
        });

        const tooltip = document.createElement("span");
        tooltip.textContent = `${point.name || "Monitoring point"} · ${riskLabel(risk)}`;

        marker.bindTooltip(tooltip);
        marker.on("click", () => selectPoint(point));
        agencyState.markers.addLayer(marker);
    }

    const shown = agencyState.map.hasLayer(agencyState.markers);

    if ($("showPoints").checked && !shown) agencyState.markers.addTo(agencyState.map);
    else if (!$("showPoints").checked && shown) agencyState.map.removeLayer(agencyState.markers);
}

function fillSelect(id, names, placeholder) {
    const select = $(id), previous = select.value;
    select.replaceChildren(new Option(placeholder, "ALL"));

    const unique = [...new Set(names.filter(Boolean))].sort((a, b) => a.localeCompare(b));
    for (const name of unique) select.add(new Option(name, name));

    select.value = [...select.options].some((option) => option.value === previous) ? previous : "ALL";
}

function updateWardOptions() {
    const subcounty = $("subcountyFilter").value;
    const wards = agencyState.points
        .filter((point) => subcounty === "ALL" || point._subcounty === subcounty)
        .map((point) => point._ward);

    fillSelect("wardFilter", wards, "All wards");
}

function matchesAreaName(point, term) {
    const fields = [point.name, point.location_id, point._ward, point._subcounty];
    return fields.some((value) =>
        String(value ?? "").toLowerCase().includes(term));
}

function administrativePoints() {
    const risk = $("riskFilter").value;
    const subcounty = $("subcountyFilter").value;
    const ward = $("wardFilter").value;

    return agencyState.points.filter((point) =>
        (risk === "ALL" || riskOf(point) === risk) &&
        (subcounty === "ALL" || point._subcounty === subcounty) &&
        (ward === "ALL" || point._ward === ward));
}

function haversineKm(place, point) {
    const rad = Math.PI / 180;
    const dLat = (Number(point.latitude) - place.latitude) * rad;
    const dLon = (Number(point.longitude) - place.longitude) * rad;
    const lat1 = place.latitude * rad;
    const lat2 = Number(point.latitude) * rad;

    const h = Math.sin(dLat / 2) ** 2 +
        Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;

    return 2 * 6371.0088 * Math.asin(Math.min(1, Math.sqrt(h)));
}

function nearestPoints(place, points, limit = 5) {
    return points.filter(hasValidCoordinates)
        .map((point) => ({point, km: haversineKm(place, point)}))
        .sort((a, b) => a.km - b.km)
        .slice(0, limit);
}

function filteredPoints() {
    const base = administrativePoints();
    const term = $("searchPoints").value.trim().toLowerCase();

    if (!term) return base;

    const selection = agencyState.geoSelection.map;

    if (selection?.query === term) {
        return nearestPoints(selection.place, base, 5)
            .map(({point}) => point);
    }

    const matches = base.filter((point) => matchesAreaName(point, term));

    // An unmatched estate name must not remove every monitoring marker.
    return matches.length ? matches : base;
}

// The report search never uses the map's filters or search text.
function reportPoints() {
    const term = $("reportSearch").value.trim().toLowerCase();
    if (!term) return [];

    const selection = agencyState.geoSelection.report;

    if (selection?.query === term) {
        return nearestPoints(selection.place, agencyState.points, 5)
            .map(({point}) => point);
    }

    return agencyState.points.filter((point) =>
        matchesAreaName(point, term));
}

function renderMapSearchStatus() {
    const term = $("searchPoints").value.trim().toLowerCase();
    const selection = agencyState.geoSelection.map;

    if (!term) {
        write("mapSearchStatus",
            "Search monitoring points, wards or subcounties; " +
            "use Find Nairobi area for estates and landmarks.");
    } else if (selection?.query === term) {
        write("mapSearchStatus",
            `Showing nearby monitoring points to ${selection.place.name}; ` +
            "not a prediction for that place.");
    } else if (administrativePoints().some((p) =>
        matchesAreaName(p, term))) {
        write("mapSearchStatus", "Showing monitoring records matching your search.");
    } else {
        write("mapSearchStatus",
            `No point or administrative name matches “${term}”. ` +
            "Choose Find Nairobi area to locate nearby monitoring points.");
    }
}

function normalizePlace(raw) {
    const latitude = Number(raw.latitude ?? raw.lat);
    const longitude = Number(raw.longitude ?? raw.lon);

    if (!hasValidCoordinates({latitude, longitude})) return null;

    // Verify Nairobi administrative containment when GIS polygons are available.
    const boundaries = agencyState.subcounties?.features ||
        agencyState.wards?.features;

    if (boundaries?.length && !boundaries.some((feature) =>
        pointInGeometry(longitude, latitude, feature.geometry))) {
        return null;
    }

    return {
        latitude,
        longitude,
        name: String(raw.name || raw.display_name || "Nairobi location"),
        description: String(raw.display_name || raw.name || "Nairobi, Kenya"),
        ward: raw.ward_name || null,
    };
}

async function findNairobiPlace(kind) {
    const input = $(kind === "map" ? "searchPoints" : "reportSearch");
    const results = $(kind === "map" ? "mapPlaceResults" : "reportPlaceResults");
    const statusId = kind === "map" ? "mapSearchStatus" : "reportStatus";
    const button = $(kind === "map" ? "findMapPlace" : "findReportPlace");
    const query = input.value.trim();

    if (!query) return;

    const serial = ++agencyState.geoSerial[kind];

    button.disabled = true;
    results.replaceChildren();
    write(statusId, `Searching Nairobi for “${query}”…`);

    try {
        const data = await getJSON(
            `${AGENCY_API.places}?q=${encodeURIComponent(query)}`
        );

        if (serial !== agencyState.geoSerial[kind]) return;

        const places = (Array.isArray(data.results) ? data.results : [])
            .map(normalizePlace)
            .filter(Boolean)
            .slice(0, 4);

        if (!places.length) {
            write(statusId,
                "No Nairobi places found. Try a different ward, estate or landmark.");
            return;
        }

        if (places.length === 1) {
            selectNairobiPlace(kind, places[0], query);
            return;
        }

        write(statusId, "Choose the intended Nairobi location:");

        for (const place of places) {
            const choice = document.createElement("button");
            choice.type = "button";
            choice.className = "result-button";
            choice.textContent = place.description;

            choice.addEventListener("click", () =>
                selectNairobiPlace(kind, place, query));

            results.append(choice);
        }

    } catch (error) {
        if (serial !== agencyState.geoSerial[kind]) return;

        console.error("Nairobi geographic lookup failed:", error);

        write(statusId,
            "Nairobi place search unavailable. " +
            "Search by a monitoring point, ward or subcounty instead.");
    } finally {
        if (serial === agencyState.geoSerial[kind]) button.disabled = false;
    }
}

function selectNairobiPlace(kind, place, query) {
    agencyState.geoSelection[kind] = {
        query: query.trim().toLowerCase(),
        place,
    };

    $(kind === "map" ? "mapPlaceResults" : "reportPlaceResults")
        .replaceChildren();

    if (kind === "report") {
        agencyState.reportPage = 1;
        renderRecords();
        return;
    }

    if (agencyState.map) {
        if (agencyState.placeMarker) {
            agencyState.map.removeLayer(agencyState.placeMarker);
        }

        agencyState.placeMarker = L.circleMarker(
            [place.latitude, place.longitude],
            {
                radius: 9,
                color: "#122b4a",
                weight: 3,
                fillColor: "#38BDF8",
                fillOpacity: 1,
            }
        ).addTo(agencyState.map);

        agencyState.placeMarker.bindTooltip(
            `Searched place: ${place.name} (not a prediction)`
        );

        agencyState.map.setView(
            [place.latitude, place.longitude], 13
        );
    }

    const results = $("mapNearbyResults");
    results.replaceChildren();

    const nearby = nearestPoints(place, administrativePoints(), 5);

    for (const {point, km} of nearby) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "nearby-item";

        button.textContent =
            `${point.name} · ${km.toFixed(1)} km away · ` +
            riskLabel(riskOf(point));

        button.addEventListener("click", () => selectPoint(point));
        results.append(button);
    }

    renderMap();
    renderMapSearchStatus();
    renderAdvisories();

    if (!nearby.length) {
        write("mapSearchStatus",
            "No nearby monitoring points match the active filters.");
    }
}

function resetGeoSearch(kind) {
    ++agencyState.geoSerial[kind];
    agencyState.geoSelection[kind] = null;

    $(kind === "map" ? "mapPlaceResults" : "reportPlaceResults")
        .replaceChildren();

    if (kind === "map") {
        $("mapNearbyResults").replaceChildren();

        if (agencyState.map && agencyState.placeMarker) {
            agencyState.map.removeLayer(agencyState.placeMarker);
            agencyState.placeMarker = null;
        }

        $("findMapPlace").disabled = !$("searchPoints").value.trim();
        renderFiltered();
        renderMapSearchStatus();
    } else {
        agencyState.reportPage = 1;
        $("findReportPlace").disabled = !$("reportSearch").value.trim();
        renderRecords();
    }
}

// Statistics count monitored points, not the people or properties at risk.
function summarize(points) {
    return {
        total: points.length,
        assessed: points.filter((p) => riskOf(p) !== "NO_DATA").length,
        high: points.filter((p) => riskOf(p) === "HIGH").length,
        advisories: points.filter((p) => Boolean(p.alert_title)).length,
    };
}

function renderSummary() {
    const stats = summarize(agencyState.points);

    write("totalPoints", stats.total);
    write("assessedPoints", stats.assessed);
    write("highPoints", stats.high);
    write("activeAdvisories", stats.advisories);

    const timestamps = agencyState.points
        .map((p) => p.prediction_time)
        .filter(Boolean)
        .map((timestamp) => new Date(timestamp).getTime())
        .filter(Number.isFinite);

    const latest = agencyState.overview?.latest_prediction ||
        (timestamps.length ? new Date(Math.max(...timestamps)).toISOString() : null);

    write("freshness", `${assessmentFreshness(latest)} · ${localTime(latest)}`);
}

function groupPoints(points, type) {
    const key = type === "ward" ? "_ward" : "_subcounty";
    const groups = new Map();

    for (const point of points) {
        const name = point[key] || "Unassigned monitoring points";

        if (!groups.has(name)) {
            groups.set(name, {
                name, total: 0, HIGH: 0, MODERATE: 0, LOW: 0, NO_DATA: 0,
            });
        }

        const group = groups.get(name);
        group.total++;
        group[riskOf(point)]++;
    }

    return [...groups.values()].sort((a, b) =>
        b.HIGH - a.HIGH || b.total - a.total || a.name.localeCompare(b.name));
}

const appendCell = (row, value) => {
    const cell = document.createElement("td");
    cell.textContent = String(value ?? "—");
    row.append(cell);
    return cell;
};

function tableMessage(id, columns, message) {
    const body = $(id);
    const row = document.createElement("tr");

    body.replaceChildren();
    appendCell(row, message).colSpan = columns;
    body.append(row);
}

function renderComparisons() {
    const type = $("compareBy").value;

    if (!(type === "ward" ? agencyState.wards : agencyState.subcounties)) {
        tableMessage("comparisonRows", 6, "Required GIS boundaries unavailable; comparison cannot be verified.");
        return;
    }

    const groups = groupPoints(administrativePoints(), type);

    if (!groups.length) {
        tableMessage("comparisonRows", 6, "No monitoring points match the filters.");
        return;
    }

    const body = $("comparisonRows");
    body.replaceChildren();

    for (const group of groups) {
        const row = document.createElement("tr");

        [group.name, group.total, group.HIGH, group.MODERATE, group.LOW, group.NO_DATA]
            .forEach((item) => appendCell(row, item));

        body.append(row);
    }
}

function renderRecords() {
    const term = $("reportSearch").value.trim();
    const points = reportPoints();
    const body = $("recordRows");

    body.replaceChildren();

    $("downloadCSV").disabled = !points.length;
    $("downloadPDF").disabled = !points.length;

    if (!term) {
        tableMessage("recordRows", 7,
            "Search for a ward, subcounty, area or monitoring point to view reports.");

        write("tableCount",
            "No report selected. Monitoring records are hidden until you search.");

        write("reportStatus",
            "Search by ward, subcounty or monitoring point name. " +
            "For an estate or landmark, choose Find nearby points.");

        $("reportPagination").hidden = true;
        return;
    }

    if (!points.length) {
        tableMessage("recordRows", 7,
            "No direct match. Use Find nearby points for an estate or landmark.");

        write("tableCount", "No report records selected.");

        write("reportStatus",
            "Try a ward/subcounty name, or find nearby points " +
            "for a Nairobi estate or landmark.");

        $("reportPagination").hidden = true;
        return;
    }

    const pages = Math.ceil(points.length / agencyState.reportPageSize);

    agencyState.reportPage = Math.max(
        1, Math.min(agencyState.reportPage, pages)
    );

    const start = (agencyState.reportPage - 1) * agencyState.reportPageSize;

    const pagePoints = points.slice(
        start, start + agencyState.reportPageSize
    );

    const selection = agencyState.geoSelection.report;

    write("tableCount",
        `${points.length} matching points · showing ` +
        `${start + 1}–${start + pagePoints.length}. ` +
        `Exports include all ${points.length}.`);

    write("reportStatus",
        selection?.query === term.toLowerCase()
            ? `Nearest ${points.length} monitored points to ` +
              `${selection.place.name}; not a prediction for the searched place.`
            : `Reporting on monitoring locations matching “${term}”.`);

    $("reportPagination").hidden = pages <= 1;

    write("reportPageStatus", `Page ${agencyState.reportPage} of ${pages}`);

    $("prevReportPage").disabled = agencyState.reportPage === 1;
    $("nextReportPage").disabled = agencyState.reportPage === pages;

    for (const point of pagePoints) {
        const row = document.createElement("tr");

        [
            point.name || `Point #${point.location_id}`,
            point._subcounty || "Unassigned",
            point._ward || "Unassigned",
            riskLabel(riskOf(point)),
            number(point.risk_score, 3),
            localTime(point.prediction_time),
        ].forEach((item) => appendCell(row, item));

        const cell = document.createElement("td");
        const button = document.createElement("button");

        button.type = "button";
        button.textContent = "View";

        button.addEventListener("click", () =>
            selectPoint(point, true));

        cell.append(button);
        row.append(cell);
        body.append(row);
    }
}

function selectPoint(point, scroll = false) {
    agencyState.selected = point;
    agencyState.selectionSerial++;

    $("selectedPrompt").hidden = true;
    $("selectedDetails").hidden = false;

    const risk = riskOf(point);
    const badge = $("pointRisk");

    write("pointName", point.name || `Point #${point.location_id}`);
    badge.className = `agency-risk-badge ${risk}`;
    badge.textContent = riskLabel(risk);

    write("pointScore", number(point.risk_score, 3));
    write("pointTime", `${localTime(point.prediction_time)} · ${assessmentFreshness(point.prediction_time)}`);
    write("pointArea", [point._ward, point._subcounty].filter(Boolean).join(" · ") || "Area unavailable");
    write("pointRain", number(point.rainfall, 1, " mm"));
    write("pointTemp", number(point.temperature, 1, " °C"));

    write("pointAdvisory", point.alert_title
        ? `${point.alert_title}. ${point.alert_message || ""}`
        : "No active model-generated advisory associated with this observation point.");

    renderMap();

    if (agencyState.map && hasValidCoordinates(point)) {
        agencyState.map.panTo([Number(point.latitude), Number(point.longitude)]);
    }

    if (scroll) {
        $("agencyFocusTitle").scrollIntoView({
            behavior: "smooth", block: "start",
        });
    }

    loadPointHistory(point.location_id, agencyState.selectionSerial);
}


function destroyPointChart() {
    if (agencyState.pointChart) {
        agencyState.pointChart.destroy();
        agencyState.pointChart = null;
    }

    if (agencyRainfallState.chart) {
        agencyRainfallState.chart.destroy();
        agencyRainfallState.chart = null;
    }

    agencyRainfallState.rows = [];

    const status = document.getElementById("rainfallHistoryStatus");

    if (status) {
        status.textContent = "Select a monitoring point to view recorded rainfall.";
    }
}

function expandAgencyAssessmentDates(rows) {
    const byDate = new Map();

    for (const row of rows) {
        const date = String(row.local_date || "");

        if (/^\d{4}-\d{2}-\d{2}$/.test(date)) {
            byDate.set(date, row);
        }
    }

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

//Update rainfall window controls
function updateAgencyRainfallControls() {
    const buttons = document.querySelectorAll("[data-rainfall-window]");

    for (const button of buttons) {
        const active = (button.dataset.rainfallWindow === agencyRainfallState.windowKey);

        button.setAttribute("aria-pressed", String(active));
        button.classList.toggle("is-active", active);
    }
}

//Render the selected rainfall accumulation window
function renderAgencyRainfallChart() {
    const canvas = document.getElementById("rainfallHistoryChart");
    const status = document.getElementById("rainfallHistoryStatus");

    if (!canvas || !status) {
        return;
    }

    // Destroy the previous window's chart.
    if (agencyRainfallState.chart) {
        agencyRainfallState.chart.destroy();
        agencyRainfallState.chart = null;
    }

    updateAgencyRainfallControls();

    const selectedWindow = AGENCY_RAINFALL_WINDOWS[agencyRainfallState.windowKey];
    const rows = agencyRainfallState.rows;

    if (!rows.length) {
        status.textContent = agencyState.selected ? "No recorded rainfall assessments for this point." : "Select a monitoring point to view recorded rainfall.";
        return;
    }

    const chartRows = expandAgencyAssessmentDates(rows);
    const values = chartRows.map(row => {
        const value = row[agencyRainfallState.windowKey];

        if (value === null || value === undefined || value === "") {
            return null;
        }

        const numeric = Number(value);

        return Number.isFinite(numeric) && numeric >= 0 ? numeric : null;
    });

    const recordedCount = values.filter(value => value !== null).length;

    if (recordedCount === 0) {
        status.textContent =`No recorded ${selectedWindow.hours}-hour ` + "rainfall accumulations for this point.";
        return;
    }

    if (typeof Chart === "undefined") {
        status.textContent = "Chart library unavailable.";
        return;
    }

    agencyRainfallState.chart = new Chart(
        canvas,
        {
            type: "line",
            data: {
                labels: chartRows.map(row => row.local_date),
                datasets: [{
                    label: selectedWindow.label,
                    data: values,
                    borderColor: "#087EAC",
                    backgroundColor: "#087EAC1C",
                    borderWidth: 2,
                    fill: false,
                    tension: 0,
                    pointRadius: 4,
                    pointHoverRadius: 6,

                    // Connect recorded observations without inventing values for missing dates.
                    spanGaps: true,
                    segment: {
                        borderDash: context => {
                            const gap = (context.p1DataIndex - context.p0DataIndex);
                            return gap > 1
                                ? [6, 5]
                                : [];
                        },
                    },
                }],
            },

            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: "nearest",
                    intersect: false,
                },

                plugins: {
                    legend: {display: false},
                    tooltip: {
                        callbacks: {
                            label(context) {
                                return (`${selectedWindow.label}: ` + `${context.parsed.y.toFixed(2)} mm`);
                            },
                        },
                    },
                },

                scales: {
                    x: {
                        ticks: {maxTicksLimit: 8},
                        title: {
                            display: true,
                            text: "Assessment date",
                        },
                    },

                    y: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: "Accumulated precipitation (mm)",
                        },
                        ticks: {precision: 2},
                    },
                },
            },
        }
    );

    status.textContent =
        `${recordedCount} recorded ${selectedWindow.hours}-hour ` +
        "rainfall accumulations. Dashed connections indicate " +
        "missing assessment dates; no values were estimated.";
}


// ---------------------------------------------------------
// Change rainfall accumulation window
// ---------------------------------------------------------

function setAgencyRainfallWindow(windowKey) {
    if (
        !Object.prototype.hasOwnProperty.call(
            AGENCY_RAINFALL_WINDOWS,
            windowKey
        )
    ) {
        return;
    }

    agencyRainfallState.windowKey = windowKey;

    // Reuse previously fetched PostgreSQL history.
    renderAgencyRainfallChart();
}



async function loadPointHistory(locationId, serial) {
    destroyPointChart();

    write(
        "historyStatus",
        "Loading recorded model scores…"
    );

    write(
        "rainfallHistoryStatus",
        "Loading stored rainfall accumulations…"
    );

    try {
        const history = await getJSON(
            AGENCY_API.history(locationId)
        );

        // Discard results from a previous selection.
        if (serial !== agencyState.selectionSerial) {
            return;
        }

        const rows = Array.isArray(history.predictions)
            ? history.predictions
            : [];

        // FIX: renderAgencyRainfallChart reads .rows,
        // not .history.
        agencyRainfallState.rows = rows;

        // Both charts reuse the same PostgreSQL response.
        renderAgencyRainfallChart();

        if (!rows.length) {
            write(
                "historyStatus",
                "No recorded model-score history for this monitoring point."
            );
            return;
        }

        if (typeof Chart === "undefined") {
            write(
                "historyStatus",
                "Chart library unavailable."
            );
            return;
        }

        const chartRows = expandAgencyAssessmentDates(rows);

        const scores = chartRows.map(row => {
            const value = row.risk_score;

            if (
                value === null ||
                value === undefined ||
                value === ""
            ) {
                return null;
            }

            const numeric = Number(value);

            return Number.isFinite(numeric)
                ? numeric
                : null;
        });

        agencyState.pointChart = new Chart(
            $("pointHistoryChart"),
            {
                type: "line",

                data: {
                    labels: chartRows.map(
                        row => row.local_date
                    ),

                    datasets: [{
                        label: "Model score",
                        data: scores,

                        borderColor: "#1755BA",
                        backgroundColor: "#1755BA16",

                        borderWidth: 2,
                        fill: false,
                        tension: 0,
                        pointRadius: 4,
                        pointHoverRadius: 6,

                        spanGaps: true,

                        // Dashed lines across dates without
                        // recorded assessments.
                        segment: {
                            borderDash: context => {
                                const gap =
                                    context.p1DataIndex -
                                    context.p0DataIndex;

                                return gap > 1
                                    ? [6, 5]
                                    : [];
                            },
                        },
                    }],
                },

                options: {
                    responsive: true,
                    maintainAspectRatio: false,

                    plugins: {
                        legend: {
                            display: false,
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
                            max: 1,
                        },
                    },
                },
            }
        );

        write(
            "historyStatus",
            `${rows.length} recorded daily assessments. ` +
            "Dashed connections identify missing dates; " +
            "no intermediate scores were estimated."
        );

    } catch (error) {
        if (serial !== agencyState.selectionSerial) {
            return;
        }

        console.error(
            "Point history unavailable:",
            error
        );

        write(
            "historyStatus",
            "Unable to load recorded model history."
        );

        write(
            "rainfallHistoryStatus",
            "Unable to load rainfall history. " +
            (
                error.status
                    ? `History API returned HTTP ${error.status}.`
                    : "Check the browser console."
            )
        );
    }
}

function renderAdvisories() {
    const list = $("advisoryList");
    list.replaceChildren();

    const advisories = filteredPoints()
        .filter((p) => Boolean(p.alert_title))
        .slice(0, 5);

    if (!advisories.length) {
        const item = document.createElement("li");
        item.textContent = "No active model advisories among the filtered monitoring points.";
        list.append(item);
        return;
    }

    for (const point of advisories) {
        const item = document.createElement("li");
        const button = document.createElement("button");
        const detail = document.createElement("small");

        button.type = "button";
        button.textContent = point.alert_title;
        button.addEventListener("click", () => selectPoint(point, true));
        detail.textContent = `${point.name || "Monitoring point"} · ${riskLabel(riskOf(point))}`;

        item.append(button, detail);
        list.append(item);
    }
}

// Road evidence is historical inundation-map agreement, NOT the live LOW/MODERATE/HIGH model risk.
function roadEvidence(count) {
    if (count <= 2) {
        return {label: "Limited mapped evidence", color: "#79B6EB", className: "limited"};
    }
    if (count <= 4) {
        return {label: "Moderate mapped evidence", color: "#4679C0", className: "moderate"};
    }
    return {label: "Stronger mapped evidence", color: "#442E88", className: "stronger"};
}

async function fetchRoadExposure() {
    const data = await getJSON(AGENCY_GIS.roads);

    if (data?.type !== "FeatureCollection" || !Array.isArray(data.features)) {
        throw new Error("Invalid road-exposure GeoJSON");
    }

    for (const feature of data.features) {
        const count = Number(feature.properties?.agreement_count);

        if (!Number.isInteger(count) || count < 1 || count > 6 ||
            !["LineString", "MultiLineString"].includes(feature.geometry?.type)) {
            throw new Error("Invalid road geometry or agreement count");
        }
    }

    return data;
}

function createRoadLayer(data) {
    const renderer = L.canvas({pane: "agency-roads", padding: 0.5});

    return L.geoJSON(data, {
        pane: "agency-roads",
        renderer,

        style(feature) {
            return {
                color: roadEvidence(Number(feature.properties.agreement_count)).color,
                weight: 4,
                opacity: 0.9,
            };
        },

        onEachFeature(feature, layer) {
            const props = feature.properties;
            const name = String(props.road_name || "Unnamed road");
            const evidence = roadEvidence(Number(props.agreement_count));
            const tooltip = document.createElement("span");

            tooltip.textContent = `${name} — ${evidence.label}`;
            layer.bindTooltip(tooltip, {sticky: true});

            layer.on("click", () => {
                write("roadExposureStatus", `${name}: ` +
                    `${evidence.label.toLowerCase()} in historical inundation mapping. ` +
                    "This does not confirm current flooding or a road closure.");
            });
        },
    });
}

function coordinateKilometres(coordinate, origin) {
    const lat = Number(coordinate[1]);
    const lon = Number(coordinate[0]);
    const lonScale = 111.195 * Math.cos(origin.lat * Math.PI / 180);

    return {
        x: (lon - origin.lng) * lonScale,
        y: (lat - origin.lat) * 111.195,
    };
}

function segmentDistanceKm(a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const squared = dx * dx + dy * dy;

    if (squared === 0) return Math.hypot(a.x, a.y);

    const t = Math.max(0, Math.min(1, -(a.x * dx + a.y * dy) / squared));
    return Math.hypot(a.x + t * dx, a.y + t * dy);
}

function nearestDistanceKm(feature, origin) {
    const lines = feature.geometry.type === "LineString"
        ? [feature.geometry.coordinates]
        : feature.geometry.coordinates;

    let minimum = Infinity;

    for (const line of lines) {
        for (let index = 0; index < line.length; index++) {
            const current = coordinateKilometres(line[index], origin);

            if (index === 0) {
                minimum = Math.min(minimum, Math.hypot(current.x, current.y));
                continue;
            }

            const previous = coordinateKilometres(line[index - 1], origin);
            minimum = Math.min(minimum, segmentDistanceKm(current, previous));
        }
    }

    return minimum;
}

function renderNearbyRoads() {
    const list = $("roadExposureList");
    list.replaceChildren();

    if (!agencyState.roadData || !agencyState.map || !$("showRoadExposure").checked) return;

    const origin = agencyState.map.getCenter();

    const candidates = agencyState.roadData.features
        .map((feature) => ({feature, distance: nearestDistanceKm(feature, origin)}))
        .filter((item) => item.distance <= ROAD_RADIUS_KM)
        .sort((a, b) => a.distance - b.distance);

    const seen = new Set();
    const selected = [];

    for (const candidate of candidates) {
        const props = candidate.feature.properties;
        const name = String(props.road_name || "Unnamed road");
        const key = name === "Unnamed road" ? String(props.segment_id) : name.toLowerCase();

        if (seen.has(key)) continue;

        seen.add(key);
        selected.push(candidate);

        if (selected.length >= MAX_NEARBY_ROADS) break;
    }

    if (!selected.length) {
        write("roadExposureStatus", "No mapped exposure sections within 3 km of map center. " +
            "This does not mean nearby roads are safe.");
        return;
    }

    write("roadExposureStatus", `${selected.length} nearby mapped road references within 3 km. ` +
        "Pan the map to explore another area.");

    for (const {feature, distance} of selected) {
        const props = feature.properties;
        const evidence = roadEvidence(Number(props.agreement_count));

        const button = document.createElement("button");
        const info = document.createElement("span");
        const name = document.createElement("strong");
        const details = document.createElement("small");
        const agreement = document.createElement("span");

        button.type = "button";
        button.className = "road-exposure-item";
        info.className = "road-exposure-info";
        name.textContent = props.road_name || "Unnamed road";

        const length = props.length_m == null ? NaN : Number(props.length_m);

        details.textContent = `${distance.toFixed(2)} km from map center · ` +
            (Number.isFinite(length) ? `${length.toFixed(0)} m mapped portion` : "Length not recorded");

        info.append(name, details);

        agreement.className = `road-agreement ${evidence.className}`;
        agreement.textContent = evidence.label;

        button.append(info, agreement);

        button.addEventListener("click", () => {
            const bounds = L.geoJSON(feature).getBounds();
            if (bounds.isValid()) {
                agencyState.map.fitBounds(bounds, {padding: [35, 35], maxZoom: 16});
            }
        });

        list.append(button);
    }
}

// Fetch the large road GeoJSON only when the user enables this layer.
async function toggleRoadExposure() {
    const checkbox = $("showRoadExposure");
    const legend = $("roadExposureLegend");

    if (!checkbox.checked) {
        legend.hidden = true;

        if (agencyState.roadLayer && agencyState.map?.hasLayer(agencyState.roadLayer)) {
            agencyState.map.removeLayer(agencyState.roadLayer);
        }

        $("roadExposureList").replaceChildren();
        write("roadExposureStatus", "Enable mapped road exposure to explore nearby road sections.");
        return;
    }

    if (!agencyState.map || agencyState.roadLoading) return;

    agencyState.roadLoading = true;
    checkbox.disabled = true;

    try {
        if (!agencyState.roadData) {
            write("roadExposureStatus", "Loading historical road-exposure geometry…");
            agencyState.roadData = await fetchRoadExposure();
            agencyState.roadLayer = createRoadLayer(agencyState.roadData);
        }

        if (checkbox.checked) {
            agencyState.roadLayer.addTo(agencyState.map);
            legend.hidden = false;
            renderNearbyRoads();
        }
    } catch (error) {
        console.error("Road exposure unavailable:", error);

        checkbox.checked = false;
        legend.hidden = true;

        write("roadExposureStatus", "Mapped road exposure unavailable. " +
            "Monitoring points and boundaries remain accessible.");
    } finally {
        checkbox.disabled = false;
        agencyState.roadLoading = false;
    }
}

// The trends endpoint must count distinct monitored points per recorded day; do not interpolate missing dates.
async function loadAreaTrends() {
    const serial = ++agencyState.trendSerial;

    if (agencyState.trendChart) agencyState.trendChart.destroy();
    agencyState.trendChart = null;

    if (agencyState.trendsUnsupported) {
        write("areaTrendStatus", "Area-level history requires the /api/agency/trends backend endpoint.");
        return;
    }

    const params = new URLSearchParams({days: "30"});

    if ($("subcountyFilter").value !== "ALL") {
        params.set("subcounty", $("subcountyFilter").value);
    }

    if ($("wardFilter").value !== "ALL") {
        params.set("ward", $("wardFilter").value);
    }

    write("areaTrendStatus", "Loading stored daily monitoring trends…");

    try {
        const response = await getJSON(`${AGENCY_API.trends}?${params}`);
        if (serial !== agencyState.trendSerial) return;

        if (!Array.isArray(response.daily)) {
            throw new Error("Invalid historical trend response");
        }

        const rows = [...response.daily].sort((a, b) =>
            String(a.local_date).localeCompare(String(b.local_date)));

        if (!rows.length) {
            write("areaTrendStatus", "No stored historical records for this selection.");
            return;
        }

        if (typeof Chart === "undefined") {
            write("areaTrendStatus", "Chart library unavailable.");
            return;
        }

        const series = [
            ["HIGH", "#C63B44"],
            ["MODERATE", "#D99A1B"],
            ["LOW", "#16855B"],
        ];

        agencyState.trendChart = new Chart($("areaTrendChart"), {
            type: "line",
            data: {
                labels: rows.map((row) => row.local_date),
                datasets: series.map(([risk, color]) => ({
                    label: risk,
                    data: rows.map((row) => row[risk] ?? null),
                    borderColor: color,
                    tension: 0.2,
                    spanGaps: false,
                })),
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {legend: {display: true, position: "bottom"}},
                scales: {
                    x: {ticks: {maxTicksLimit: 6}},
                    y: {beginAtZero: true, ticks: {precision: 0}},
                },
            },
        });

        write("areaTrendStatus", `${rows.length} recorded dates. Values count monitoring-point classifications.`);
    } catch (error) {
        if (serial !== agencyState.trendSerial) return;

        if (error.status === 404) {
            agencyState.trendsUnsupported = true;
            write("areaTrendStatus", "Agency trends require /api/agency/trends; no values have been estimated.");
            return;
        }

        console.error("Area trends unavailable:", error);
        write("areaTrendStatus", "Unable to load recorded area trends.");
    }
}

// Quote CSV fields and neutralize spreadsheet formulas before exporting.
function csvCell(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "number" && Number.isFinite(value)) return String(value);

    let text = String(value);
    if (/^[\s\u0000-\u001f]*[=+@-]/.test(text)) text = "'" + text;

    return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function exportCSV(points) {
    const rows = [[
        "location_id",
        "location_name",
        "subcounty",
        "ward",
        "risk_level",
        "model_score",
        "assessment_time",
        "precipitation_snapshot_mm",
        "temperature_c",
        "humidity_pct",
        "active_model_advisory",
    ]];

    for (const point of points) {
        rows.push([
            point.location_id,
            point.name,
            point._subcounty,
            point._ward,
            riskOf(point),
            point.risk_score,
            point.prediction_time,
            point.rainfall,
            point.temperature,
            point.humidity,
            point.alert_title,
        ]);
    }

    return rows.map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

function downloadCSV() {
    const points = reportPoints();
    if (!points.length) return;

    const blob = new Blob(["\uFEFF", exportCSV(points)], {
        type: "text/csv;charset=utf-8",
    });

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = url;
    link.download = "floodwatch_agency_monitoring_report.csv";

    document.body.append(link);
    link.click();
    link.remove();

    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
// Export all matching report records, not just the current table page.
function downloadPDF() {
    const points = reportPoints();
    if (!points.length) return;

    if (!window.jspdf?.jsPDF) {
        alert("PDF export library unavailable. Download CSV or check your connection.");
        return;
    }

    const doc = new window.jspdf.jsPDF({
        orientation: "landscape",
        unit: "mm",
        format: "a4",
    });

    if (typeof doc.autoTable !== "function") {
        alert("PDF table library unavailable. Download CSV or check your connection.");
        return;
    }

    const label = $("reportSearch").value.trim()
        .replace(/[\r\n]/g, " ")
        .slice(0, 90);

    doc.setFont("helvetica", "bold");
    doc.setFontSize(16);
    doc.text("Nairobi FloodWatch | Agency Monitoring Report", 14, 17);

    doc.setFont("helvetica", "normal");
    doc.setFontSize(10);

    doc.text(
        `Search: ${label}  |  Monitoring points: ${points.length}`,
        14, 25
    );

    doc.text(
        `Generated: ${localTime(new Date().toISOString())}`,
        14, 31
    );

    doc.autoTable({
        startY: 37,

        head: [[
            "Location",
            "Subcounty",
            "Ward",
            "Risk",
            "Score",
            "Assessed at",
        ]],

        body: points.map((point) => [
            String(point.name || `Point #${point.location_id}`),
            String(point._subcounty || "Unassigned"),
            String(point._ward || "Unassigned"),
            riskLabel(riskOf(point)),
            number(point.risk_score, 3),
            localTime(point.prediction_time),
        ]),

        styles: {
            fontSize: 8,
            cellPadding: 2.5,
            overflow: "linebreak",
        },

        headStyles: {
            fillColor: [23, 85, 186],
        },

        margin: {
            left: 14,
            right: 14,
            bottom: 20,
        },

        didDrawPage: () => {
            doc.setFontSize(8);
            doc.text(
                "Point-level model assessments, not official warnings or property-level predictions.",
                14, 199
            );
        },
    });

    doc.save("floodwatch_agency_monitoring_report.pdf");
}

function renderFiltered() {
    renderMap();
    renderComparisons();
    renderRecords();
    renderAdvisories();
    renderMapSearchStatus();
}

async function loadDashboard() {
    if (agencyState.loading) return;

    agencyState.loading = true;
    $("refreshData").disabled = true;

    try {
        const [overview, points] = await Promise.all([
            getJSON(AGENCY_API.overview),
            getJSON(AGENCY_API.locations),
        ]);

        if (!Array.isArray(points)) {
            throw new Error("Invalid monitoring-point response");
        }

        agencyState.overview = overview;

        await loadBoundaries();

        agencyState.points = assignAdministrativeAreas(
            points, agencyState.wards, agencyState.subcounties
        );

        fillSelect(
            "subcountyFilter",
            agencyState.points.map((p) => p._subcounty),
            "All subcounties"
        );

        updateWardOptions();
        renderSummary();

        if (agencyState.selected) {
            const updated = agencyState.points.find((p) =>
                String(p.location_id) === String(agencyState.selected.location_id));

            if (updated) {
                selectPoint(updated);
            } else {
                agencyState.selected = null;
                agencyState.selectionSerial++;
                destroyPointChart();

                $("selectedDetails").hidden = true;
                $("selectedPrompt").hidden = false;

                write("selectedPrompt", "Previously selected monitoring point is unavailable.");
                write("historyStatus", "Select a point to view history.");
            }
        }

        renderFiltered();
        loadAreaTrends();

        $("errorNotice").hidden = true;
    } catch (error) {
        console.error("Agency dashboard error:", error);

        write("errorNotice", `Monitoring data unavailable. ${error.message}`);
        $("errorNotice").hidden = false;

        write("freshness", "Connection unavailable. Previously shown information may be stale.");
        $("downloadCSV").disabled = true;
    } finally {
        $("refreshData").disabled = false;
        agencyState.loading = false;
    }
}


function startAgency() {
    initializeMap();

    // ================================================
    // RECORDED RAINFALL WINDOW SELECTORS
    // ================================================

    document.querySelectorAll(
        "[data-rainfall-window]"
    ).forEach(button => {
        button.addEventListener("click", () => {
            setAgencyRainfallWindow(
                button.dataset.rainfallWindow
            );
        });
    });

    updateAgencyRainfallControls();

    // ================================================
    // REFRESH MONITORING DATA
    // ================================================

    $("refreshData").addEventListener(
        "click",
        loadDashboard
    );

    // ================================================
    // INDEPENDENT MAP SEARCH
    // ================================================

    $("searchPoints").addEventListener(
        "input",
        () => resetGeoSearch("map")
    );

    $("findMapPlace").addEventListener(
        "click",
        () => findNairobiPlace("map")
    );

    $("searchPoints").addEventListener(
        "keydown",
        event => {
            if (event.key === "Enter") {
                event.preventDefault();
                findNairobiPlace("map");
            }
        }
    );

    // ================================================
    // INDEPENDENT REPORT SEARCH
    // ================================================

    $("reportSearch").addEventListener(
        "input",
        () => resetGeoSearch("report")
    );

    $("findReportPlace").addEventListener(
        "click",
        () => findNairobiPlace("report")
    );

    $("reportSearch").addEventListener(
        "keydown",
        event => {
            if (event.key === "Enter") {
                event.preventDefault();
                findNairobiPlace("report");
            }
        }
    );

    // ================================================
    // REPORT PAGINATION
    // ================================================

    $("prevReportPage").addEventListener(
        "click",
        () => {
            agencyState.reportPage--;
            renderRecords();
        }
    );

    $("nextReportPage").addEventListener(
        "click",
        () => {
            agencyState.reportPage++;
            renderRecords();
        }
    );

    // ================================================
    // RISK AND ADMINISTRATIVE FILTERS
    // ================================================

    $("riskFilter").addEventListener(
        "change",
        renderFiltered
    );

    $("subcountyFilter").addEventListener(
        "change",
        () => {
            updateWardOptions();
            renderFiltered();
            loadAreaTrends();
        }
    );

    $("wardFilter").addEventListener(
        "change",
        () => {
            renderFiltered();
            loadAreaTrends();
        }
    );

    $("compareBy").addEventListener(
        "change",
        renderComparisons
    );

    // ================================================
    // GIS LAYER CONTROLS
    // ================================================

    $("showPoints").addEventListener(
        "change",
        renderMap
    );

    $("showWards").addEventListener(
        "change",
        toggleBoundaries
    );

    $("showSubcounties").addEventListener(
        "change",
        toggleBoundaries
    );

    $("showRoadExposure").addEventListener(
        "change",
        toggleRoadExposure
    );

    // ================================================
    // REPORT EXPORT
    // ================================================

    $("downloadCSV").addEventListener(
        "click",
        downloadCSV
    );

    $("downloadPDF").addEventListener(
        "click",
        downloadPDF
    );

    // ================================================
    // INITIAL DASHBOARD LOAD
    // ================================================

    renderRecords();
    renderMapSearchStatus();

    loadDashboard();
}


if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", startAgency);
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        normalizeRisk,
        inRing,
        inPolygon,
        pointInGeometry,
        assignAdministrativeAreas,
        summarize,
        groupPoints,
        roadEvidence,
        nearestDistanceKm,
        csvCell,
        exportCSV,
    };
}