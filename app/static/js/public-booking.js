(function () {
    "use strict";

    function initializePatientForm() {
        const patientForm = document.querySelector("[data-public-patient-form]");
        if (!patientForm) {
            return;
        }

        const submitButton = patientForm.querySelector("[data-public-submit]");
        patientForm.addEventListener("submit", function (event) {
            if (patientForm.dataset.submitting === "true") {
                event.preventDefault();
                return;
            }

            patientForm.dataset.submitting = "true";
            if (submitButton) {
                submitButton.disabled = true;
                submitButton.setAttribute("aria-disabled", "true");
                submitButton.setAttribute("aria-busy", "true");
                submitButton.classList.add("button-loading");
            }
        });

        window.addEventListener("pageshow", function () {
            delete patientForm.dataset.submitting;
            if (submitButton) {
                submitButton.disabled = false;
                submitButton.removeAttribute("aria-disabled");
                submitButton.removeAttribute("aria-busy");
                submitButton.classList.remove("button-loading");
            }
        });
    }

    initializePatientForm();

    const panel = document.querySelector("[data-public-availability]");
    if (!panel) {
        return;
    }

    const form = panel.querySelector("[data-public-search-form]");
    const results = panel.querySelector("[data-public-results]");
    const actionBar = panel.querySelector("[data-public-slot-action]");
    const actionLabel = panel.querySelector("[data-public-slot-label]");
    const actionControl = panel.querySelector("[data-public-slot-continue]");
    const availabilityUrl = panel.dataset.availabilityUrl;
    const typesUrl = panel.dataset.typesUrl;
    const smartScheduling = panel.dataset.smartScheduling === "true";
    const odontologoControl = form ? form.elements.namedItem("odontologo") : null;
    const tipoControl = form ? form.elements.namedItem("tipo_turno") : null;
    const fechaControl = form ? form.elements.namedItem("fecha") : null;
    const servicePicker = panel.querySelector("[data-public-service-picker]");
    const visualCalendar = panel.querySelector("[data-public-calendar]");
    const calendarDays = panel.querySelector("[data-public-calendar-days]");
    const calendarMonth = panel.querySelector("[data-public-calendar-month]");
    const calendarSelected = panel.querySelector("[data-public-calendar-selected]");
    const calendarPrevious = panel.querySelector("[data-public-calendar-prev]");
    const calendarNext = panel.querySelector("[data-public-calendar-next]");
    const summaryProfessional = panel.querySelector("[data-summary-professional]");
    const summarySpecialty = panel.querySelector("[data-summary-specialty]");
    const summaryService = panel.querySelector("[data-summary-service]");
    const summaryDate = panel.querySelector("[data-summary-date]");
    const summaryDuration = panel.querySelector("[data-summary-duration]");
    let activeRequest = null;
    let activeTypesRequest = null;
    let debounceTimer = null;
    let calendarVisibleMonth = null;
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

    function parseIsoDate(value) {
        const parts = String(value || "").split("-").map(Number);
        if (parts.length !== 3 || parts.some(Number.isNaN)) {
            return null;
        }
        const parsed = new Date(parts[0], parts[1] - 1, parts[2]);
        if (
            parsed.getFullYear() !== parts[0]
            || parsed.getMonth() !== parts[1] - 1
            || parsed.getDate() !== parts[2]
        ) {
            return null;
        }
        return parsed;
    }

    function toIsoDate(value) {
        const year = value.getFullYear();
        const month = String(value.getMonth() + 1).padStart(2, "0");
        const day = String(value.getDate()).padStart(2, "0");
        return `${year}-${month}-${day}`;
    }

    function startOfMonth(value) {
        return new Date(value.getFullYear(), value.getMonth(), 1);
    }

    function addMonths(value, amount) {
        return new Date(value.getFullYear(), value.getMonth() + amount, 1);
    }

    function sameDay(left, right) {
        return Boolean(
            left
            && right
            && left.getFullYear() === right.getFullYear()
            && left.getMonth() === right.getMonth()
            && left.getDate() === right.getDate()
        );
    }

    function formatReadableDate(value) {
        if (!value) {
            return "Pendiente";
        }
        return new Intl.DateTimeFormat("es-AR", {
            weekday: "long",
            day: "numeric",
            month: "long",
            year: "numeric",
        }).format(value);
    }

    function formatDateChip(value) {
        const weekday = new Intl.DateTimeFormat("es-AR", {weekday: "short"})
            .format(value)
            .replace(".", "");
        const month = new Intl.DateTimeFormat("es-AR", {month: "short"})
            .format(value)
            .replace(".", "");
        return {
            weekday: weekday.charAt(0).toUpperCase() + weekday.slice(1),
            date: `${String(value.getDate()).padStart(2, "0")} ${month.toUpperCase()}`,
        };
    }

    function setBusy(isBusy) {
        results.setAttribute("aria-busy", isBusy ? "true" : "false");
    }

    function updateProfessionalSummary(professional) {
        if (!professional) {
            if (summaryProfessional) {
                summaryProfessional.textContent = "Elegí un profesional";
            }
            if (summarySpecialty) {
                summarySpecialty.textContent = "Tu selección aparecerá acá";
            }
            return;
        }
        if (summaryProfessional) {
            summaryProfessional.textContent = professional.nombre || professional.name || "";
        }
        if (summarySpecialty) {
            summarySpecialty.textContent = professional.especialidad
                || professional.specialty
                || "Odontología general";
        }
        if (!smartScheduling && summaryDuration) {
            summaryDuration.textContent = "30 minutos";
        }
    }

    function updateServiceSummary(service) {
        if (summaryService) {
            summaryService.textContent = service && (service.nombre || service.name)
                ? service.nombre || service.name
                : "Pendiente";
        }
        if (summaryDuration) {
            const duration = service && (service.duracion_aproximada || service.duration);
            summaryDuration.textContent = duration
                ? `${duration} minutos`
                : smartScheduling ? "Pendiente" : "30 minutos";
        }
    }

    function updateDateSummary() {
        const selected = parseIsoDate(fechaControl.value);
        if (summaryDate) {
            summaryDate.textContent = formatReadableDate(selected);
        }
    }

    function hideAction() {
        if (actionBar) {
            actionBar.hidden = true;
        }
        if (actionLabel) {
            actionLabel.textContent = "";
        }
        if (actionControl) {
            actionControl.disabled = true;
            actionControl.setAttribute("aria-disabled", "true");
            delete actionControl.dataset.href;
        }
    }

    function selectProfessional(id, professionalData) {
        odontologoControl.value = String(id || "");
        let selectedButton = null;
        form.querySelectorAll("[data-public-professional]").forEach(function (button) {
            const selected = button.dataset.publicProfessional === String(id);
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-pressed", selected ? "true" : "false");
            if (selected) {
                selectedButton = button;
            }
        });
        if (professionalData) {
            updateProfessionalSummary(professionalData);
        } else if (selectedButton) {
            updateProfessionalSummary({
                name: selectedButton.dataset.professionalName,
                specialty: selectedButton.dataset.professionalSpecialty,
            });
        } else {
            updateProfessionalSummary(null);
        }
        hideAction();
    }

    function selectService(id, serviceData) {
        if (!tipoControl) {
            return;
        }
        tipoControl.value = String(id || "");
        let selectedButton = null;
        form.querySelectorAll("[data-public-service]").forEach(function (button) {
            const selected = button.dataset.publicService === String(id);
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-checked", selected ? "true" : "false");
            if (selected) {
                selectedButton = button;
            }
        });
        if (serviceData) {
            updateServiceSummary(serviceData);
        } else if (selectedButton) {
            updateServiceSummary({
                name: selectedButton.dataset.serviceName,
                duration: selectedButton.dataset.serviceDuration,
            });
        } else {
            updateServiceSummary(null);
        }
        hideAction();
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
                <button class="button public-outline-button" type="button" data-public-retry>Reintentar</button>
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
                    data-public-service="${escapeHtml(tipo.tipo_turno_id)}"
                    data-service-name="${escapeHtml(tipo.nombre)}"
                    data-service-duration="${escapeHtml(tipo.duracion_aproximada)}"
                    aria-checked="false">
                    <span class="public-service-option-icon" aria-hidden="true">${escapeHtml(initial)}</span>
                    <span class="public-service-option-copy">
                        <strong>${escapeHtml(tipo.nombre)}</strong>
                        ${tipo.descripcion ? `<small>${escapeHtml(tipo.descripcion)}</small>` : ""}
                        <span>Aproximadamente ${escapeHtml(tipo.duracion_aproximada)} min</span>
                    </span>
                    <span class="public-service-option-check" aria-hidden="true">✓</span>
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
            const date = parseIsoDate(day.fecha);
            const label = date ? formatDateChip(date) : {weekday: "Día", date: day.label};
            return `
                <a class="public-date-chip animate-fade-up${day.seleccionado ? " is-active" : ""}"
                    style="--stagger-index: ${index};" href="${escapeHtml(day.url)}"
                    data-public-date="${escapeHtml(day.fecha)}"${day.seleccionado ? ' aria-current="date"' : ""}>
                    <small>${escapeHtml(label.weekday)}</small>
                    <strong>${escapeHtml(label.date)}</strong>
                    <span>Ver horarios</span>
                </a>
            `;
        }).join("");
        return `<div class="public-date-strip stagger-list" aria-label="Días disponibles cercanos">${chips}</div>`;
    }

    function parseSlotHour(slot) {
        const hour = Number.parseInt(String(slot.label || "").split(":")[0], 10);
        return Number.isNaN(hour) ? 0 : hour;
    }

    function renderSlotCard(title, slots, index) {
        const horarios = slots || [];
        const body = horarios.length
            ? `<div class="public-slot-list public-time-grid">${horarios.map(function (slot) {
                return `<a class="public-slot-button" href="${escapeHtml(slot.url)}" data-public-slot="${escapeHtml(slot.label)}" aria-label="Seleccionar horario ${escapeHtml(slot.label)}" aria-pressed="false"><span>${escapeHtml(slot.label)}</span></a>`;
            }).join("")}</div>`
            : '<p class="muted public-slot-empty">No hay opciones en esta franja.</p>';
        const countLabel = horarios.length === 1 ? "1 opción" : `${horarios.length} opciones`;
        return `
            <section class="public-slot-card public-time-period animate-fade-up" style="--stagger-index: ${index};" data-public-slot-card data-slot-card-title="${escapeHtml(title)}">
                <div class="public-slot-card-header"><div><h3>${escapeHtml(title)}</h3></div><span>${countLabel}</span></div>
                ${body}
            </section>
        `;
    }

    function renderSlotGroups(slots, prefix) {
        const groups = [
            {title: prefix ? `${prefix} de mañana` : "Mañana", slots: []},
            {title: prefix ? `${prefix} de tarde` : "Tarde", slots: []},
            {title: prefix ? `${prefix} de tarde / noche` : "Tarde / noche", slots: []},
        ];
        (slots || []).forEach(function (slot) {
            const hour = parseSlotHour(slot);
            const index = hour < 13 ? 0 : hour < 17 ? 1 : 2;
            groups[index].slots.push(slot);
        });
        return groups
            .filter(function (group) { return group.slots.length > 0; })
            .map(function (group, index) { return renderSlotCard(group.title, group.slots, index); })
            .join("");
    }

    function normalizeRenderedSlotGroups(container) {
        container.querySelectorAll(".public-slot-grid").forEach(function (grid) {
            if (grid.dataset.slotGroupsNormalized === "true") {
                return;
            }
            const cards = Array.from(grid.querySelectorAll("[data-public-slot-card]"));
            const slots = Array.from(grid.querySelectorAll("[data-public-slot]")).map(function (slot) {
                return {label: slot.dataset.publicSlot, url: slot.getAttribute("href")};
            });
            if (!cards.length || !slots.length) {
                return;
            }
            const isAlternative = cards.some(function (card) {
                return String(card.dataset.slotCardTitle || "").toLowerCase().includes("otras");
            });
            grid.innerHTML = renderSlotGroups(slots, isAlternative ? "Otras opciones" : "");
            grid.dataset.slotGroupsNormalized = "true";
        });
    }

    function renderSelectedDate(fecha) {
        return `
            <p class="public-selected-date-label">
                <strong>${escapeHtml(fecha.display)}</strong>
            </p>
        `;
    }

    function renderAvailabilityNote() {
        return `
            <div class="public-availability-note">
                <span aria-hidden="true">i</span>
                <p>Los horarios mostrados son sugerencias basadas en la disponibilidad. También podés consultar otras opciones.</p>
            </div>
        `;
    }

    function renderSmartAvailability(data) {
        const recommended = data.horarios_recomendados || {manana: [], tarde: []};
        const alternatives = data.horarios_alternativos || {manana: [], tarde: []};
        const recommendedSlots = recommended.manana.concat(recommended.tarde);
        const alternativeSlots = alternatives.manana.concat(alternatives.tarde);
        const total = recommendedSlots.length + alternativeSlots.length;
        const empty = total === 0
            ? `<div class="empty-state public-booking-empty"><h3>Sin horarios para este día</h3><p>${escapeHtml(data.mensaje)}</p></div>`
            : "";
        const recommendedHtml = recommendedSlots.length ? `
            <div class="public-smart-schedule-intro">
                <div><p class="eyebrow">Agenda inteligente</p><h3>Horarios recomendados</h3></div>
                <p>Primero aparecen las opciones sugeridas. También podés consultar otras alternativas.</p>
            </div>
            <div class="public-slot-grid stagger-list" data-slot-groups-normalized="true">
                ${renderSlotGroups(recommendedSlots, "")}
            </div>
        ` : "";
        const alternativesHtml = alternativeSlots.length ? `
            <details class="public-more-slots" data-public-more-slots>
                <summary aria-expanded="false"><span>Ver más horarios</span><span aria-hidden="true">⌄</span></summary>
                <div class="public-slot-grid stagger-list" data-slot-groups-normalized="true">
                    ${renderSlotGroups(alternativeSlots, "Otras opciones")}
                </div>
            </details>
        ` : "";
        results.innerHTML = `
            ${renderDateStrip(data.dias_cercanos)}
            ${renderSelectedDate(data.fecha)}
            ${empty}${recommendedHtml}${alternativesHtml}
            ${total ? renderAvailabilityNote() : ""}
        `;
    }

    function renderLegacyAvailability(data) {
        const slots = (data.horarios_manana || []).concat(data.horarios_tarde || []);
        const empty = slots.length === 0
            ? `<div class="empty-state public-booking-empty"><h3>Sin horarios para este día</h3><p>${escapeHtml(data.mensaje)}</p></div>`
            : "";
        results.innerHTML = `
            ${renderDateStrip(data.dias_cercanos)}
            ${renderSelectedDate(data.fecha)}
            ${empty}
            ${slots.length ? `<div class="public-slot-grid stagger-list" data-slot-groups-normalized="true">${renderSlotGroups(slots, "")}</div>` : ""}
            ${slots.length ? renderAvailabilityNote() : ""}
        `;
    }

    function renderAvailability(data) {
        setBusy(false);
        hideAction();
        if (!data.ok) {
            renderEmpty("No encontramos disponibilidad", data.mensaje || "Elegí otra opción.");
            return;
        }
        selectProfessional(data.odontologo.id, data.odontologo);
        if (smartScheduling) {
            selectService(data.tipo_turno.id, data.tipo_turno);
            renderSmartAvailability(data);
        } else {
            renderLegacyAvailability(data);
        }
        if (data.fecha && data.fecha.iso) {
            fechaControl.value = data.fecha.iso;
            updateDateSummary();
            syncCalendarFromControl();
        }
    }

    async function updateTypes(focusFirst) {
        if (!smartScheduling || !typesUrl || !servicePicker || !tipoControl) {
            return;
        }
        selectService("");
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
        hideAction();
        if (activeRequest) {
            activeRequest.abort();
            activeRequest = null;
        }
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(updateAvailability, debounceDelay);
    }

    function renderCalendar() {
        if (!visualCalendar || !calendarDays || !calendarMonth || !calendarVisibleMonth) {
            return;
        }
        const minimum = parseIsoDate(fechaControl.min);
        const maximum = parseIsoDate(fechaControl.max);
        const selected = parseIsoDate(fechaControl.value);
        const monthStart = startOfMonth(calendarVisibleMonth);
        const offset = (monthStart.getDay() + 6) % 7;
        const firstCell = new Date(monthStart);
        firstCell.setDate(firstCell.getDate() - offset);
        let firstEnabledAssigned = false;
        const cells = [];

        for (let index = 0; index < 42; index += 1) {
            const date = new Date(firstCell);
            date.setDate(firstCell.getDate() + index);
            const iso = toIsoDate(date);
            const outside = date.getMonth() !== monthStart.getMonth();
            const disabled = Boolean((minimum && date < minimum) || (maximum && date > maximum));
            const isSelected = sameDay(date, selected);
            const tabIndex = !disabled && (isSelected || (!selected && !firstEnabledAssigned)) ? 0 : -1;
            if (tabIndex === 0) {
                firstEnabledAssigned = true;
            }
            cells.push(`
                <button class="public-calendar-day${outside ? " is-outside" : ""}${isSelected ? " is-selected" : ""}"
                    type="button" role="gridcell" data-public-calendar-day="${iso}"
                    aria-label="${escapeHtml(formatReadableDate(date))}"
                    aria-selected="${isSelected ? "true" : "false"}"
                    tabindex="${tabIndex}"${disabled ? " disabled" : ""}>${date.getDate()}</button>
            `);
        }

        calendarMonth.textContent = new Intl.DateTimeFormat("es-AR", {
            month: "long",
            year: "numeric",
        }).format(monthStart);
        calendarDays.innerHTML = cells.join("");
        if (calendarSelected) {
            calendarSelected.textContent = selected
                ? formatReadableDate(selected)
                : "Elegí una fecha";
        }
        if (calendarPrevious && minimum) {
            calendarPrevious.disabled = monthStart <= startOfMonth(minimum);
        }
        if (calendarNext && maximum) {
            calendarNext.disabled = monthStart >= startOfMonth(maximum);
        }
    }

    function syncCalendarFromControl() {
        const selected = parseIsoDate(fechaControl.value);
        const minimum = parseIsoDate(fechaControl.min);
        if (!calendarVisibleMonth) {
            calendarVisibleMonth = startOfMonth(selected || minimum || new Date());
        } else if (selected) {
            calendarVisibleMonth = startOfMonth(selected);
        }
        updateDateSummary();
        renderCalendar();
    }

    function initializeCalendar() {
        if (!visualCalendar || !calendarDays) {
            return;
        }
        visualCalendar.hidden = false;
        syncCalendarFromControl();

        calendarPrevious.addEventListener("click", function () {
            calendarVisibleMonth = addMonths(calendarVisibleMonth, -1);
            renderCalendar();
        });
        calendarNext.addEventListener("click", function () {
            calendarVisibleMonth = addMonths(calendarVisibleMonth, 1);
            renderCalendar();
        });
        calendarDays.addEventListener("click", function (event) {
            const day = event.target.closest("[data-public-calendar-day]");
            if (!day || day.disabled) {
                return;
            }
            fechaControl.value = day.dataset.publicCalendarDay;
            syncCalendarFromControl();
            updateAvailability();
        });
        calendarDays.addEventListener("keydown", function (event) {
            const day = event.target.closest("[data-public-calendar-day]");
            if (!day || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
                return;
            }
            event.preventDefault();
            const enabled = Array.from(calendarDays.querySelectorAll("[data-public-calendar-day]:not(:disabled)"));
            const currentIndex = enabled.indexOf(day);
            const movement = {
                ArrowLeft: -1,
                ArrowRight: 1,
                ArrowUp: -7,
                ArrowDown: 7,
                Home: -currentIndex,
                End: enabled.length - currentIndex - 1,
            }[event.key];
            const target = enabled[Math.max(0, Math.min(enabled.length - 1, currentIndex + movement))];
            if (target) {
                enabled.forEach(function (button) { button.tabIndex = -1; });
                target.tabIndex = 0;
                target.focus();
            }
        });
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
        selectProfessional(odontologoControl.value);
        if (smartScheduling) {
            updateTypes(false);
        } else {
            scheduleAvailabilityUpdate();
        }
    });
    if (tipoControl) {
        tipoControl.addEventListener("change", function () {
            selectService(tipoControl.value);
            scheduleAvailabilityUpdate();
        });
    }
    fechaControl.addEventListener("change", function () {
        syncCalendarFromControl();
        scheduleAvailabilityUpdate();
    });

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
            syncCalendarFromControl();
            updateAvailability();
            return;
        }
        const slot = event.target.closest("[data-public-slot]");
        if (!slot || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
            return;
        }
        event.preventDefault();
        results.querySelectorAll("[data-public-slot]").forEach(function (option) {
            const selected = option === slot;
            option.classList.toggle("is-selected", selected);
            option.setAttribute("aria-pressed", selected ? "true" : "false");
        });
        if (actionBar && actionLabel && actionControl) {
            actionLabel.textContent = slot.dataset.publicSlot;
            actionControl.dataset.href = slot.href;
            actionControl.disabled = false;
            actionControl.setAttribute("aria-disabled", "false");
            actionBar.hidden = false;
            actionControl.focus({preventScroll: true});
        }
    });

    if (actionControl) {
        actionControl.addEventListener("click", function () {
            if (!actionControl.disabled && actionControl.dataset.href) {
                window.location.assign(actionControl.dataset.href);
            }
        });
    }

    const selectedProfessional = form.querySelector("[data-public-professional].is-selected");
    const selectedService = form.querySelector("[data-public-service].is-selected");
    if (selectedProfessional) {
        selectProfessional(selectedProfessional.dataset.publicProfessional);
    }
    if (selectedService) {
        selectService(selectedService.dataset.publicService);
    }
    initializeCalendar();
    normalizeRenderedSlotGroups(results);
    hideAction();
})();
