"use strict";

// Administrative GIS overlays are independent of flood-risk predictions.
const gisState = {
    wards: null,
    subcounties: null,
    boundaryPane: null,
};

const gisSources = {
    wards: {
        url: "/static/gis/nairobi_wards.geojson",
        expected: 85,
        field: "ward_name",
        label: "Ward",
        checkbox: "toggleWards",
    },
    subcounties: {
        url: "/static/gis/nairobi_subcounties.geojson",
        expected: 17,
        field: "subcounty_name",
        label: "Subcounty",
        checkbox: "toggleSubcounties",
    },
};


function gisElement(id) {
    return document.getElementById(id);
}


function setGISStatus(message) {
    gisElement("gisLayerStatus").textContent = message;
}


// Validate a GIS file before creating any map layers.
async function fetchGISData(source) {
    const response = await fetch(source.url, {
        cache: "no-cache",
    });

    if (!response.ok) {
        throw new Error(
            `Unable to load ${source.url}: HTTP ${response.status}`
        );
    }

    const data = await response.json();

    if (
        data.type !== "FeatureCollection" ||
        !Array.isArray(data.features)
    ) {
        throw new Error("Invalid GeoJSON FeatureCollection.");
    }

    if (data.features.length !== source.expected) {
        throw new Error(
            `Expected ${source.expected} polygons, ` +
            `found ${data.features.length}.`
        );
    }

    for (const feature of data.features) {
        const name = feature.properties?.[source.field];

        if (
            !name ||
            !["Polygon", "MultiPolygon"].includes(
                feature.geometry?.type
            )
        ) {
            throw new Error(
                "GIS data contains missing names or invalid feature types."
            );
        }
    }

    return data;
}


// Style ward/subcounty boundaries in blue.
// Never use LOW/MODERATE/HIGH colors for administrative polygons.
function boundaryStyle(level) {
    if (level === "wards") {
        return {
            color: "#4188C5",
            weight: 1.2,
            opacity: 0.7,
            fillColor: "#74B7E8",
            fillOpacity: 0.035,
        };
    }

    return {
        color: "#164C80",
        weight: 2.5,
        opacity: 0.9,
        fillColor: "#74B7E8",
        fillOpacity: 0.015,
    };
}


// Create one interactive Leaflet GeoJSON layer.
function createBoundaryLayer(data, level) {
    const source = gisSources[level];
    const normal = boundaryStyle(level);

    return L.geoJSON(data, {
        pane: gisState.boundaryPane,

        style: () => ({...normal}),

        onEachFeature(feature, layer) {
            const name = String(
                feature.properties?.[source.field] ||
                "Unnamed administrative area"
            );

            // Render GIS labels as plain text to avoid HTML injection.
            const tooltip = document.createElement("span");
            tooltip.textContent = name;

            layer.bindTooltip(tooltip, {
                sticky: true,
                direction: "auto",
            });

            layer.on("mouseover", () => {
                layer.setStyle({
                    ...normal,
                    weight: normal.weight + 1.4,
                    fillOpacity: 0.11,
                });
            });

            layer.on("mouseout", () => {
                layer.setStyle(normal);
            });

            layer.on("click", () => {
                if (typeof layer.getBounds === "function") {
                    state.map.fitBounds(layer.getBounds(), {
                        padding: [25, 25],
                        maxZoom: 14,
                    });
                }

                setGISStatus(
                    `${source.label}: ${name}. ` +
                    "Administrative boundary only; " +
                    "model risk remains specific to observation points."
                );
            });
        },
    });
}


// Load a layer, enable its checkbox and honor its default state.
async function loadBoundaryLayer(level) {
    const source = gisSources[level];
    const checkbox = gisElement(source.checkbox);

    try {
        const data = await fetchGISData(source);
        const layer = createBoundaryLayer(data, level);

        gisState[level] = layer;
        checkbox.disabled = false;

        if (checkbox.checked) {
            layer.addTo(state.map);
        }

        return true;

    } catch (error) {
        console.warn(
            `Nairobi ${level} GIS unavailable:`,
            error
        );

        checkbox.checked = false;
        checkbox.disabled = true;

        return false;
    }
}


// Toggling polygons does not change model scores or selected locations.
function toggleBoundaryLayer(level) {
    const source = gisSources[level];
    const layer = gisState[level];
    const checkbox = gisElement(source.checkbox);

    if (!layer || !state.map) {
        return;
    }

    if (checkbox.checked) {
        state.map.addLayer(layer);
    } else {
        state.map.removeLayer(layer);
    }
}


// Residents can also hide all observation-point markers without
// removing their device location marker or geographic search marker.
function toggleObservationMarkers() {
    if (!state.map || !state.markers) {
        return;
    }

    const checkbox = gisElement("toggleObservations");

    if (checkbox.checked) {
        state.map.addLayer(state.markers);
    } else {
        state.map.removeLayer(state.markers);
    }
}


// The resident.js DOMContentLoaded handler initializes the map first.
document.addEventListener("DOMContentLoaded", async () => {
    if (!state.map || typeof L === "undefined") {
        setGISStatus(
            "GIS map unavailable. Monitoring locations remain searchable."
        );
        return;
    }

    // Administrative polygons are drawn beneath observation points.
    // A separate Leaflet pane prevents polygon outlines from
    // covering the markers and interfering with their interaction.
    const paneName = "floodwatch-boundaries";

    if (!state.map.getPane(paneName)) {
        const pane = state.map.createPane(paneName);
        pane.style.zIndex = "350";
    }

    gisState.boundaryPane = paneName;

    gisElement("toggleObservations").addEventListener(
        "change",
        toggleObservationMarkers
    );

    for (const level of Object.keys(gisSources)) {
        gisElement(gisSources[level].checkbox).addEventListener(
            "change",
            () => toggleBoundaryLayer(level)
        );
    }

    const results = await Promise.all([
        loadBoundaryLayer("wards"),
        loadBoundaryLayer("subcounties"),
    ]);

    const available = results.filter(Boolean).length;

    if (available === 2) {
        setGISStatus(
            "Ward and subcounty boundaries loaded. " +
            "Use the controls to choose visible layers."
        );
    } else if (available === 1) {
        setGISStatus(
            "One administrative layer is unavailable. " +
            "Other map features remain available."
        );
    } else {
        setGISStatus(
            "Administrative layers are unavailable. " +
            "You can still explore monitoring points."
        );
    }
});
