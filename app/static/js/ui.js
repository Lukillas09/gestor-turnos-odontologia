(function () {
    "use strict";

    document.addEventListener("click", function (event) {
        const close = event.target.closest("[data-message-close]");

        if (close) {
            const message = close.closest("[data-message]");
            if (message) {
                message.remove();
            }
        }

        const mobileClose = event.target.closest("[data-mobile-more-close]");
        if (mobileClose) {
            const details = mobileClose.closest("[data-mobile-more]");
            if (details) {
                details.open = false;
                const summary = details.querySelector("summary");
                if (summary && !mobileClose.matches("[aria-hidden='true']")) {
                    summary.focus();
                }
            }
        }

        if (!event.target.closest(".public-mobile-menu")) {
            document.querySelectorAll(".public-mobile-menu[open]").forEach(function (details) {
                details.open = false;
            });
        }
    });

    document.addEventListener("keydown", function (event) {
        const openMenu = document.querySelector("[data-mobile-more][open]");

        if (event.key === "Tab" && openMenu) {
            const panel = openMenu.querySelector(".mobile-more-panel");
            const focusable = panel
                ? Array.from(panel.querySelectorAll("a[href], button:not([disabled]), input:not([disabled])"))
                : [];
            if (focusable.length) {
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            }
            return;
        }

        if (event.key !== "Escape") {
            return;
        }

        document.querySelectorAll("[data-mobile-more][open]").forEach(function (details) {
            details.open = false;
            const summary = details.querySelector("summary");
            if (summary) {
                summary.focus();
            }
        });

        document.querySelectorAll(".public-mobile-menu[open]").forEach(function (details) {
            details.open = false;
            const summary = details.querySelector("summary");
            if (summary) {
                summary.focus();
            }
        });
    });

    document.querySelectorAll(".public-mobile-menu").forEach(function (details) {
        const summary = details.querySelector(":scope > summary");

        function syncState() {
            if (summary) {
                summary.setAttribute("aria-expanded", details.open ? "true" : "false");
                summary.setAttribute(
                    "aria-label",
                    details.open ? "Cerrar navegación" : "Abrir navegación"
                );
            }
        }

        syncState();
        details.addEventListener("toggle", syncState);
    });

    document.querySelectorAll("[data-mobile-more]").forEach(function (details) {
        const summary = details.querySelector("summary");
        details.addEventListener("toggle", function () {
            if (summary) {
                summary.setAttribute("aria-expanded", details.open ? "true" : "false");
            }
            document.body.classList.toggle("is-mobile-menu-open", details.open);
            document.querySelectorAll(".app-topbar, .app-page").forEach(function (region) {
                region.inert = details.open;
            });
            if (details.open) {
                const firstControl = details.querySelector(".mobile-more-panel a, .mobile-more-panel button");
                if (firstControl) {
                    firstControl.focus();
                }
            }
        });
    });

    document.querySelectorAll("[data-patient-directory-filters]").forEach(function (details) {
        const summary = details.querySelector(":scope > summary");

        if (!summary) {
            return;
        }

        function syncState() {
            summary.setAttribute("aria-expanded", details.open ? "true" : "false");
        }

        syncState();
        details.addEventListener("toggle", syncState);
        details.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && details.open) {
                event.preventDefault();
                details.open = false;
                summary.focus();
            }
        });
    });

    function initializeTurnoActionsMenus() {
        const menus = Array.from(document.querySelectorAll("[data-row-actions-menu]"));

        if (!menus.length) {
            return;
        }

        function syncState(details) {
            const trigger = details.querySelector(":scope > summary");
            const card = details.closest("[data-turno-card]");

            if (trigger) {
                trigger.setAttribute("aria-expanded", details.open ? "true" : "false");
            }
            if (card) {
                card.classList.toggle("is-actions-menu-open", details.open);
            }
            if (!details.open) {
                details.classList.remove("is-menu-above");
            }
        }

        function positionMenu(details) {
            const trigger = details.querySelector(":scope > summary");
            const panel = details.querySelector(":scope > [role='menu']");

            if (!details.open || !trigger || !panel) {
                return;
            }

            details.classList.remove("is-menu-above");
            const triggerBox = trigger.getBoundingClientRect();
            const panelHeight = panel.offsetHeight;
            const spaceBelow = window.innerHeight - triggerBox.bottom - 8;
            const spaceAbove = triggerBox.top - 8;

            if (spaceBelow < panelHeight && spaceAbove > spaceBelow) {
                details.classList.add("is-menu-above");
            }
        }

        function closeMenu(details, returnFocus) {
            if (!details.open) {
                syncState(details);
                return;
            }

            details.open = false;
            syncState(details);

            if (returnFocus) {
                const trigger = details.querySelector(":scope > summary");
                if (trigger) {
                    trigger.focus();
                }
            }
        }

        function closeOtherMenus(current) {
            menus.forEach(function (details) {
                if (details !== current) {
                    closeMenu(details, false);
                }
            });
        }

        function positionOpenMenu() {
            const openMenu = menus.find(function (details) {
                return details.open;
            });
            if (openMenu) {
                positionMenu(openMenu);
            }
        }

        menus.forEach(function (details) {
            if (details.dataset.actionsMenuInitialized === "true") {
                return;
            }
            details.dataset.actionsMenuInitialized = "true";
            syncState(details);

            details.addEventListener("toggle", function () {
                if (details.open) {
                    closeOtherMenus(details);
                }
                syncState(details);
                if (details.open) {
                    window.requestAnimationFrame(function () {
                        positionMenu(details);
                    });
                }
            });

            details.addEventListener("click", function (event) {
                if (event.target.closest("[role='menuitem']")) {
                    closeMenu(details, false);
                }
            });
        });

        document.addEventListener("click", function (event) {
            if (event.target.closest("[data-row-actions-menu]")) {
                return;
            }
            menus.forEach(function (details) {
                closeMenu(details, false);
            });
        });

        document.addEventListener("keydown", function (event) {
            if (event.key !== "Escape") {
                return;
            }
            const openMenu = menus.find(function (details) {
                return details.open;
            });
            if (openMenu) {
                event.preventDefault();
                closeMenu(openMenu, true);
            }
        });

        window.addEventListener("resize", positionOpenMenu);
        window.addEventListener("scroll", positionOpenMenu, {
            passive: true,
            capture: true,
        });
        window.addEventListener("pagehide", function () {
            menus.forEach(function (details) {
                closeMenu(details, false);
            });
        });
    }

    initializeTurnoActionsMenus();

    document.querySelectorAll("[data-password-toggle]").forEach(function (toggle) {
        const inputId = toggle.getAttribute("aria-controls");
        const input = inputId ? document.getElementById(inputId) : null;
        const showIcon = toggle.querySelector("[data-password-show]");
        const hideIcon = toggle.querySelector("[data-password-hide]");

        if (!input) {
            return;
        }

        toggle.addEventListener("click", function () {
            const isVisible = input.type === "text";
            input.type = isVisible ? "password" : "text";
            toggle.setAttribute("aria-pressed", isVisible ? "false" : "true");
            toggle.setAttribute(
                "aria-label",
                isVisible ? "Mostrar contraseña" : "Ocultar contraseña"
            );
            if (showIcon) {
                showIcon.hidden = !isVisible;
            }
            if (hideIcon) {
                hideIcon.hidden = isVisible;
            }
            input.focus({ preventScroll: true });
        });
    });

    document.addEventListener("submit", function (event) {
        const submitter = event.submitter;

        if (!submitter || submitter.matches("[data-no-loading]")) {
            return;
        }

        submitter.classList.add("button-loading");
        submitter.setAttribute("aria-busy", "true");

        if (event.target.matches("[data-login-form]")) {
            const label = submitter.querySelector("[data-submit-label]");
            if (label) {
                label.textContent = "Ingresando...";
            }
            submitter.disabled = true;
        }
    });

    window.addEventListener("pageshow", function () {
        document.querySelectorAll("[data-login-form] [type='submit']").forEach(function (button) {
            const label = button.querySelector("[data-submit-label]");
            button.disabled = false;
            button.classList.remove("button-loading");
            button.removeAttribute("aria-busy");
            if (label) {
                label.textContent = "Ingresar de forma segura";
            }
        });
    });

    document.querySelectorAll(".form-field").forEach(function (wrapper) {
        const control = wrapper.querySelector("input, select, textarea");
        const help = wrapper.querySelector(".field-help[id]");
        const error = wrapper.querySelector(".field-error[id], .errorlist[id]");

        if (!control) {
            return;
        }

        const descriptions = [help, error]
            .filter(Boolean)
            .map(function (element) {
                return element.id;
            });

        if (descriptions.length) {
            control.setAttribute("aria-describedby", descriptions.join(" "));
        }

        if (wrapper.querySelector(".field-error, .errorlist")) {
            control.setAttribute("aria-invalid", "true");
        }
    });
})();
