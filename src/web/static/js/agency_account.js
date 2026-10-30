
"use strict";

(() => {
    const $ = (id) => document.getElementById(id);
    const ACCOUNT_API = "/api/agency/account";

    function write(id, value) {
        const element = $(id);
        if (!element) return;
        element.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
    }

    function formatStatus(value) {
        if (!value) return "Not available";
        return String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
    }

    function formatDate(value) {
        if (!value) return "—";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) {
            return "—";
        }
        return new Intl.DateTimeFormat("en-KE", {timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "short"}).format(date);
    }

    function setApprovalStatus(status) {
        const badge = $("agencyApprovalBadge");
        if (!badge) return;
        const value = String(status || "").trim().toLowerCase();
        const allowed = new Set(["approved", "pending", "rejected"]);
        badge.className = "agency-account-badge";

        if (allowed.has(value)) {
            badge.classList.add(value);
        }
        badge.textContent = status ? formatStatus(status) : "Not linked";
    }

    function renderAccount(data) {
        const account = data.account || {};
        const organization = data.organization || {};
        const security = data.security || {};

        // Profile information
        write("accountName", account.display_name);
        write("accountEmail", account.email);
        write("accountRole", formatStatus(account.role));
        write("accountVerification", account.email_verified ? "Verified" : "Not Verified");
        write("accountStatus", formatStatus(account.account_status));
        write("accountCreated", formatDate(account.created_at));
        write("organizationName", organization.name);
        write("organizationPosition", organization.position_title);
        write("organizationEmail", organization.contact_email);
        write("organizationStatus", formatStatus(organization.application_status));
        write("organizationApplied", formatDate(organization.applied_at));
        write("organizationApproved", formatDate(organization.approved_at));
        setApprovalStatus(organization.application_status);
        // Security policy information
        write("agencyMfaStatus", security.mfa_required ? "Multi-factor authentication is required for Agency Dashboard access." : "Security policy information is unavailable.");
    }

    async function loadAgencyAccount() {
        const notice = $("agencyAccountNotice");
        const content = $("agencyAccountContent");
        notice.hidden = false;
        notice.classList.remove("error");
        content.hidden = true;
        write("agencyAccountNotice", "Loading account information…");

        try {
            const response = await fetch(ACCOUNT_API, {
                method: "GET",
                credentials: "same-origin",
                cache: "no-store",
                headers: {"Accept": "application/json"}
            });

            if (!response.ok) {
                if (response.status === 401) {
                    throw new Error("Your session has expired. Please sign in again.");
                }

                if (response.status === 403) {
                    throw new Error("Agency authorization or additional security verification is required.");
                }
                throw new Error("Unable to retrieve your account information.");
            }

            const data = await response.json();

            if (!data.account || data.account.role !== "agency") {
                throw new Error("This page is only available to authorized Agency Officers.");
            }
            renderAccount(data);
            notice.hidden = true;
            content.hidden = false;
        } catch (error) {
            console.error("Unable to load Agency account:", error.message);
            notice.classList.add("error");
            write("agencyAccountNotice", error.message);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", loadAgencyAccount, { once: true });
    } else {
        loadAgencyAccount();
    }
})();
