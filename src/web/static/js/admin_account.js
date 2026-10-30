"use strict";
(() => {
    const $ = (id) => document.getElementById(id);

    function setText(id, value) {
        const element = $(id);
        if (element) {
            element.textContent = value ?? "—";
        }
    }

    function formatStatus(value) {
        if (!value) return "Not available";

        return String(value)
            .replaceAll("_", " ")
            .replace(/\b\w/g, character => character.toUpperCase());
    }

    async function loadAccount() {
        try {
            const response = await fetch("/auth/session", {
                credentials: "same-origin",
                cache: "no-store"
            });

            if (!response.ok) {
                throw new Error("Unable to load authentication session.");
            }

            const session = await response.json();
            const user = session.user;

            if (
                !session.authenticated ||
                !user ||
                user.role !== "admin"
            ) {
                window.location.replace(
                    "/static/auth/login.html"
                );
                return;
            }

            setText("accountName", user.display_name);
            setText("accountEmail", user.email);
            setText("accountRole", formatStatus(user.role));
            setText(
                "accountVerification",
                user.email_verified ? "Verified" : "Not Verified"
            );
            setText(
                "accountStatus",
                formatStatus(user.account_status)
            );

            $("accountNotice").hidden = true;

        } catch (error) {
            console.error("Administrator account error:", error);

            setText(
                "accountNotice",
                "Account information could not be loaded."
            );
        }
    }

    async function signOut() {
        const button = $("adminLogout");
        button.disabled = true;

        setText("logoutNotice", "Signing out…");

        try {
            const response = await fetch("/auth/logout", {
                method: "POST",
                credentials: "same-origin",
                cache: "no-store"
            });

            if (!response.ok) {
                throw new Error("Logout request failed.");
            }

            window.location.replace("/");

        } catch (error) {
            console.error("Logout error:", error);

            setText(
                "logoutNotice",
                "Unable to sign out. Please try again."
            );

            button.disabled = false;
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        loadAccount();

        $("adminLogout")?.addEventListener(
            "click",
            signOut
        );
    });
})();
