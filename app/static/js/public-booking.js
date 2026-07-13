(function () {
    "use strict";

    const panel = document.querySelector("[data-public-availability]");

    if (!panel) {
        return;
    }

    const form = panel.querySelector("[data-public-search-form]");
    const results = panel.querySelector("[data-public-results]");
    const availabilityUrl = panel.dataset.availabilityUrl;
    const actionBar = panel.querySelector("[data-public-slot-action]");
    const actionLabel = panel.querySelector("[data-public-slot-label]");
    const actionLink = panel.querySelector("[data-public-slot-continue]");
    const odontologoControl = form ? form.elements.namedItem("odontologo") : null;
    const fechaControl = form ? form.elements.namedItem("fecha") : null;
    let activeRequest = null;
    let debounceTimer = null;
    const debounceDelay = 350;

    if (!form || !results || !availabilityUrl || !odontologoControl || !fechaControl) {
        return;
    }

    form.classList.add("is-enhanced");

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function setBusy(isBusy) {
        results.setAttribute("aria-busy", isBusy ? "true" : "false");
    }

    function hideAction() {
        if (!actionBar) {
            return;
        }
        actionBar.hidden = true;
        actionLabel.textContent = "";
        actionLink.removeAttribute("href");
    }

    function selectProfessional(id) {
        odontologoControl.value = String(id || "");
        form.querySelectorAll("[data-public-professional]").forEach(function (button) {
            const selected = button.dataset.publicProfessional === String(id);
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-pressed", selected ? "true" : "false");
        });
    }

    function renderEmpty(title, message) {
        setBusy(false);
        results.innerHTML = `
            <div class="empty-state public-booking-empty animate-fade-up">
                <h3>${escapeHtml(title)}</h3>
                <p>${escapeHtml(message)}</p>
            </div>
        `;
    }

    function renderError() {
        setBusy(false);
        results.innerHTML = `
            <div class="empty-state public-booking-empty public-booking-error animate-fade-up" role="alert">
                <h3>No pudimos cargar los horarios</h3>
                <p>Revisá tu conexión e intentá nuevamente.</p>
                <button class="button button-secondary" type="button" data-public-retry>Reintentar</button>
            </div>
        `;
    }

    function renderLoading() {
        hideAction();
        setBusy(true);
        results.innerHTML = `
            <div class="public-booking-skeleton" aria-label="Buscando horarios disponibles">
                <div class="ui-skeleton ui-skeleton-line ui-skeleton-line-short"></div>
                <div class="public-booking-skeleton-grid">
                    <div class="ui-skeleton ui-skeleton-card"></div>
                    <div class="ui-skeleton ui-skeleton-card"></div>
                </div>
            </div>
        `;
    }

    function renderDateStrip(days) {
        if (!days || days.length === 0) {
            return "";
        }

        const chips = days.map(function (day, index) {
            return `
                <a class="public-date-chip animate-fade-up${day.seleccionado ? " is-active" : ""}"
                    style="--stagger-index: ${index};" href="${escapeHtml(day.url)}"
                    data-public-date="${escapeHtml(day.fecha)}"${day.seleccionado ? ' aria-current="date"' : ""}>
                    <strong>${escapeHtml(day.label)}</strong>
                    <span>${day.cantidad === null || day.cantidad === undefined ? "Ver horarios" : `${escapeHtml(day.cantidad)} horario${day.cantidad === 1 ? "" : "s"}`}</span>
                </a>
            `;
        }).join("");

        return `<div class="public-date-strip stagger-list" aria-label="Días disponibles cercanos">${chips}</div>`;
    }

    function renderProfile(odontologo, fecha) {
        const photo = odontologo.foto_url
            ? `<img src="${escapeHtml(odontologo.foto_url)}" alt="" style="object-position: ${escapeHtml(odontologo.foto_object_position)};">`
            : `<span>${escapeHtml(odontologo.inicial || "O")}</span>`;

        return `
            <div class="public-selected-professional" data-public-selected-profile>
                <div class="public-selected-professional-photo">${photo}</div>
                <div><span>${escapeHtml(odontologo.especialidad)}</span><strong>${escapeHtml(odontologo.nombre)}</strong><small>${escapeHtml(fecha.display)}</small></div>
            </div>
        `;
    }

    function renderSlotCard(title, horarios, index, emptyMessage) {
        const body = horarios && horarios.length
            ? `<div class="public-slot-list public-time-grid">${horarios.map(function (slot) {
                return `<a class="public-slot-button" href="${escapeHtml(slot.url)}" data-public-slot="${escapeHtml(slot.label)}" aria-label="Seleccionar horario ${escapeHtml(slot.label)}"><span>${escapeHtml(slot.label)}</span></a>`;
            }).join("")}</div>`
            : `<p class="muted public-slot-empty">${escapeHtml(emptyMessage)}</p>`;

        return `
            <section class="public-slot-card animate-fade-up" style="--stagger-index: ${index};">
                <div class="public-slot-card-header"><div><h3>${escapeHtml(title)}</h3></div><span>${horarios.length} opciones</span></div>
                ${body}
            </section>
        `;
    }

    function renderAvailability(data) {
        setBusy(false);
        hideAction();

        if (!data.ok) {
            renderEmpty("No encontramos disponibilidad", data.mensaje || "Elegí otro profesional o fecha.");
            return;
        }

        selectProfessional(data.odontologo.id);
        const count = (data.horarios_manana || []).length + (data.horarios_tarde || []).length;
        const empty = count === 0
            ? `<div class="empty-state public-booking-empty"><h3>Sin horarios para este día</h3><p>${escapeHtml(data.mensaje)}</p></div>`
            : "";

        results.innerHTML = `
            ${renderDateStrip(data.dias_cercanos)}
            ${renderProfile(data.odontologo, data.fecha)}
            ${empty}
            <div class="public-slot-grid stagger-list">
                ${renderSlotCard("Mañana", data.horarios_manana || [], 0, "No hay horarios por la mañana para esta fecha.")}
                ${renderSlotCard("Tarde", data.horarios_tarde || [], 1, "No hay horarios por la tarde para esta fecha.")}
            </div>
        `;
    }

    async function updateAvailability() {
        if (debounceTimer) {
            clearTimeout(debounceTimer);
            debounceTimer = null;
        }

        const odontologoId = odontologoControl.value;
        const fecha = fechaControl.value;

        if (!odontologoId) {
            renderEmpty("Elegí un profesional", "Después podrás seleccionar la fecha y consultar sus horarios disponibles.");
            return;
        }

        if (!fecha) {
            renderEmpty("Elegí una fecha", "Seleccioná un día para ver los horarios disponibles.");
            return;
        }

        if (activeRequest) {
            activeRequest.abort();
        }

        const request = new AbortController();
        activeRequest = request;
        renderLoading();

        try {
            const params = new URLSearchParams({odontologo: odontologoId, fecha: fecha});
            const response = await fetch(`${availabilityUrl}?${params}`, {
                headers: {Accept: "application/json"},
                signal: request.signal,
            });
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.codigo || "availability_error");
            }
            renderAvailability(data);
        } catch (error) {
            if (error.name !== "AbortError") {
                renderError();
            }
        } finally {
            if (activeRequest === request) {
                activeRequest = null;
            }
        }
    }

    function scheduleAvailabilityUpdate() {
        if (activeRequest) {
            activeRequest.abort();
            activeRequest = null;
        }
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(updateAvailability, debounceDelay);
    }

    form.addEventListener("click", function (event) {
        const button = event.target.closest("[data-public-professional]");
        if (!button) {
            return;
        }
        selectProfessional(button.dataset.publicProfessional);
        updateAvailability();
    });

    odontologoControl.addEventListener("change", scheduleAvailabilityUpdate);
    fechaControl.addEventListener("change", scheduleAvailabilityUpdate);

    results.addEventListener("click", function (event) {
        const retry = event.target.closest("[data-public-retry]");
        if (retry) {
            updateAvailability();
            return;
        }

        const dateLink = event.target.closest("[data-public-date]");
        if (dateLink) {
            event.preventDefault();
            fechaControl.value = dateLink.dataset.publicDate;
            updateAvailability();
            return;
        }

        const slot = event.target.closest("[data-public-slot]");
        if (!slot || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
            return;
        }

        event.preventDefault();
        results.querySelectorAll("[data-public-slot]").forEach(function (option) {
            option.classList.toggle("is-selected", option === slot);
            option.setAttribute("aria-pressed", option === slot ? "true" : "false");
        });
        actionLabel.textContent = `${slot.dataset.publicSlot} seleccionado`;
        actionLink.href = slot.href;
        actionBar.hidden = false;
        actionLink.focus({preventScroll: true});
    });
})();
