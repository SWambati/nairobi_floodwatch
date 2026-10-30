
"use strict";

(() => {
    const TIME_ZONE = "Africa/Nairobi";

    function getGreeting() {
        const hour = Number(
            new Intl.DateTimeFormat("en-GB", {
                timeZone: TIME_ZONE,
                hour: "2-digit",
                hourCycle: "h23"
            }).format(new Date())
        );

        if (hour < 12) return "Good Morning";
        if (hour < 17) return "Good Afternoon";
        return "Good Evening";
    }

    async function initializeGreeting() {
        const main = document.querySelector("main");
        if (!main) return;

        try {
            const response = await fetch("/auth/session", {
                credentials: "same-origin",
                cache: "no-store"
            });

            if (!response.ok) return;

            const session = await response.json();

            if (!session.authenticated || !session.user) {
                return;
            }

            const fullName =
                String(session.user.display_name || "").trim()
                || "there";

            let banner = document.getElementById(
                "dashboardWelcome"
            );

            if (!banner) {
                banner = document.createElement("section");
                banner.id = "dashboardWelcome";
                banner.className = "dashboard-welcome";

                const top = document.createElement("div");
                top.className = "dashboard-welcome__top";

                const label = document.createElement("span");
                label.className = "dashboard-welcome__label";
                label.textContent = "WELCOME BACK";

                const timezone = document.createElement("span");
                timezone.className = "dashboard-welcome__timezone";
                timezone.textContent = "NAIROBI · EAT";

                top.append(label, timezone);

                const greeting = document.createElement("h2");
                greeting.id = "dashboardGreeting";
                greeting.className = "dashboard-welcome__heading";

                banner.append(top, greeting);
                main.prepend(banner);
            }

            const greeting = document.getElementById(
                "dashboardGreeting"
            );

            function updateGreeting() {
                const message = `${getGreeting()}, ${fullName}`;

                if (greeting.textContent !== message) {
                    greeting.textContent = message;
                }
            }

            updateGreeting();

            window.setInterval(updateGreeting, 60_000);

            document.addEventListener(
                "visibilitychange",
                () => {
                    if (!document.hidden) updateGreeting();
                }
            );
        } catch (error) {
            console.warn(
                "Unable to load personalized greeting.",
                error
            );
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener(
            "DOMContentLoaded",
            initializeGreeting,
            { once: true }
        );
    } else {
        initializeGreeting();
    }
})();
