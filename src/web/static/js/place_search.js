"use strict";

// Smart place search state.
// This is separate from the resident's optional device coordinates.
const geoSearchState = {
    requestSerial: 0,
    marker: null,
    nearbyLayer: null,
    chosenPlace: null,
};

const GEO_SEARCH_DELAY_MS = 900;
let geoSearchTimer = null;


function geoElement(id) {
    return document.getElementById(id);
}


function geoWrite(id, text) {
    geoElement(id).textContent = String(text);
}


// Reset only the searched geographic place.
// Never remove or overwrite the user's consent-based device position.
function clearSearchedPlace() {
    if (state.map && geoSearchState.marker) {
        state.map.removeLayer(geoSearchState.marker);
    }

    if (state.map && geoSearchState.nearbyLayer) {
        state.map.removeLayer(geoSearchState.nearbyLayer);
    }

    geoSearchState.marker = null;
    geoSearchState.nearbyLayer = null;
    geoSearchState.chosenPlace = null;

    geoElement("geoContext").hidden = true;
    geoElement("geoNearbyResults").replaceChildren();
}


// Keep local monitoring-point search instant.
// Show the geographic lookup option without automatically calling Nominatim.

function updateGeoSearchAction() {
    const query = geoElement("areaSearch").value.trim();
    const panel = geoElement("geoSearchPanel");
    const button = geoElement("geoSearchButton");

    // Check actual location names regardless of the selected risk filter.
    const localMatch = state.locations.some((point) => {
        const name = String(point.name || "").toLowerCase();
        const id = String(point.location_id);

        return name.includes(query.toLowerCase()) || id.includes(query);
    });

    const validQuery = query.length >= 3 && query.length <= 70;

    // Geographic search is a fallback, not a replacement for local search.
    panel.hidden = !validQuery || localMatch;

    if (!panel.hidden) {
        button.textContent = `Find "${query}" in Nairobi`;
    }
}


// Schedule the suggestion only after the user pauses typing.
function scheduleGeoSearchAction() {
    clearTimeout(geoSearchTimer);

    // Hide the previous suggestion while the user is typing.
    geoElement("geoSearchPanel").hidden = true;

    const query = geoElement("areaSearch").value.trim();

    if (query.length < 3 || query.length > 70 || document.hidden) {
        return;
    }

    geoSearchTimer = setTimeout(() => {
        // Ignore outdated searches or an inactive browser tab.
        if (
            document.hidden ||
            geoElement("areaSearch").value.trim() !== query
        ) {
            return;
        }

        updateGeoSearchAction();
    }, GEO_SEARCH_DELAY_MS);
}


// Calculate nearest monitoring points using existing resident.js helpers.
function nearestMonitoringPoints(place, count = 3) {
    return state.locations
        .filter(hasValidCoordinates)
        .map((point) => ({
            point,
            distance: haversineKm(
                {
                    latitude: place.latitude,
                    longitude: place.longitude,
                },
                {
                    latitude: Number(point.latitude),
                    longitude: Number(point.longitude),
                }
            ),
        }))
        .sort((a, b) => a.distance - b.distance)
        .slice(0, count);
}


// Render nearby monitoring points as accessible buttons.
function renderGeoNearby(place) {
    const container = geoElement("geoNearbyResults");
    container.replaceChildren();

    const nearby = nearestMonitoringPoints(place);

    if (!nearby.length) {
        geoWrite(
            "geoSearchStatus",
            "The place was found, but monitoring coordinates are unavailable."
        );
        return;
    }

    // Draw outlined rings around the nearest monitoring points.
    // The points' model-derived risk colors remain visible underneath.
    if (state.map) {
        geoSearchState.nearbyLayer = L.layerGroup().addTo(state.map);
    }

    for (const {point, distance} of nearby) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "geo-nearby-button";

        const information = document.createElement("span");
        information.className = "geo-nearby-info";

        const name = document.createElement("strong");
        name.textContent = point.name;

        const detail = document.createElement("small");
        detail.textContent = `${distance.toFixed(2)} km from the searched place`;

        information.append(name, detail);

        const category = riskOf(point);
        const risk = document.createElement("span");
        risk.className = `geo-nearby-risk ${category}`;
        risk.textContent = category === "NO_DATA" ? "Not assessed" : category;

        button.append(information, risk);

        button.addEventListener("click", () => {
            // Clear search filtering so the selected point is visible
            // among the regular monitoring markers.
            geoElement("areaSearch").value = "";
            geoElement("riskFilter").value = "ALL";

            selectPoint(point);
            updateGeoSearchAction();

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

        if (geoSearchState.nearbyLayer) {
            const ring = L.circleMarker(
                [
                    Number(point.latitude),
                    Number(point.longitude),
                ],
                {
                    radius: 13,
                    color: "#1456B8",
                    weight: 2,
                    fillOpacity: 0,
                    interactive: true,
                }
            ).addTo(geoSearchState.nearbyLayer);

            ring.on("click", () => button.click());
        }
    }

    if (nearby[0].distance > 5) {
        geoWrite(
            "geoSearchStatus",
            "Place found. No monitoring point is within 5 km; showing the nearest available reference points."
        );
    } else {
        geoWrite(
            "geoSearchStatus",
            "Place found. Select a nearby monitoring point to view its recorded risk and weather conditions."
        );
    }
}


// Select an actual geocoding result.
function selectGeographicPlace(place) {
    clearSearchedPlace();

    geoSearchState.chosenPlace = place;

    geoElement("geoContext").hidden = false;

    geoWrite("geoContextTitle", place.name || "Located area");
    geoWrite("geoPlaceDescription", place.display_name);

    // Ward names are trusted only when matched to a real boundary polygon.
    if (place.ward_source === "polygon" && place.ward_name) {
        geoWrite("geoWard", `Verified administrative ward: ${place.ward_name}`);
    } else if (place.ward_source === "unverified") {
        geoWrite(
            "geoWard",
            "An administrative ward could not be verified at this coordinate."
        );
    } else {
        geoWrite(
            "geoWard",
            "Ward boundaries are not configured yet. Nearby monitoring points are shown below."
        );
    }

    // Reset local text filtering while retaining the geographic result.
    geoElement("areaSearch").value = "";
    geoElement("riskFilter").value = "ALL";
    geoElement("geoMatches").replaceChildren();

    renderMap();
    updateGeoSearchAction();

    if (state.map) {
        // Blue location marker differentiates the searched area from
        // LOW/MODERATE/HIGH observation-point markers.
        geoSearchState.marker = L.circleMarker(
            [place.latitude, place.longitude],
            {
                radius: 10,
                color: "#FFFFFF",
                weight: 3,
                fillColor: "#1456B8",
                fillOpacity: 1,
            }
        ).addTo(state.map);

        const tooltip = document.createElement("span");
        tooltip.textContent = `Searched place: ${place.name}`;

        geoSearchState.marker.bindTooltip(tooltip);

        state.map.setView(
            [place.latitude, place.longitude],
            14
        );
    }

    renderGeoNearby(place);
    geoElement("geoContext").scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "auto"
            : "smooth",
        block: "nearest",
    });
}


