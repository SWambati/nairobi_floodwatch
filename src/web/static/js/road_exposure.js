"use strict";

// Historical road-exposure information is separate from operational
// LOW/MODERATE/HIGH observation-point predictions.
const roadExposureState = {
    data: null,
    layer: null,
    loading: false,
    features: [],
};

const ROAD_EXPOSURE_URL =
    "/static/gis/nairobi_road_inundation_exposure.geojson";

const ROAD_SEARCH_RADIUS_KM = 3;
const MAX_NEARBY_ROADS = 5;


// Translate underlying flood-model agreement into readable map evidence.
// These are display groupings, not new flood-risk predictions.
function roadEvidence(count) {
    if (count <= 2) {
        return {
            label: "Limited mapped evidence",
            color: "#79B6EB",
            className: "limited",
        };
    }

    if (count <= 4) {
        return {
            label: "Moderate mapped evidence",
            color: "#4679C0",
            className: "moderate",
        };
    }

    return {
        label: "Stronger mapped evidence",
        color: "#442E88",
        className: "stronger",
    };
}



function roadElement(id) {
    return document.getElementById(id);
}


function roadStatus(message) {
    roadElement("roadExposureStatus").textContent = message;
}


// Validate the prepared source before drawing any road segments.
async function fetchRoadExposure() {
    const response = await fetch(ROAD_EXPOSURE_URL, {
        cache: "no-cache",
    });

    if (!response.ok) {
        throw new Error(
            `Road exposure data unavailable: HTTP ${response.status}`
        );
    }

    const data = await response.json();

    if (
        data.type !== "FeatureCollection" ||
        !Array.isArray(data.features)
    ) {
        throw new Error("Invalid road-exposure GeoJSON.");
    }

    for (const feature of data.features) {
        const count = Number(
            feature.properties?.agreement_count
        );

        if (
            !Number.isInteger(count) ||
            count < 1 ||
            count > 6 ||
            !["LineString", "MultiLineString"].includes(
                feature.geometry?.type
            )
        ) {
            throw new Error(
                "Invalid road geometry or flood-model agreement."
            );
        }
    }

    return data;
}


// Create the road overlay using a blue-purple scale.
// These colors must not be interpreted as the RF risk categories.
function createRoadLayer(data) {
    const renderer = L.canvas({
        padding: 0.5,
    });

    return L.geoJSON(data, {
        renderer,

        style(feature) {
            const count = Number(feature.properties.agreement_count);
            const evidence = roadEvidence(count);

            return {
                color: evidence.color,
                weight: 4,
                opacity: 0.9,
            };
        },

        onEachFeature(feature, layer) {
            const properties = feature.properties;
            const count = Number(properties.agreement_count);

            const name = String(
                properties.road_name || "Unnamed road"
            );
            const evidence = roadEvidence(count);

            const tooltip = document.createElement("span");

            tooltip.textContent = `${name} — ${evidence.label}`;

            layer.bindTooltip(tooltip, {
                sticky: true,
            });

            layer.on("click", () => {
                const evidence = roadEvidence(count);

                roadStatus(
                    `${name}: ${evidence.label.toLowerCase()} of inundation ` +
                    "in the historical hazard mapping. " +
"This does not indicate that the road is currently flooded or closed."
                );
            });
        },
    });
}


// Convert longitude/latitude differences into approximate kilometres
// near Nairobi. This is only for client-side proximity ranking.
function coordinateKilometres(coordinate, origin) {
    const latitude = Number(coordinate[1]);
    const longitude = Number(coordinate[0]);

    const longitudeScale = (
        111.195 * Math.cos(origin.lat * Math.PI / 180)
    );

    return {
        x: (longitude - origin.lng) * longitudeScale,
        y: (latitude - origin.lat) * 111.195,
    };
}


// Calculate distance from the map center to a short line segment.
function segmentDistanceKm(a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSquared = dx * dx + dy * dy;

    if (lengthSquared === 0) {
        return Math.hypot(a.x, a.y);
    }

    const t = Math.max(
        0,
        Math.min(
            1,
            -(a.x * dx + a.y * dy) / lengthSquared
        )
    );

    return Math.hypot(
        a.x + t * dx,
        a.y + t * dy,
    );
}


// A road may have LineString or MultiLineString geometry.
function nearestDistanceKm(feature, origin) {
    const geometry = feature.geometry;

    const lines = geometry.type === "LineString"
        ? [geometry.coordinates]
        : geometry.coordinates;

    let minimum = Infinity;

    for (const line of lines) {
        for (let index = 0; index < line.length; index++) {
            const start = coordinateKilometres(
                line[index],
                origin
            );

            if (index === 0) {
                minimum = Math.min(
                    minimum,
                    Math.hypot(start.x, start.y)
                );
                continue;
            }

            const end = coordinateKilometres(
                line[index - 1],
                origin
            );

            minimum = Math.min(
                minimum,
                segmentDistanceKm(start, end)
            );
        }
    }

    return minimum;
}


