
"use strict";

(() => {
    const $ = (id) => document.getElementById(id);
    const reduceMotion = window.matchMedia(
        "(prefers-reduced-motion: reduce)"
    ).matches;

    const scrollBehavior = reduceMotion ? "instant" : "smooth";


    // Navigation
    function initializeNavigation() {
        const toggle = $("mobileMenuToggle");
        const menu = $("homeNavLinks");
        const header = document.querySelector(".home-header");

        if (!toggle || !menu || !header) return;

        const links = [...document.querySelectorAll(
            ".home-nav-link[href^='#']"
        )];

        const sections = links.map((link) => ({
            link,
            section: document.querySelector(link.getAttribute("href")),
        })).filter((item) => item.section);

        function closeMenu() {
            menu.classList.remove("is-open");
            toggle.setAttribute("aria-expanded", "false");
        }

        toggle.addEventListener("click", () => {
            const open = menu.classList.toggle("is-open");
            toggle.setAttribute("aria-expanded", String(open));
        });

        menu.querySelectorAll("a").forEach((link) => {
            link.addEventListener("click", closeMenu);
        });

        document.addEventListener("click", (event) => {
            if (
                menu.classList.contains("is-open") &&
                !menu.contains(event.target) &&
                !toggle.contains(event.target)
            ) {
                closeMenu();
            }
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && menu.classList.contains("is-open")) {
                closeMenu();
                toggle.focus();
            }
        });

        window.addEventListener("resize", () => {
            if (window.innerWidth > 900) closeMenu();
        });

        function updateActiveNavigation() {
            const boundary = header.offsetHeight + 130;
            let active = sections[0];

            for (const item of sections) {
                if (item.section.getBoundingClientRect().top <= boundary) {
                    active = item;
                }
            }

            const atBottom = window.innerHeight + window.scrollY >=
                document.documentElement.scrollHeight - 8;

            if (atBottom) active = sections.at(-1);

            for (const item of sections) {
                const selected = item === active;
                item.link.classList.toggle("is-active", selected);

                if (selected) {
                    item.link.setAttribute("aria-current", "location");
                } else {
                    item.link.removeAttribute("aria-current");
                }
            }
        }

        let scheduled = false;

        window.addEventListener("scroll", () => {
            if (scheduled) return;

            scheduled = true;
            requestAnimationFrame(() => {
                updateActiveNavigation();
                scheduled = false;
            });
        }, {passive: true});

        window.addEventListener("hashchange", () => {
            closeMenu();
            requestAnimationFrame(updateActiveNavigation);
        });

        updateActiveNavigation();
    }


    // Feature and data-source carousels
    function initializeCarousels() {
        document.querySelectorAll(".home-carousel").forEach((carousel) => {
            const previous = document.querySelector(
                `[data-carousel-prev="${carousel.id}"]`
            );

            const next = document.querySelector(
                `[data-carousel-next="${carousel.id}"]`
            );

            const firstCard = carousel.querySelector("article");

            if (!previous || !next || !firstCard) return;

            function cardStep() {
                const styles = getComputedStyle(carousel);
                const gap = parseFloat(styles.columnGap) || 0;

                return firstCard.getBoundingClientRect().width + gap;
            }

            function move(direction) {
                carousel.scrollBy({
                    left: direction * cardStep(),
                    behavior: scrollBehavior,
                });
            }

            function updateArrows() {
                const maxScroll = Math.max(
                    0,
                    carousel.scrollWidth - carousel.clientWidth
                );

                previous.disabled = carousel.scrollLeft <= 2;
                next.disabled = carousel.scrollLeft >= maxScroll - 2;
            }

            previous.addEventListener("click", () => move(-1));
            next.addEventListener("click", () => move(1));

            carousel.addEventListener("scroll", updateArrows, {
                passive: true,
            });

            carousel.addEventListener("keydown", (event) => {
                if (event.target !== carousel) return;

                if (event.key === "ArrowRight") {
                    event.preventDefault();
                    move(1);
                }

                if (event.key === "ArrowLeft") {
                    event.preventDefault();
                    move(-1);
                }
            });

            window.addEventListener("resize", updateArrows);

            if ("ResizeObserver" in window) {
                const observer = new ResizeObserver(updateArrows);
                observer.observe(carousel);
            }

            requestAnimationFrame(updateArrows);
        });
    }


    // Interactive star ratings
    function initializeStarRating() {
        const container = $("reviewStars");
        const description = $("ratingDescription");

        if (!container || !description) return;

        const options = [...container.querySelectorAll(
            ".home-star-option"
        )];

        const labels = {
            1: "Needs improvement",
            2: "Fair",
            3: "Good",
            4: "Very good",
            5: "Excellent",
        };

        function selectedRating() {
            const selected = container.querySelector(
                'input[name="rating"]:checked'
            );

            return selected ? Number(selected.value) : 0;
        }

        function paintStars(rating, preview = false) {
            options.forEach((option) => {
                const input = option.querySelector("input");
                const star = option.querySelector("span");
                const filled = Number(input.value) <= rating;

                option.classList.toggle("is-filled", filled);

                star.style.color = filled ? "#ecaa22" : "#bac6d4";
            });

            description.textContent = rating
                ? `${rating}/5 stars — ${labels[rating]}${preview ? " (preview)" : ""}`
                : "Select a star rating";
        }

        options.forEach((option) => {
            const input = option.querySelector("input");
            const rating = Number(input.value);

            option.addEventListener("pointerenter", () => {
                paintStars(rating, true);
            });

            input.addEventListener("focus", () => {
                paintStars(rating, true);
            });

            input.addEventListener("change", () => {
                paintStars(selectedRating());
            });
        });

        container.addEventListener("pointerleave", () => {
            paintStars(selectedRating());
        });

        container.addEventListener("focusout", (event) => {
            if (!container.contains(event.relatedTarget)) {
                paintStars(selectedRating());
            }
        });

        paintStars(selectedRating());
    }


    // Frontend-only feedback preview
    function initializeFeedback() {
        const form = $("reviewForm");
        const status = $("reviewStatus");

        if (!form || !status) return;

        const preview = document.createElement("section");
        preview.id = "reviewPreview";
        preview.className = "home-sources-note";
        preview.hidden = true;
        preview.setAttribute("aria-label", "Feedback preview");
        preview.style.marginTop = "18px";

        form.after(preview);

        function addPreviewText(parent, tag, value) {
            const element = document.createElement(tag);
            element.textContent = value;
            parent.append(element);
        }

        function clearPreview() {
            preview.hidden = true;
            preview.replaceChildren();
            status.textContent = "";
        }

        form.addEventListener("input", clearPreview);
        form.addEventListener("change", clearPreview);

        form.addEventListener("submit", (event) => {
            event.preventDefault();

            const rating = form.querySelector(
                'input[name="rating"]:checked'
            );

            const email = $("reviewEmail").value.trim();
            const message = $("reviewMessage").value.trim();

            if (!rating) {
                status.textContent = "Please select a star rating.";
                return;
            }

            if (message.length < 10) {
                status.textContent =
                    "Please enter at least 10 meaningful characters.";
                return;
            }

            preview.replaceChildren();

            addPreviewText(
                preview,
                "strong",
                "Your Feedback Preview — Not Submitted"
            );

            addPreviewText(
                preview,
                "p",
                `Rating: ${rating.value} out of 5 stars`
            );

            addPreviewText(
                preview,
                "p",
                `Email: ${email}`
            );

            addPreviewText(
                preview,
                "p",
                message
            );

            addPreviewText(
                preview,
                "p",
                "This is a preview only. No feedback has been saved or emailed."
            );

            preview.hidden = false;

            status.textContent =
                "Preview generated successfully. Nothing has been submitted.";

            preview.scrollIntoView({
                behavior: scrollBehavior,
                block: "nearest",
            });
        });
    }


    // Footer and startup
    function initializeFooter() {
        const year = $("footerYear");
        if (year) year.textContent = new Date().getFullYear();
    }

    function initializeHomepage() {
        initializeNavigation();
        initializeCarousels();
        initializeStarRating();
        initializeFeedback();
        initializeFooter();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initializeHomepage);
    } else {
        initializeHomepage();
    }
})();