// Display geographic search candidates returned by Nominatim.
function renderGeographicCandidates(results) {
    const container = geoElement("geoMatches");
    container.replaceChildren();

    for (const place of results) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "geo-match-button";

        const name = document.createElement("strong");
        name.textContent = place.name || "Unnamed place";

        const description = document.createElement("small");
        description.textContent = place.display_name;

        button.append(name, description);

        button.addEventListener("click", () => {
            selectGeographicPlace(place);
        });

        container.append(button);
    }
}


// The external geographic lookup runs only on an explicit user action.

async function searchGeographicPlace() {
    const input = geoElement("areaSearch");
    const query = input.value.trim();

    if (query.length < 3 || query.length > 70) {
        geoWrite(
            "geoSearchStatus",
            "Enter a place name between 3 and 70 characters."
        );
        return;
    }

    const serial = ++geoSearchState.requestSerial;
    const button = geoElement("geoSearchButton");
    const panel = geoElement("geoSearchPanel");
    const loading = geoElement("geoSearchLoading");

    // Replace the fallback prompt and old local-search message
    // with a single animated loading indicator.
    button.disabled = true;
    panel.hidden = true;
    loading.hidden = false;

    geoElement("searchResults").replaceChildren();
    geoElement("geoMatches").replaceChildren();
    geoWrite("geoSearchStatus", "");

    try {
        const response = await fetch(
            `/api/places/search?q=${encodeURIComponent(query)}`,
            {
                headers: { Accept: "application/json" },
                cache: "no-store",
            }
        );

        if (!response.ok) {
            throw new Error(
                `Geographic lookup returned HTTP ${response.status}`
            );
        }

        const payload = await response.json();

        // Discard results from an outdated search.
        if (serial !== geoSearchState.requestSerial) {
            return;
        }

        if (!Array.isArray(payload.results) || !payload.results.length) {
            geoWrite(
                "geoSearchStatus",
                `No geographic match was found for "${query}" within the Nairobi search area. Try another spelling or a nearby landmark.`
            );

            // Allow a repeat search after an unsuccessful lookup.
            updateGeoSearchAction();
            return;
        }

        renderGeographicCandidates(payload.results);

        geoWrite(
            "geoSearchStatus",
            payload.results.length === 1
                ? "Location found. Select it to view nearby monitoring points."
                : `${payload.results.length} locations found. Select the place you meant.`
        );

        // The search action is no longer relevant after success.
        panel.hidden = true;

    } catch (error) {
        if (serial !== geoSearchState.requestSerial) {
            return;
        }

        console.error("Geographic search failed:", error);

        geoWrite(
            "geoSearchStatus",
            "Location search is temporarily unavailable. You can retry or choose a monitoring point manually."
        );

        updateGeoSearchAction();

    } finally {
        // Never let an outdated request change a newer search's UI.
        if (serial === geoSearchState.requestSerial) {
            loading.hidden = true;
            button.disabled = false;
        }
    }
}



// Connect the enhancement to our existing resident search bar.
document.addEventListener("DOMContentLoaded", () => {
    const input = geoElement("areaSearch");

    // The resident.js listener runs first and updates local matches.
    // This second listener manages only the geographic lookup controls.
    input.addEventListener("input", () => {
        geoSearchState.requestSerial++;
        geoElement("geoMatches").replaceChildren();
        geoElement("geoSearchLoading").hidden = true;
        geoElement("geoSearchButton").disabled = false;
        geoWrite("geoSearchStatus", "");


        clearSearchedPlace();

        //Reset timer with every keystroke
        scheduleGeoSearchAction();
    });

    input.addEventListener("keydown", (event) => {
        // Enter provides keyboard access to the geographic fallback.
        // Do not interfere when the resident already has local matches.
        if (
            event.key === "Enter" &&
            input.value.trim().length >= 3 &&
            filteredPoints().length === 0
        ) {
            event.preventDefault();
            searchGeographicPlace();
        }
    });
    document.addEventListener("visibilitychange", () => {
        if (document.hidden){
            clearTimeout(geoSearchTimer);
            geoElement("geoSearchPanel").hidden = true;
        } else{
            scheduleGeoSearchAction();
        }
    });

    geoElement("geoSearchButton").addEventListener(
        "click",
        searchGeographicPlace
    );

    scheduleGeoSearchAction();
});
