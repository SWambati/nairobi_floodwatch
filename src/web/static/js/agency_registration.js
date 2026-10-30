"use strict";

(() => {
    const form = document.getElementById("agencyRegistrationForm");

    if (!form) return;

    const button = document.getElementById("registerAgencyButton");
    const message = document.getElementById("registrationMessage");

    function showMessage(text, isError = false) {
        message.textContent = text;
        message.hidden = false;
        message.classList.toggle("is-error", isError);
        message.classList.toggle("is-success", !isError);
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        const data = new FormData(form);
        const password = String(data.get("password") || "");
        const confirmPassword = String(data.get("confirm_password") || "");

        if (password !== confirmPassword) {
            showMessage("The passwords do not match.", true);
            return;
        }
        
        const validPassword = password.length >= 8 && /[A-Z]/.test(password) && /[a-z]/.test(password) && /[0-9]/.test(password) && /[^A-Za-z0-9\s]/.test(password);

        if (!validPassword) {
            showMessage("Use at least 8 characters, including uppercase " + "and lowercase letters, a number and a special symbol.",
                true
            );
            return;
        }

        // if (password.length < 8) {
        //     showMessage("Your password must contain at least 8 characters.", true);
        //     return;
        // }

        const payload = {
            display_name: String(data.get("display_name") || "").trim(),
            email: String(data.get("email") || "").trim(),
            password,
            organization_name: String(data.get("organization_name") || "").trim(),
            position_title: String(data.get("position_title") || "").trim(),
            organization_email: String(data.get("organization_email") || "").trim() || null,
            justification: String(data.get("justification") || "").trim(),
        };

        button.disabled = true;
        button.textContent = "Submitting registration…";
        message.hidden = true;

        try {
            const response = await fetch(
                "/auth/agency/register",
                {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(payload),
                }
            );

            const result = await response.json().catch(() => ({}));

            if (!response.ok) {
                const detail = result.detail;
                throw new Error(typeof detail === "string" ? detail : "Registration could not be completed.");
            }

            form.reset();

            showMessage(
                "Registration request received. " +
                "If eligible, check your email to verify " +
                "your account. After verification, " +
                "an administrator will review your " +
                "agency access request."
            );

            button.textContent = "Request Submitted";

        } catch (error) {
            showMessage(error.message, true);
            button.disabled = false;
            button.textContent = "Submit Agency Registration";
        }
    });
    document.querySelectorAll("[data-password-target]").forEach(button => {
        button.addEventListener("click", () => {
            const input = document.getElementById(button.dataset.passwordTarget);

            if (!input) return;

            const showing = input.type === "text";
            input.type = showing ? "password" : "text";
            const label = showing ? "Show password" : "Hide password";
            
            button.setAttribute("aria-label", label);
            button.setAttribute("title", label);
            button.setAttribute("aria-pressed", String(!showing));
        });
    });
})();
