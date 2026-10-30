"use strict";

(() => {
    const $ = (id) => document.getElementById(id);
    const API = "/api/admin/agency-requests";
    const PAGE_SIZE = 50;
    const state = {requests: [], selectedId: null, decision: null, offset: 0, total: 0, submitting: false};

    function write(id, value) {
        const element = $(id);

        if (element) {
            element.textContent = String(value ?? "—");
        }
    }

    function localTime(value) {
        if (!value) return "—";

        const date = new Date(value);

        if (Number.isNaN(date.getTime())) {
            return "—";
        }
        return new Intl.DateTimeFormat("en-KE", {timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "short"}).format(date);
    }

    async function requestJSON(url, options = {}) {
        const response = await fetch(url, {credentials: "same-origin", cache: "no-store", ...options});
        const payload = await response.json().catch(() => ({}));

        if (!response.ok) {
            throw new Error(typeof payload.detail === "string" ? payload.detail : `Request failed (${response.status})`);
        }
        return payload;
    }

    function addCell(row, value) {
        const td = document.createElement("td");
        td.textContent = value ?? "—";
        row.append(td);
        return td;
    }

    // RENDER APPLICATION TABLE
    function renderRequests() {
        const tbody = $("approvalsRows");
        tbody.replaceChildren();
        const search = $("approvalsSearch").value.trim().toLowerCase();

        const filtered = state.requests.filter((item) => {
            return [item.applicant_name, item.applicant_email, item.organization_name].some((value) => String(value || "").toLowerCase().includes(search));
        });

        if (!filtered.length) {
            const row = document.createElement("tr");
            const cell = addCell(row, "No matching agency applications.");
            cell.colSpan = 5;
            tbody.append(row);
            return;
        }

        for (const item of filtered) {
            const row = document.createElement("tr");
            const nameCell = addCell(row, item.applicant_name);
            const email = document.createElement("small");
            email.textContent = item.applicant_email || "—";
            
            nameCell.append(email);
            addCell(row, item.organization_name);
            addCell(row, localTime(item.created_at));
            addCell(row, item.status);

            const actionCell = document.createElement("td");
            const button = document.createElement("button");
            button.type = "button";
            button.className = "button secondary";
            button.textContent = "View";

            button.addEventListener("click", () => {openApplication(item.id);});
            actionCell.append(button);
            row.append(actionCell);
            tbody.append(row);
        }
    }

    // FETCH APPLICATIONS
    async function loadRequests() {
        const status = $("approvalsStatus").value;
        write("approvalsMessage", "Loading applications…");

        try {
            const params = new URLSearchParams({
                status,
                limit: String(PAGE_SIZE),
                offset: String(state.offset),
            });

            const data = await requestJSON(`${API}?${params.toString()}`);
            state.requests = Array.isArray(data.requests) ? data.requests : [];
            state.total = Number(data.total || 0);

            const start = state.requests.length ? state.offset + 1 : 0;
            const end = state.offset + state.requests.length;

            write("approvalsMessage", `Showing ${start}–${end} of ${state.total} applications.`);
            write("approvalsPageStatus", `Page ${Math.floor(state.offset / PAGE_SIZE) + 1}`);

            $("approvalsPrevious").disabled = state.offset === 0;
            $("approvalsNext").disabled = end >= state.total;

            renderRequests();
        } catch (error) {
            console.error(error);
            state.requests = [];
            renderRequests();

            $("approvalsPrevious").disabled = true;
            $("approvalsNext").disabled = true;
            write("approvalsMessage", error.message);
        }
    }

    // VIEW APPLICATION DETAILS
    async function openApplication(id) {
        state.selectedId = id;
        const panel = $("approvalDetailsPanel");
        panel.hidden = false;

        $("approvalActions").hidden = true;
        write("applicationTitle", "Loading application…");

        try {
            const item = await requestJSON(`${API}/${encodeURIComponent(id)}`);

            if (state.selectedId !== id) return;

            write("applicationTitle", "Application Details");
            write("detailStatus", item.status);
            write("detailName", item.applicant_name);
            write("detailEmail", item.applicant_email);
            write("detailEmailVerified", item.email_verified ? "Verified" : "Not Verified");
            write("detailAccountStatus", item.account_status);
            write("detailOrganization", item.organization_name);
            write("detailOrganizationEmail", item.organization_email || "Not supplied");
            write("detailJustification", item.justification);
            write("detailCreated", localTime(item.created_at));
            write("detailReviewedBy", item.reviewer_name || "Not reviewed");
            write("detailNotes", item.review_notes || "—");

            $("approvalActions").hidden = item.status !== "pending";
            panel.scrollIntoView({behavior: "smooth", block: "start",});
        } catch (error) {
            write("applicationTitle", error.message);
        }
    }

    // SHOW APPROVAL / REJECTION DIALOG
    function showDecision(decision) {
        if (!state.selectedId || state.submitting) return;

        state.decision = decision;
        const approving = decision === "approved";

        write("approvalDialogTitle", approving ? "Approve Agency Access" : "Reject Agency Access");
        write("approvalDialogText", approving ? "This grants agency privileges and ends the applicant's existing sessions." : "This rejects the application without granting agency access.");
        write("notesRequired", approving ? "(optional)" : "(required)");

        $("approvalNotes").value = "";
        $("affiliationConfirmed").checked = false;
        $("affiliationLabel").hidden = !approving;

        write("approvalDialogError", "");
        $("approvalDialog").showModal();
    }

    // SUBMIT DECISION
    async function submitDecision(event) {
        event.preventDefault();

        if (state.submitting || !state.selectedId || !state.decision) {
            return;
        }

        const notes = $("approvalNotes").value.trim();

        if (state.decision === "rejected" && notes.length < 5) {
            write("approvalDialogError", "Please provide a rejection reason.");
            return;
        }

        const verified = $("affiliationConfirmed").checked;

        if (state.decision === "approved" && !verified) {
            write("approvalDialogError", "Confirm that institutional affiliation was independently verified.");
            return;
        }
        state.submitting = true;
        $("approvalConfirm").disabled = true;
        $("approvalCancel").disabled = true;
        write("approvalDialogError", "Saving decision…");

        try {
            const id = state.selectedId;
            await requestJSON(
                `${API}/${encodeURIComponent(id)}/decision`,
                {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        decision: state.decision,
                        review_notes: notes,
                        affiliation_confirmed: verified,
                    }),
                }
            );
            $("approvalDialog").close();
            await loadRequests();
            await openApplication(id);
        } catch (error) {
            write("approvalDialogError", error.message);
        } finally {
            state.submitting = false;
            $("approvalConfirm").disabled = false;
            $("approvalCancel").disabled = false;
        }
    }

    // INITIALIZATION
    document.addEventListener("DOMContentLoaded", () => {
        $("approvalsRefresh").addEventListener("click", loadRequests);
        $("approvalsStatus").addEventListener("change", () => {
            state.offset = 0;
            state.selectedId = null;
            $("approvalDetailsPanel").hidden = true;
            loadRequests();
        });

        $("approvalsSearch").addEventListener( "input", renderRequests);
        $("approvalsPrevious").addEventListener("click", () => {
            state.offset = Math.max( 0, state.offset - PAGE_SIZE);
            state.selectedId = null;
            $("approvalDetailsPanel").hidden = true;
            loadRequests();
        });

        $("approvalsNext").addEventListener("click", () => {
            if (state.offset + PAGE_SIZE < state.total) {
                state.offset += PAGE_SIZE;
                state.selectedId = null;
                $("approvalDetailsPanel").hidden = true;
                loadRequests();
            }
        });

        $("approveButton").addEventListener("click", () => showDecision("approved"));
        $("rejectButton").addEventListener("click", () => showDecision("rejected"));
        $("approvalCancel").addEventListener("click", () => {$("approvalDialog").close();});
        $("approvalForm").addEventListener("submit", submitDecision);
        loadRequests();
    });
})();
