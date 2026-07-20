(function () {
    "use strict";

    const panel = document.querySelector("[data-public-availability]");
    if (!panel) {
        return;
    }

    const form = panel.querySelector("[data-public-search-form]");
    const results = panel.querySelector("[data-public-results]");
    const actionBar = panel.querySelector("[data-public-slot-action]");
    const actionLabel = panel.querySelector("[data-public-slot-label]");
    const actionLink = panel.querySelector("[data-public-slot-continue]");
    const availabilityUrl = panel.dataset.availabilityUrl;
    const typesUrl = panel.dataset.typesUrl;
    const smartScheduling = panel.dataset.smartScheduling === "true";
    const odontologoControl = form ? form.elements.namedItem("odontologo") : null;
    const tipoControl = form ? form.elements.namedItem("tipo_turno") : null;
    const fechaControl = form ? form.elements.namedItem("fecha") : null;
    const servicePicker = panel.querySelector("[data-public-service-picker]");
    let activeRequest = null;
    let activeTypesRequest = null;
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
        if (!actionBar || !actionLabel || !actionLink) {
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

    function selectService(id) {
        if (!tipoControl) {
            return;
        }
        tipoControl.value = String(id || "");
        form.querySelectorAll("[data-public-service]").forEach(function (button) {
            const selected = button.dataset.publicService === String(id);
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-checked", selected ? "true" : "false");
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

    function renderTypesLoading() {
        if (!servicePicker) {
            return;
        }
        servicePicker.setAttribute("aria-busy", "true");
        servicePicker.innerHTML = `
            <div class="public-service-loading" aria-label="Cargando motivos de visita">
                <span class="ui-skeleton ui-skeleton-line"></span>
                <span class="ui-skeleton ui-skeleton-line"></span>
            </div>
        `;
    }

    function renderTypes(data, focusFirst) {
        if (!servicePicker || !tipoControl) {
            return;
        }
        servicePicker.setAttribute("aria-busy", "false");
        if (!data.ok || !data.tipos || data.tipos.length === 0) {
            servicePicker.innerHTML = `
                <p class="muted public-service-empty">${escapeHtml(data.mensaje)}</p>
            `;
            renderEmpty("Sin motivos disponibles", data.mensaje);
            return;
        }

        servicePicker.innerHTML = data.tipos.map(function (tipo) {
            const initial = String(tipo.nombre || "M").trim().charAt(0).toUpperCase();
            return `
                <button class="public-service-option" type="button" role="radio"
                    data-public-service="${escapeHtml(tipo.tipo_turno_id)}" aria-checked="false">
                    <span class="public-service-option-icon" aria-hidden="true">${escapeHtml(initial)}</span>
                    <span class="public-service-option-copy">
                        <strong>${escapeHtml(tipo.nombre)}</strong>
                        ${tipo.descripcion ? `<small>${escapeHtml(tipo.descripcion)}</small>` : ""}
                        <span>Aproximadamente ${escapeHtml(tipo.duracion_aproximada)} min</span>
                    </span>
                    <span class="public-service-option-arrow" aria-hidden="true">›</span>
                </button>
            `;
        }).join("");

        tipoControl.innerHTML = '<option value="">Seleccionar motivo</option>' + data.tipos
            .map(function (tipo) {
                return `<option value="${escapeHtml(tipo.tipo_turno_id)}">${escapeHtml(tipo.nombre)}</option>`;
            })
            .join("");
        if (focusFirst) {
            const first = servicePicker.querySelector("[data-public-service]");
            if (first) {
                first.focus({preventScroll: true});
            }
        }
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
                    <strong>${escapeHtml(day.label)}</strong><span>Ver horarios</span>
                </a>
            `;
        }).join("");
        return `<div class="public-date-strip stagger-list" aria-label="Días disponibles cercanos">${chips}</div>`;
    }

    function renderProfile(odontologo, fecha, tipo) {
        const photo = odontologo.foto_url
            ? `<img src="${escapeHtml(odontologo.foto_url)}" alt="" style="object-position: ${escapeHtml(odontologo.foto_object_position)};">`
            : `<span>${escapeHtml(odontologo.inicial || "O")}</span>`;
        const detail = tipo
            ? `${escapeHtml(tipo.nombre)} · ${escapeHtml(tipo.duracion_aproximada)} min · ${escapeHtml(fecha.display)}`
            : escapeHtml(fecha.display);
        return `
            <div class="public-selected-professional" data-public-selected-profile>
                <div class="public-selected-professional-photo">${photo}</div>
                <div><span>${escapeHtml(odontologo.especialidad)}</span><strong>${escapeHtml(odontologo.nombre)}</strong><small>${detail}</small></div>
            </div>
        `;
    }

    function renderSlotCard(title, slots, index) {
        const horarios = slots || [];
        const body = horarios.length
            ? `<div class="public-slot-list public-time-grid">${horarios.map(function (slot) {
                return `<a class="public-slot-button" href="${escapeHtml(slot.url)}" data-public-slot="${escapeHtml(slot.label)}" aria-label="Seleccionar horario ${escapeHtml(slot.label)}"><span>${escapeHtml(slot.label)}</span></a>`;
            }).join("")}</div>`
            : '<p class="muted public-slot-empty">No hay opciones en esta franja.</p>';
        return `
            <section class="public-slot-card animate-fade-up" style="--stagger-index: ${index};">
                <div class="public-slot-card-header"><div><h3>${escapeHtml(title)}</h3></div><span>${horarios.length} opciones</span></div>
                ${body}
            </section>
        `;
    }

    function renderSmartAvailability(data) {
        const recommended = data.horarios_recomendados || {manana: [], tarde: []};
        const alternatives = data.horarios_alternativos || {manana: [], tarde: []};
        const total = recommended.manana.length + recommended.tarde.length
            + alternatives.manana.length + alternatives.tarde.length;
        const empty = total === 0
            ? `<div class="empty-state public-booking-empty"><h3>Sin horarios para este día</h3><p>${escapeHtml(data.mensaje)}</p></div>`
            : "";
        const recommendedHtml = total ? `
            <div class="public-smart-schedule-intro">
                <div><p class="eyebrow">Agenda inteligente</p><h3>Horarios recomendados</h3></div>
                <p>Te mostramos primero los horarios que mejor encajan con la disponibilidad. También podés consultar otras opciones.</p>
            </div>
            <div class="public-slot-grid stagger-list">
                ${renderSlotCard("Mañana", recommended.manana, 0)}
                ${renderSlotCard("Tarde", recommended.tarde, 1)}
            </div>
        ` : "";
        const alternativesCount = alternatives.manana.length + alternatives.tarde.length;
        const alternativesHtml = alternativesCount ? `
            <details class="public-more-slots" data-public-more-slots>
                <summary aria-expanded="false"><span>Ver más horarios</span><span aria-hidden="true">⌄</span></summary>
                <div class="public-slot-grid stagger-list">
                    ${renderSlotCard("Otras opciones de mañana", alternatives.manana, 0)}
                    ${renderSlotCard("Otras opciones de tarde", alternatives.tarde, 1)}
                </div>
            </details>
        ` : "";
        results.innerHTML = `
            ${renderDateStrip(data.dias_cercanos)}
            ${renderProfile(data.odontologo, data.fecha, data.tipo_turno)}
            ${empty}${recommendedHtml}${alternativesHtml}
        `;
    }

    function renderLegacyAvailability(data) {
        const count = (data.horarios_manana || []).length + (data.horarios_tarde || []).length;
        const empty = count === 0
            ? `<div class="empty-state public-booking-empty"><h3>Sin horarios para este día</h3><p>${escapeHtml(data.mensaje)}</p></div>`
            : "";
        results.innerHTML = `
            ${renderDateStrip(data.dias_cercanos)}
            ${renderProfile(data.odontologo, data.fecha, null)}
            ${empty}
            <div class="public-slot-grid stagger-list">
                ${renderSlotCard("Mañana", data.horarios_manana || [], 0)}
                ${renderSlotCard("Tarde", data.horarios_tarde || [], 1)}
            </div>
        `;
    }

    function renderAvailability(data) {
        setBusy(false);
        hideAction();
        if (!data.ok) {
            renderEmpty("No encontramos disponibilidad", data.mensaje || "Elegí otra opción.");
            return;
        }
        selectProfessional(data.odontologo.id);
        if (smartScheduling) {
            selectService(data.tipo_turno.id);
            renderSmartAvailability(data);
        } else {
            renderLegacyAvailability(data);
        }
    }

    async function updateTypes(focusFirst) {
        if (!smartScheduling || !typesUrl || !servicePicker || !tipoControl) {
            return;
        }
        selectService("");
        hideAction();
        if (!odontologoControl.value) {
            servicePicker.innerHTML = '<p class="muted public-service-empty">Primero elegí un profesional.</p>';
            return;
        }
        if (activeTypesRequest) {
            activeTypesRequest.abort();
        }
        const request = new AbortController();
        activeTypesRequest = request;
        renderTypesLoading();
        renderEmpty("Elegí el motivo de la visita", "Después podrás consultar los horarios disponibles.");
        try {
            const params = new URLSearchParams({odontologo: odontologoControl.value});
            const response = await fetch(`${typesUrl}?${params}`, {
                headers: {Accept: "application/json"},
                signal: request.signal,
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.codigo || "types_error");
            }
            renderTypes(data, focusFirst);
        } catch (error) {
            if (error.name !== "AbortError") {
                servicePicker.setAttribute("aria-busy", "false");
                servicePicker.innerHTML = '<p class="field-error" role="alert">No pudimos cargar los motivos. Intentá nuevamente.</p>';
            }
        } finally {
            if (activeTypesRequest === request) {
                activeTypesRequest = null;
            }
        }
    }

    async function updateAvailability() {
        clearTimeout(debounceTimer);
        debounceTimer = null;
        const odontologoId = odontologoControl.value;
        const fecha = fechaControl.value;
        const tipoTurnoId = tipoControl ? tipoControl.value : "";

        if (!odontologoId) {
            renderEmpty("Elegí un profesional", "Después podrás consultar sus horarios disponibles.");
            return;
        }
        if (smartScheduling && !tipoTurnoId) {
            renderEmpty("Elegí el motivo de la visita", "La duración se calcula según el profesional.");
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
            if (smartScheduling) {
                params.set("tipo_turno", tipoTurnoId);
            }
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
        const professional = event.target.closest("[data-public-professional]");
        if (professional) {
            selectProfessional(professional.dataset.publicProfessional);
            if (smartScheduling) {
                updateTypes(true);
            } else {
                updateAvailability();
            }
            return;
        }
        const service = event.target.closest("[data-public-service]");
        if (service) {
            selectService(service.dataset.publicService);
            updateAvailability();
        }
    });

    odontologoControl.addEventListener("change", function () {
        if (smartScheduling) {
            updateTypes(false);
        } else {
            scheduleAvailabilityUpdate();
        }
    });
    if (tipoControl) {
        tipoControl.addEventListener("change", scheduleAvailabilityUpdate);
    }
    fechaControl.addEventListener("change", scheduleAvailabilityUpdate);

    results.addEventListener("toggle", function (event) {
        if (event.target.matches("[data-public-more-slots]")) {
            const summary = event.target.querySelector("summary");
            summary.setAttribute("aria-expanded", event.target.open ? "true" : "false");
        }
    }, true);

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
            option.setAttribute("aria-current", option === slot ? "true" : "false");
        });
        if (actionBar && actionLabel && actionLink) {
            actionLabel.textContent = `${slot.dataset.publicSlot} seleccionado`;
            actionLink.href = slot.href;
            actionBar.hidden = false;
            actionLink.focus({preventScroll: true});
        }
    });
})();
