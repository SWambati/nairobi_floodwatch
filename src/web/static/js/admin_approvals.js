"use strict";


(() => {
    //CONFIGURATION AND STATE
    const $ = (id) => document.getElementById(id);
    const API = "/api/admin/agency-requests";
    const PAGE_SIZE = 50;
    const VALID_STATUSES = new Set(["pending", "approved", "rejected", "withdrawn"]);
    const state = {
        requests: [],
        selectedId: null,
        selectedApplication: null,
        decision: null,
        offset: 0,
        total: 0,
        submitting: false,
        listRequestSerial: 0,
        detailRequestSerial: 0
    };

    //GENERAL UTILITIES
    function write(id, value) {
        const element = $(id);

        if (element) {
            element.textContent = String(value ?? "—");
        }
    }


    function localTime(value) {
        if (!value) {
            return "—";
        }

        const date = new Date(value);

        if (Number.isNaN(date.getTime())) {
            return "—";
        }

        return new Intl.DateTimeFormat("en-KE", {
            timeZone: "Africa/Nairobi",
            dateStyle: "medium",
            timeStyle: "short"
        }).format(date);
    }

    function formatValue(value) {
        if (value === null || value === undefined || value === "") {
            return "—";
        }
        return String(value);
    }

    function formatStatus(value) {
        if (!value) {
            return "Unknown";
        }
        return String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
    }

    //SHARED API REQUEST HANDLER
    async function requestJSON(url, options = {}) {
        const response = await fetch(url, {credentials: "same-origin", cache: "no-store", ...options});
        const payload = await response.json().catch(() => ({}));

        if (!response.ok) {
            let message = `Request failed (${response.status}).`;

            if (typeof payload.detail === "string") {
                message = payload.detail;

            } else if (Array.isArray(payload.detail)) {
                message = payload.detail.map((error) => error.msg || "Invalid request.").join(" ");
            }
            throw new Error(message);
        }
        return payload;
    }

    //TABLE CELL CREATION
    function addCell(row, value) {
        const cell = document.createElement("td");
        cell.textContent = formatValue(value);
        row.append(cell);
        return cell;
    }

    //APPLICATION STATUS BADGES
    function updateStatusBadge(element, status) {
        const value = String(status || "").trim().toLowerCase();
        element.className = "approvals-status";

        if (VALID_STATUSES.has(value)) {
            element.classList.add(`status-${value}`);
        }

        element.textContent = formatStatus(value);
        return element;
    }

    function createStatusBadge(status) {
        const badge = document.createElement("span");
        return updateStatusBadge(badge, status);
    }

    // 6. EMPTY TABLE MESSAGE
    function showTableMessage(message) {
        const tbody = $("approvalsRows");
        tbody.replaceChildren();
        const row = document.createElement("tr");
        const cell = addCell(row, message);
        cell.colSpan = 5;
        row.append();
        tbody.append(row);
    }

    //RENDER APPLICATION TABLE
    function renderRequests() {
        const tbody = $("approvalsRows");
        tbody.replaceChildren();
        const search = $("approvalsSearch").value.trim().toLowerCase();

        const filtered = state.requests.filter((item) => {
            return [item.applicant_name, item.applicant_email, item.organization_name, item.organization_email].some((value) =>
                String(value || "").toLowerCase().includes(search)
            );
        });

        if (filtered.length === 0) {
            showTableMessage(search ? "No applications match your search." : "No agency applications found.");
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

            const statusCell = document.createElement("td");
            statusCell.append(createStatusBadge(item.status));
            row.append(statusCell);

            const actionCell = document.createElement("td");
            const button = document.createElement("button");

            button.type = "button";
            button.className = "button secondary";
            button.textContent = "View";
            button.setAttribute("aria-label", `View application from ${item.applicant_name || "applicant"}`);
            button.addEventListener("click", () => {openApplication(item.id);});
            actionCell.append(button);
            row.append(actionCell);
            tbody.append(row);
        }
    }

    //FETCH APPLICATIONS
    async function loadRequests() {
        const status = $("approvalsStatus").value;
        const serial = ++state.listRequestSerial;

        write("approvalsMessage", "Loading applications…");

        try {
            const params = new URLSearchParams({status, limit: String(PAGE_SIZE), offset: String(state.offset)});
            const data = await requestJSON(`${API}?${params.toString()}`);

            // Ignore results from an older filter/page request.
            if (serial !== state.listRequestSerial) {
                return;
            }

            state.requests = Array.isArray(data.requests) ? data.requests : [];
            state.total = Number(data.total || 0);

            const start = state.requests.length ? state.offset + 1 : 0;
            const end = state.offset + state.requests.length;

            write("approvalsMessage", `Showing ${start}–${end} of ${state.total} applications.`);
            const currentPage = Math.floor(state.offset / PAGE_SIZE) + 1;

            write("approvalsPageStatus", `Page ${currentPage}`);
            $("approvalsPrevious").disabled = state.offset === 0;
            $("approvalsNext").disabled = end >= state.total;

            renderRequests();
        } catch (error) {
            if (serial !== state.listRequestSerial) {
                return;
            }

            console.error("Failed to load agency applications:", error);
            state.requests = [];

            showTableMessage("Unable to load applications.");

            $("approvalsPrevious").disabled = true;
            $("approvalsNext").disabled = true;

            write("approvalsMessage", error.message);
        }
    }

    //CLEAR SELECTED APPLICATION
    function clearSelectedApplication() {
        ++state.detailRequestSerial;
        state.selectedId = null;
        state.selectedApplication = null;
        state.decision = null;
        $("approvalDetailsPanel").hidden = true;
        $("approvalActions").hidden = true;
    }

    // 10. APPROVAL ELIGIBILITY
    function approvalEligibility(item) {
        if (item.status !== "pending") {
            return {allowed: false, reason: "This application is no longer pending."};
        }

        if (!item.email_verified) {
            return {
                allowed: false,
                reason: ("The applicant must verify their registered " +  "email address before agency approval.")
            };
        }

        if (item.account_status !== "active") {
            return {
                allowed: false,
                reason: ("The applicant's account must be active " + "before agency approval.")
            };
        }

        if (item.applicant_role !== "resident") {
            return {
                allowed: false,
                reason: ("The applicant is no longer eligible " + "for agency role activation.")
            };
        }
        return {
            allowed: true,
            reason: ""
        };
    }

    // VIEW APPLICATION DETAILS
    async function openApplication(id) {
        state.selectedId = id;
        state.selectedApplication = null;
        const serial = ++state.detailRequestSerial;
        const panel = $("approvalDetailsPanel");
        panel.hidden = false;
        $("approvalActions").hidden = true;
        write("applicationTitle", "Loading application…");

        try {
            const item = await requestJSON(`${API}/${encodeURIComponent(id)}`);

            if (state.selectedId !== id || serial !== state.detailRequestSerial) {
                return;
            }

            state.selectedApplication = item;
            write("applicationTitle", "Application Details");

            // Update the existing status badge in place.
            // Do not replace its DOM element or ID.
            updateStatusBadge($("detailStatus"), item.status);

            // Applicant information
            write("detailName", item.applicant_name);
            write("detailEmail", item.applicant_email);
            write("detailEmailVerified", item.email_verified ? "Verified" : "Not Verified");
            write("detailAccountStatus", formatStatus(item.account_status));

            // Institution information
            write("detailOrganization", item.organization_name);
            write("detailOrganizationEmail", item.organization_email || "Not supplied");
            write("detailJustification", item.justification);

            // Review information
            write("detailCreated", localTime(item.created_at));
            write("detailReviewedBy", item.reviewer_name || "Not reviewed");
            write("detailNotes", item.review_notes || "—");
            
            // APPLICATION APPROVAL ELIGIBILITY
            const isPending = item.status === "pending";
            const approvalActions = $("approvalActions");
            const approveButton = $("approveButton");
            const rejectButton = $("rejectButton");
            const notice = $("approvalEligibilityNotice");
            
            approvalActions.hidden = !isPending;
            notice.hidden = true;
            notice.textContent = "";
            
            approveButton.disabled = false;
            approveButton.removeAttribute("title");
            rejectButton.disabled = false;
            
            if (isPending) {
                const eligibility = approvalEligibility(item);
                if (!eligibility.allowed) {
                    
                    // Prevent premature approval.
                    approveButton.disabled = true;
                    approveButton.title = eligibility.reason;
                    
                    // Clearly explain the restriction.
                    notice.textContent = eligibility.reason;
                    notice.hidden = false;
                } else {
                    approveButton.disabled = false;
                    approveButton.title = "Approve this agency application";
                }
                
                // Rejection is permitted even when email verification is incomplete.
                rejectButton.disabled = false;
            }     
            panel.scrollIntoView({behavior: "smooth", block: "start"});
        } catch (error) {
            if (serial !== state.detailRequestSerial) {
                return;
            }
            console.error("Unable to load application details:", error);
            state.selectedApplication = null;
            write("applicationTitle", "Application could not be loaded");
            $("approvalActions").hidden = true;
        }
    }

    //SHOW APPROVAL / REJECTION DIALOG
    function showDecision(decision) {
        if (!state.selectedId || !state.selectedApplication || state.submitting) {
            return;
        }

        if (state.selectedApplication.status !== "pending") {
            return;
        }

        const approving = decision === "approved";

        if (approving) {
            const eligibility = approvalEligibility(state.selectedApplication);

            if (!eligibility.allowed) {
                write("approvalsMessage", eligibility.reason);
                return;
            }
        }
        state.decision = decision;

        write("approvalDialogTitle", approving ? "Approve Agency Access" : "Reject Agency Access");
        write( "approvalDialogText", approving ? ("This grants agency privileges and ends " + "the applicant's existing sessions.") : ("This rejects the application without " + "granting agency access."));
        write("notesRequired", approving ? "(optional)" : "(required)");
        $("approvalNotes").value = "";
        $("affiliationConfirmed").checked = false;
        $("affiliationLabel").hidden = !approving;

        write("approvalDialogError", "");
        $("approvalDialog").showModal();
    }

    //SUBMIT APPROVAL / REJECTION
    async function submitDecision(event) {
        event.preventDefault();
        if (state.submitting || !state.selectedId || !state.selectedApplication || !state.decision) {
            return;
        }

        const notes = $("approvalNotes").value.trim();
        const verified = $("affiliationConfirmed").checked;
        const approving = state.decision === "approved";

        // Rejection requires an explanation.
        if (!approving && notes.length < 5) {
            write("approvalDialogError", "Please provide a rejection reason of at least five characters.");
            return;
        }

        // Approval requires institutional verification.
        if (approving && !verified) {
            write("approvalDialogError", "Confirm that institutional affiliation was independently verified.");
            return;
        }

        // Re-check frontend eligibility immediately before submission.
        if (approving) {
            const eligibility = approvalEligibility(state.selectedApplication);

            if (!eligibility.allowed) {
                write("approvalDialogError", eligibility.reason);
                return;
            }
        }

        const id = state.selectedId;
        const decision = state.decision;
        state.submitting = true;
        $("approvalConfirm").disabled = true;
        $("approvalCancel").disabled = true;
        write("approvalDialogError", "Saving decision…");

        try {
            await requestJSON(
                `${API}/${encodeURIComponent(id)}/decision`,
                {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({decision, review_notes: notes, affiliation_confirmed: verified})
                }
            );

            $("approvalDialog").close();
            write("approvalsMessage", decision === "approved" ? "Agency application approved successfully." : "Agency application rejected successfully.");

            // The selected status filter may no longer  include the reviewed application.
            await loadRequests();

            // Refresh details from PostgreSQL and display the new approved/rejected status.
            await openApplication(id);
        } catch (error) {
            console.error("Agency review decision failed:", error);
            write("approvalDialogError", error.message);
        } finally {
            state.submitting = false;
            $("approvalConfirm").disabled = false;
            $("approvalCancel").disabled = false;
        }
    }

    //PAGINATION
    function previousPage() {
        if (state.submitting || state.offset === 0) {
            return;
        }
        state.offset = Math.max(0, state.offset - PAGE_SIZE);
        clearSelectedApplication();
        loadRequests();
    }

    function nextPage() {
        if (state.submitting) {
            return;
        }

        if (state.offset + PAGE_SIZE >= state.total) {
            return;
        }
        state.offset += PAGE_SIZE;
        clearSelectedApplication();
        loadRequests();
    }

    //INITIALIZATION
    document.addEventListener("DOMContentLoaded", () => {
        $("approvalsRefresh").addEventListener("click", loadRequests);
        $("approvalsStatus").addEventListener(
            "change",
            () => {
                state.offset = 0;
                clearSelectedApplication();
                loadRequests();
            }
        );

        $("approvalsSearch").addEventListener("input", renderRequests);
        $("approvalsPrevious").addEventListener("click", previousPage);
        $("approvalsNext").addEventListener("click", nextPage);
        $("approveButton").addEventListener("click", () => showDecision("approved"));
        $("rejectButton").addEventListener("click", () => showDecision("rejected"));
        $("approvalCancel").addEventListener(
            "click",
            () => {
                if (!state.submitting) {
                    $("approvalDialog").close();
                }
            }
        );

        $("approvalForm").addEventListener("submit", submitDecision);
        loadRequests();
    });
})();