// Rank exposure fragments locally rather than sending the user's
// map center or device coordinates to the server.
function nearbyRoads(origin) {
    return roadExposureState.features
        .map((feature) => ({
            feature,
            distance: nearestDistanceKm(feature, origin),
        }))
        .filter(
            (item) => item.distance <= ROAD_SEARCH_RADIUS_KM
        )
        .sort((a, b) => a.distance - b.distance);
}


function renderNearbyRoads() {
    const list = roadElement("roadExposureList");
    list.replaceChildren();

    if (
        !roadElement("toggleRoadExposure").checked ||
        !roadExposureState.data ||
        !state.map
    ) {
        return;
    }

    const origin = state.map.getCenter();
    const candidates = nearbyRoads(origin);

    // Avoid listing many fragments of the same road repeatedly.
    const seen = new Set();
    const selected = [];

    for (const candidate of candidates) {
        const properties = candidate.feature.properties;

        const name = String(
            properties.road_name || "Unnamed road"
        );

        const key = name === "Unnamed road"
            ? String(properties.segment_id)
            : name.toLowerCase();

        if (seen.has(key)) {
            continue;
        }

        seen.add(key);
        selected.push(candidate);

        if (selected.length >= MAX_NEARBY_ROADS) {
            break;
        }
    }

    if (!selected.length) {
        roadStatus(
            "No road sections with mapped flood exposure were identified " +
            "within 3 km of the current map center. This does not mean " +
            "nearby roads are flood-free or safe."
        );
        return;
    }

    roadStatus(
        `${selected.length} nearby exposed road references shown ` +
        "within 3 km of the map center. Pan the map to explore another area."
    );

    for (const {feature, distance} of selected) {
        const properties = feature.properties;
        const count = Number(properties.agreement_count);

        const button = document.createElement("button");
        button.type = "button";
        button.className = "road-exposure-item";

        const information = document.createElement("span");
        information.className = "road-exposure-info";

        const name = document.createElement("strong");

        name.textContent = (
            properties.road_name || "Unnamed road"
        );

        const details = document.createElement("small");

        details.textContent = (
            `${distance.toFixed(2)} km from map center · ` +
            `${Number(properties.length_m).toFixed(0)} m mapped portion`
        );

        information.append(name, details);

        const agreement = document.createElement("span");
        const evidence = roadEvidence(count);

        agreement.className = `road-agreement ${evidence.className}`;
        agreement.style.borderLeftColor = evidence.color;
        agreement.textContent = evidence.label;

        button.append(information, agreement);

        button.addEventListener("click", () => {
            const layer = L.geoJSON(feature);
            const bounds = layer.getBounds();

            if (bounds.isValid()) {
                state.map.fitBounds(bounds, {
                    padding: [35, 35],
                    maxZoom: 16,
                });
            }
        });

        list.append(button);
    }
}


// Load the static GeoJSON only after the resident enables the layer.
async function toggleRoadExposure() {
    const checkbox = roadElement("toggleRoadExposure");
    const legend = roadElement("roadExposureLegend");

    if (!checkbox.checked) {
        legend.hidden = true;

        if (roadExposureState.layer) {
            state.map.removeLayer(
                roadExposureState.layer
            );
        }

        roadElement("roadExposureList").replaceChildren();

        roadStatus(
            "Enable mapped road exposure to explore nearby road sections."
        );

        return;
    }

    if (roadExposureState.loading) {
        return;
    }

    try {
        if (!roadExposureState.data) {
            roadExposureState.loading = true;
            checkbox.disabled = true;

            roadStatus(
                "Loading historical road-exposure geometry…"
            );

            roadExposureState.data = await fetchRoadExposure();

            roadExposureState.features = (
                roadExposureState.data.features
            );

            roadExposureState.layer = createRoadLayer(
                roadExposureState.data
            );
        }

        roadExposureState.layer.addTo(state.map);
        legend.hidden = false;

        renderNearbyRoads();

    } catch (error) {
        console.error(
            "Unable to load road exposure:",
            error
        );

        checkbox.checked = false;
        legend.hidden = true;

        roadStatus(
            "Mapped road exposure is currently unavailable. " +
            "Monitoring points and administrative GIS remain accessible."
        );

    } finally {
        checkbox.disabled = false;
        roadExposureState.loading = false;
    }
}


// Keep proximity results synchronized with the existing map.
document.addEventListener("DOMContentLoaded", () => {
    if (!state.map || typeof L === "undefined") {
        roadStatus(
            "Road exposure requires the interactive Nairobi map."
        );

        roadElement("toggleRoadExposure").disabled = true;
        return;
    }

    roadElement("toggleRoadExposure").addEventListener(
        "change",
        toggleRoadExposure
    );

    state.map.on("moveend", () => {
        if (roadElement("toggleRoadExposure").checked) {
            renderNearbyRoads();
        }
    });
});
