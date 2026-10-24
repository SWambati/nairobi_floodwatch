"use strict";

(() => {
    const API = {
        health: "/api/admin/health",
        overview: "/api/admin/overview",
        runs: "/api/admin/runs",
        stages: "/api/admin/stages",
        errors: "/api/admin/errors",
        events: (id) => `/api/admin/runs/${encodeURIComponent(id)}/events`,
    };

    const REFRESH_INTERVAL_MS = 30_000;
    const $ = (id) => document.getElementById(id);

    const state = {
        authenticated: false,
        refreshing: false,
        runs: [],
        stages: [],
        events: [],
        selectedRunId: null,
        eventRequest: 0,
        durationChart: null,
        riskChart: null,
    };

    function text(id, value) {
        $(id).textContent = value == null ? "—" : String(value);
    }

    function timeEAT(value) {
        if (!value) return "Not recorded";

        const date = new Date(value);
        if (!Number.isFinite(date.getTime())) return "Invalid timestamp";

        return new Intl.DateTimeFormat("en-KE", {
            timeZone: "Africa/Nairobi",
            dateStyle: "medium",
            timeStyle: "short",
        }).format(date) + " EAT";
    }

    function integer(value) {
        if (value === null || value === undefined || value === "") return "—";
        const n = Number(value);
        return Number.isFinite(n) ? n.toLocaleString("en-KE") : "—";
    }

    function duration(value) {
        if (value === null || value === undefined || value === "") return "—";

        const n = Number(value);
        if (!Number.isFinite(n) || n < 0) return "—";

        return n >= 1000 ? `${(n / 1000).toFixed(2)} s` : `${Math.round(n)} ms`;
    }

    function statusCard(labelId, detailId, status, label, detail) {
        const title = $(labelId);
        title.textContent = label;
        title.closest(".admin-health-card").dataset.status = status;
        text(detailId, detail);
    }

    function rowMessage(bodyId, columns, message) {
        const body = $(bodyId);
        const row = document.createElement("tr");
        const cell = document.createElement("td");

        cell.colSpan = columns;
        cell.textContent = message;
        row.append(cell);
        body.replaceChildren(row);
    }

    function addCell(row, value) {
        const cell = document.createElement("td");
        cell.textContent = value == null ? "—" : String(value);
        row.append(cell);
        return cell;
    }

    function badge(status) {
        const label = document.createElement("span");
        const normalized = String(status || "unknown").toLowerCase();

        label.className = "admin-run-status";
        label.dataset.status = ["succeeded", "failed", "running"].includes(normalized)
            ? normalized : "unknown";

        label.textContent = normalized === "running" ? "Recorded running" :
            normalized.charAt(0).toUpperCase() + normalized.slice(1);

        return label;
    }

    async function fetchJSON(url) {
        const response = await fetch(url, {
            headers: {Accept: "application/json"},
            credentials: "same-origin",
            cache: "no-store",
        });

        if (!response.ok) {
            const error = new Error(`HTTP ${response.status}`);
            error.status = response.status;
            throw error;
        }

        return response.json();
    }

    function showError(message) {
        text("adminError", message);
        $("adminError").hidden = false;
    }

    function renderOverview(overview) {
        const latest = overview.latest_run;
        const metrics = latest?.metrics || {};
        const counts = overview.runs_last_24h || {};

        text("lastRunTime", latest ? timeEAT(latest.started_at) : "—");
        text("lastRunDuration", duration(latest?.duration_ms));
        text("runs24Total", integer(counts.total));
        text("runs24Failed", integer(counts.failed));
        text("latestRunId", latest?.run_id || "No recorded runs");
        text("lastSuccessfulRun", timeEAT(overview.last_success?.finished_at));

        const metricFields = {
            opsLocations: "locations_loaded",
            opsPredictions: "predictions_generated",
            opsWeatherRows: "weather_rows_written",
            opsAlertsCreated: "alerts_created",
            opsAlertsActive: "alerts_active",
            opsAlertsUpdated: "alerts_updated",
            opsAlertsResolved: "alerts_resolved",
            opsAlertsExpired: "alerts_expired",
            opsAlertsSuperseded: "alerts_superseded",
        };

        for (const [id, name] of Object.entries(metricFields)) {
            text(id, integer(metrics[name]));
        }

        if (!latest) {
            statusCard(
                "pipelineRunState", "pipelineHealthDetail",
                "unknown", "No runs recorded",
                "Pipeline execution has not been observed."
            );
            return;
        }

        const stateLabel = latest.status === "running"
            ? "Started — no completion recorded"
            : `Last run ${latest.status || "unknown"}`;

        statusCard(
            "pipelineRunState",
            "pipelineHealthDetail",
            latest.status || "unknown",
            stateLabel,
            `${timeEAT(latest.started_at)} · ${duration(latest.duration_ms)}`
        );
    }

    function renderComponentCards(stages) {
        const lookups = [
            ["open_meteo_acquisition", "etlRunState", "etlHealthDetail"],
            ["model_inference", "modelRunState", "modelHealthDetail"],
            ["alert_generation", "alertRunState", "alertHealthDetail"],
        ];

        for (const [component, labelId, detailId] of lookups) {
            const stage = stages.find((item) => item.component === component);

            if (!stage) {
                statusCard(
                    labelId, detailId, "unknown",
                    "No stage record",
                    "No execution recorded in the latest run."
                );
                continue;
            }

            const label = stage.status === "succeeded"
                ? "Last stage completed"
                : stage.status === "failed"
                    ? "Last stage failed"
                    : "Started — completion unrecorded";

            statusCard(
                labelId, detailId, stage.status || "unknown",
                label,
                `${timeEAT(stage.occurred_at)} · ${duration(stage.duration_ms)}`
            );
        }
    }

    function renderRunHistory(runs) {
        const body = $("runRows");
        body.replaceChildren();

        if (!runs.length) {
            rowMessage("runRows", 7, "No operational runs recorded yet.");
            text("runHistoryStatus", "No pipeline execution history is available.");
            return;
        }

        for (const run of runs) {
            const row = document.createElement("tr");

            addCell(row, timeEAT(run.started_at));
            addCell(row, run.job_name);

            const statusCell = document.createElement("td");
            statusCell.append(badge(run.status));
            row.append(statusCell);

            addCell(row, duration(run.duration_ms));
            addCell(row, integer(run.metrics?.locations_loaded));
            addCell(row, integer(run.metrics?.predictions_generated));

            const action = document.createElement("td");
            const button = document.createElement("button");

            button.type = "button";
            button.textContent = "Inspect";
            button.setAttribute("aria-label", `Inspect run ${run.run_id}`);
            button.addEventListener("click", () => selectRun(run.run_id, true));

            action.append(button);
            row.append(action);
            body.append(row);
        }

        text(
            "runHistoryStatus",
            `${runs.length} recorded run(s) returned. Select Inspect to view events.`
        );
    }

    function drawDurationChart(stages) {
        if (state.durationChart) state.durationChart.destroy();
        state.durationChart = null;

        const measured = stages.filter((item) =>
            item.duration_ms != null &&
            Number.isFinite(Number(item.duration_ms))
        );

        if (!measured.length) {
            text("stageChartStatus", "No completed stage durations recorded.");
            return;
        }

        if (typeof Chart === "undefined") {
            text("stageChartStatus", "Chart.js is unavailable.");
            return;
        }

        const sorted = [...measured].sort(
            (a, b) => Number(b.duration_ms) - Number(a.duration_ms)
        );

        state.durationChart = new Chart($("stageDurationChart"), {
            type: "bar",
            data: {
                labels: sorted.map((s) => s.component.replaceAll("_", " ")),
                datasets: [{
                    label: "Duration (seconds)",
                    data: sorted.map((s) => Number(s.duration_ms) / 1000),
                    backgroundColor: "#1755ba",
                }],
            },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {display: false},
                },
                scales: {
                    x: {
                        beginAtZero: true,
                        title: {display: true, text: "Seconds"},
                    },
                },
            },
        });

        text(
            "stageChartStatus",
            `${sorted.length} recorded stage durations from the latest run.`
        );
    }

    function renderStages(stages) {
        const body = $("stageRows");
        body.replaceChildren();

        if (!stages.length) {
            rowMessage("stageRows", 4, "No processing stages recorded.");
        } else {
            for (const stage of stages) {
                const row = document.createElement("tr");

                addCell(row, stage.component.replaceAll("_", " "));

                const statusCell = document.createElement("td");
                statusCell.append(badge(stage.status));
                row.append(statusCell);

                addCell(row, duration(stage.duration_ms));
                addCell(row, timeEAT(stage.occurred_at));

                body.append(row);
            }
        }

        renderComponentCards(stages);
        drawDurationChart(stages);
    }

    function renderRiskChart(metrics = {}) {
        if (state.riskChart) state.riskChart.destroy();
        state.riskChart = null;

        const names = ["LOW", "MODERATE", "HIGH"];
        const values = names.map(
            (name) => metrics[`risk_${name.toLowerCase()}_count`]
        );

        if (values.some((value) => value === undefined || value === null)) {
            text(
                "adminRiskStatus",
                "No complete risk-count metrics stored for the latest run."
            );
            return;
        }

        if (typeof Chart === "undefined") {
            text("adminRiskStatus", "Chart.js is unavailable.");
            return;
        }

        state.riskChart = new Chart($("adminRiskChart"), {
            type: "doughnut",
            data: {
                labels: ["Low", "Moderate", "High"],
                datasets: [{
                    data: values.map(Number),
                    backgroundColor: ["#16855b", "#d99a1b", "#c63b44"],
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {position: "bottom"},
                },
            },
        });

        text(
            "adminRiskStatus",
            "Latest recorded model classifications at individual monitoring points."
        );
    }

    function renderErrors(errors) {
        const body = $("recentErrorsRows");
        body.replaceChildren();

        if (!errors.length) {
            rowMessage("recentErrorsRows", 3, "No recorded error events returned.");
            return;
        }

        for (const event of errors) {
            const row = document.createElement("tr");

            addCell(row, timeEAT(event.occurred_at));
            addCell(row, event.component);

            const details = event.details?.error_type
                ? ` (${event.details.error_type})`
                : "";

            addCell(row, `${event.message}${details}`);
            body.append(row);
        }
    }

    function updateEventComponents() {
        const select = $("eventComponentFilter");
        const prior = select.value;

        const components = [
            ...new Set(state.events.map((e) => e.component).filter(Boolean)),
        ].sort();

        select.replaceChildren(new Option("All components", "ALL"));

        for (const component of components) {
            select.add(new Option(component.replaceAll("_", " "), component));
        }

        select.value = components.includes(prior) ? prior : "ALL";
    }

    function renderEvents() {
        const level = $("eventLevelFilter").value;
        const component = $("eventComponentFilter").value;
        const term = $("eventSearch").value.trim().toLowerCase();

        const events = state.events.filter((e) =>
            (level === "ALL" || e.level === level) &&
            (component === "ALL" || e.component === component) &&
            (
                !term ||
                `${e.message} ${e.component} ${e.details?.error_type || ""}`
                    .toLowerCase()
                    .includes(term)
            )
        );

        const body = $("eventRows");
        body.replaceChildren();

        if (!events.length) {
            rowMessage("eventRows", 5, "No events match the selected filters.");
        } else {
            for (const event of events) {
                const row = document.createElement("tr");

                addCell(row, timeEAT(event.occurred_at));

                const levelCell = document.createElement("td");
                const label = document.createElement("span");

                label.className = "admin-event-level";
                label.classList.add(
                    ["INFO", "WARNING", "ERROR"].includes(event.level)
                        ? event.level
                        : "INFO"
                );

                label.textContent = event.level;
                levelCell.append(label);
                row.append(levelCell);

                addCell(row, event.component);

                const messageCell = addCell(row, event.message);

                if (event.details && Object.keys(event.details).length) {
                    messageCell.title = JSON.stringify(event.details);
                }

                addCell(row, duration(event.duration_ms));
                body.append(row);
            }
        }

        text(
            "eventCount",
            `Showing ${events.length} of ${state.events.length} event(s) for this run.`
        );
    }

    async function selectRun(runId, scroll = false) {
        state.selectedRunId = runId;
        const serial = ++state.eventRequest;

        const run = state.runs.find((item) => item.run_id === runId);

        text(
            "selectedRunLabel",
            run
                ? `Run ${runId} · ${timeEAT(run.started_at)} · ${run.status}`
                : `Run ${runId}`
        );

        rowMessage("eventRows", 5, "Loading operational events…");
        text("eventCount", "Loading events…");

        if (scroll) {
            $("systemLogs").scrollIntoView({
                behavior: "smooth",
                block: "start",
            });
        }

        try {
            const response = await fetchJSON(
                `${API.events(runId)}?limit=500`
            );

            if (serial !== state.eventRequest) return;

            state.events = Array.isArray(response.events)
                ? response.events
                : [];

            updateEventComponents();
            renderEvents();

        } catch (error) {
            if (serial !== state.eventRequest) return;

            state.events = [];
            rowMessage(
                "eventRows", 5,
                "Unable to load events for the selected run."
            );

            text("eventCount", "Event query failed.");
            console.error("Admin run events:", error);
        }
    }

    async function refreshOperations() {
        if (state.refreshing) return;

        state.refreshing = true;
        $("refreshOperations").disabled = true;
        $("adminError").hidden = true;

        text("opsUpdated", "Checking recorded operational information…");

        try {
            // Verify administrator access before querying operational history.
            const health = await fetchJSON(API.health);

            state.authenticated = true;
            $("adminAuthNotice").hidden = true;

            statusCard(
                "apiReachabilityState", "apiHealthDetail",
                "reachable", "Responding",
                "Protected monitoring API responded to this request."
            );

            statusCard(
                "dbConnectionState",
                "dbHealthDetail",
                health.database === "reachable" ? "reachable" : "unknown",
                health.database === "reachable" ? "Connected" : "Not verified",
                "Connectivity checked through the monitoring query."
            );

            const limit = Number($("runLimit").value) || 20;

            const [overview, runData, stageData, errorData] = await Promise.all([
                fetchJSON(API.overview),
                fetchJSON(`${API.runs}?limit=${limit}`),
                fetchJSON(API.stages),
                fetchJSON(`${API.errors}?limit=30`),
            ]);

            state.runs = Array.isArray(runData.runs) ? runData.runs : [];
            state.stages = Array.isArray(stageData.stages)
                ? stageData.stages
                : [];

            renderOverview(overview);
            renderRunHistory(state.runs);
            renderStages(state.stages);
            renderRiskChart(overview.latest_run?.metrics);
            renderErrors(
                Array.isArray(errorData.errors) ? errorData.errors : []
            );

            text(
                "opsUpdated",
                `Checked ${timeEAT(new Date().toISOString())}`
            );

            const nextId = state.runs.some(
                (run) => run.run_id === state.selectedRunId
            )
                ? state.selectedRunId
                : state.runs[0]?.run_id;

            if (nextId) {
                await selectRun(nextId);
            } else {
                state.events = [];
                ++state.eventRequest;

                rowMessage(
                    "eventRows", 5,
                    "No operational execution selected."
                );

                text("selectedRunLabel", "No recorded runs available.");
                text("eventCount", "No events to display.");
            }

        } catch (error) {
            if (error.status === 401 || error.status === 403) {
                state.authenticated = false;
                $("adminAuthNotice").hidden = false;

                text("opsUpdated", "Admin sign-in required");

                showError(
                    "Operational API access requires a verified admin session. " +
                    "Do not put an API key in browser JavaScript."
                );
            } else {
                text("opsUpdated", "Operational update failed");

                showError(
                    "Could not update operational telemetry. " +
                    "Previously displayed values may be stale."
                );
            }

            console.warn("Admin monitoring update:", error);

        } finally {
            state.refreshing = false;
            $("refreshOperations").disabled = false;
        }
    }
    
    function switchAdminTab() {
        const systemPanel = $("adminSystemTab");
        const floodPanel = $("adminFloodTab");
        if (!systemPanel || !floodPanel) return;

        const hash  = window.location.hash.slice(1);
        const target = document.getElementById(hash);
        const isFlood = hash === "flood" || (target && floodPanel.contains(target));

        systemPanel.hidden = Boolean(isFlood);
        floodPanel.hidden = !isFlood;

        document.querySelectorAll("[data-admin-tab]").forEach((link) =>{
            const active = link.dataset.adminTab === (isFlood ? "flood" : "system");
            link.classList.toggle("current", active);

            if (active){
                link.setAttribute("aria-current", "location");
            }
            else{
                link.removeAttribute("aria-current");
            }
        });
        text("adminPageTitle", isFlood ? "Nairobi FloodWatch Flood Monitoring" : "Nairobi FloodWatch System Monitoring");
        text("adminPageDescription", isFlood ? "Explore Nairobi flood assessments, GIS monitoring, geographic comparisons, historical trends and reports." : "Monitor ETL operations, database connectivity, Random Forest inference, pipeline performance, alerts and system logs.");

        $("opsUpdated").hidden = isFlood;

        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                window.dispatchEvent(new Event("resize"));

                if (!isFlood){
                    state.durationChart?.resize();
                    state.riskChart?.resize();
                }
                if (hash === "system" || hash === "flood"){
                    window.scrollTo(0, 0);
                }
                else if (target){
                    target.scrollIntoView({block: "start"})
                }
            });
        });
    }

    function startAdmin() {
        switchAdminTab();
        window.addEventListener("hashchange", switchAdminTab);

        $("refreshOperations").addEventListener(
            "click", refreshOperations
        );

        $("runLimit").addEventListener(
            "change", refreshOperations
        );

        for (const id of ["eventLevelFilter", "eventComponentFilter"]) {
            $(id).addEventListener("change", renderEvents);
        }

        $("eventSearch").addEventListener(
            "input", renderEvents
        );

        refreshOperations();

        // Poll the API, not the prediction pipeline itself.
        setInterval(() => {
            if (state.authenticated && !document.hidden) {
                refreshOperations();
            }
        }, REFRESH_INTERVAL_MS);

        document.addEventListener("visibilitychange", () => {
            if (state.authenticated && !document.hidden) {
                refreshOperations();
            }
        });
    }

    document.addEventListener("DOMContentLoaded", startAdmin);
})();