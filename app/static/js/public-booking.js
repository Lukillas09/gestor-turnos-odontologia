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
    const calendarWindow = panel.querySelector("[data-public-calendar-window]");
    const calendarToggle = panel.querySelector("[data-public-calendar-toggle]");
    const calendarPrevious = panel.querySelector("[data-public-calendar-prev]");
    const calendarNext = panel.querySelector("[data-public-calendar-next]");
    const summaryProfessional = panel.querySelector("[data-summary-professional]");
    const summaryService = panel.querySelector("[data-summary-service]");
    const summaryDate = panel.querySelector("[data-summary-date]");
    const summaryDuration = panel.querySelector("[data-summary-duration]");
    const summaryCompact = panel.querySelector("[data-summary-compact]");
    const emptyValue = "Sin elegir";
    let preselectedType = panel.dataset.preselectedType || "";
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

    function capitalize(value) {
        return value.charAt(0).toUpperCase() + value.slice(1);
    }

    function formatDayMonth(value) {
        const weekday = new Intl.DateTimeFormat("es-AR", {weekday: "long"}).format(value);
        const month = new Intl.DateTimeFormat("es-AR", {month: "long"}).format(value);
        return capitalize(`${weekday} ${value.getDate()} de ${month}`);
    }

    function formatDateChip(value) {
        const weekday = new Intl.DateTimeFormat("es-AR", {weekday: "short"})
            .format(value)
            .replace(".", "");
        return {weekday: capitalize(weekday), day: String(value.getDate())};
    }

    function setBusy(isBusy) {
        results.setAttribute("aria-busy", isBusy ? "true" : "false");
    }

    function updateProfessionalSummary(professional) {
        if (summaryProfessional) {
            summaryProfessional.textContent = professional
                ? professional.nombre || professional.name || emptyValue
                : emptyValue;
        }
        if (professional && !smartScheduling && summaryDuration) {
            summaryDuration.textContent = "30 minutos";
        }
    }

    function updateServiceSummary(service) {
        if (summaryService) {
            summaryService.textContent = service && (service.nombre || service.name)
                ? service.nombre || service.name
                : emptyValue;
        }
        if (summaryDuration) {
            const duration = service && (service.duracion_aproximada || service.duration);
            summaryDuration.textContent = duration
                ? `${duration} minutos`
                : smartScheduling ? emptyValue : "30 minutos";
        }
    }

    function updateDateSummary() {
        const selected = parseIsoDate(fechaControl.value);
        if (summaryDate) {
            summaryDate.textContent = selected ? formatDayMonth(selected) : emptyValue;
        }
    }

    function updateCompactSummary() {
        if (!summaryCompact) {
            return;
        }
        const hasSlot = actionControl && !actionControl.disabled && actionLabel;
        summaryCompact.textContent = hasSlot && summaryDate
            ? `${summaryDate.textContent} · ${actionLabel.textContent}`
            : "Elegí un horario";
    }

    function hideAction() {
        if (actionBar) {
            actionBar.classList.add("is-empty");
        }
        if (actionLabel) {
            actionLabel.textContent = emptyValue;
        }
        if (actionControl) {
            actionControl.disabled = true;
            actionControl.setAttribute("aria-disabled", "true");
            delete actionControl.dataset.href;
        }
        updateCompactSummary();
    }

    function syncRovingTabindex(group) {
        if (!group) {
            return;
        }
        const radios = Array.from(group.querySelectorAll("[role='radio']"));
        const checked = radios.find(function (radio) {
            return radio.getAttribute("aria-checked") === "true";
        });
        radios.forEach(function (radio) {
            radio.tabIndex = radio === (checked || radios[0]) ? 0 : -1;
        });
    }

    function selectProfessional(id, professionalData) {
        odontologoControl.value = String(id || "");
        let selectedButton = null;
        form.querySelectorAll("[data-public-professional]").forEach(function (button) {
            const selected = button.dataset.publicProfessional === String(id);
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-checked", selected ? "true" : "false");
            if (selected) {
                selectedButton = button;
            }
        });
        syncRovingTabindex(form.querySelector("[data-professional-picker]"));
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
        syncRovingTabindex(servicePicker);
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
            <div class="public-booking-skeleton" role="status" aria-label="Buscando horarios disponibles">
                <div class="ui-skeleton ui-skeleton-line ui-skeleton-line-short"></div>
                <div class="public-booking-skeleton-grid">
                    ${'<div class="ui-skeleton public-booking-skeleton-slot"></div>'.repeat(6)}
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
            return `
                <button class="public-service-option" type="button" role="radio"
                    data-public-service="${escapeHtml(tipo.tipo_turno_id)}"
                    data-service-name="${escapeHtml(tipo.nombre)}"
                    data-service-duration="${escapeHtml(tipo.duracion_aproximada)}"
                    aria-checked="false">
                    <strong>${escapeHtml(tipo.nombre)}</strong>
                    <span>${escapeHtml(tipo.duracion_aproximada)} min</span>
                </button>
            `;
        }).join("");

        tipoControl.innerHTML = '<option value="">Seleccionar motivo</option>' + data.tipos
            .map(function (tipo) {
                return `<option value="${escapeHtml(tipo.tipo_turno_id)}">${escapeHtml(tipo.nombre)}</option>`;
            })
            .join("");
        syncRovingTabindex(servicePicker);

        const preselected = preselectedType
            ? servicePicker.querySelector(`[data-public-service="${CSS.escape(preselectedType)}"]`)
            : null;
        if (preselected) {
            preselectedType = "";
            selectService(preselected.dataset.publicService);
            updateAvailability();
            return;
        }
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
        const chips = days.map(function (day) {
            const date = parseIsoDate(day.fecha);
            const label = date ? formatDateChip(date) : {weekday: "", day: day.label};
            return `
                <a class="public-date-chip${day.seleccionado ? " is-active" : ""}"
                    href="${escapeHtml(day.url)}" data-public-date="${escapeHtml(day.fecha)}"
                    aria-label="${escapeHtml(date ? formatDayMonth(date) : day.label)}"${day.seleccionado ? ' aria-current="date"' : ""}>
                    <small>${escapeHtml(label.weekday)}</small>
                    <strong>${escapeHtml(label.day)}</strong>
                </a>
            `;
        }).join("");
        return `<div class="public-date-strip" aria-label="Días cercanos">${chips}</div>`;
    }

    function renderSlotGroup(title, group, slots) {
        if (!slots || slots.length === 0) {
            return "";
        }
        const buttons = slots.map(function (slot) {
            return `<a class="public-slot-button" role="button" aria-pressed="false" href="${escapeHtml(slot.url)}" data-public-slot="${escapeHtml(slot.label)}" aria-label="Seleccionar horario ${escapeHtml(slot.label)}">${escapeHtml(slot.label)}</a>`;
        }).join("");
        return `
            <section class="public-slot-group${group === "recomendados" ? " is-recommended" : ""}" data-public-slot-card data-slot-group="${group}">
                <h3>${escapeHtml(title)}</h3>
                <div class="public-time-grid">${buttons}</div>
            </section>
        `;
    }

    function renderSelectedDate(fecha) {
        const date = fecha ? parseIsoDate(fecha.iso) : null;
        const label = date ? formatDayMonth(date) : fecha && fecha.display;
        return `<p class="public-selected-date-label">${escapeHtml(label)}</p>`;
    }

    function renderNoSlots(message) {
        return `<div class="empty-state public-booking-empty"><h3>Sin horarios para este día</h3><p>${escapeHtml(message || "Probá con otra fecha disponible.")}</p></div>`;
    }

    function renderSmartAvailability(data) {
        const recommended = data.horarios_recomendados || {manana: [], tarde: []};
        const alternatives = data.horarios_alternativos || {manana: [], tarde: []};
        const groups = renderSlotGroup("Recomendados", "recomendados", recommended.manana.concat(recommended.tarde))
            + renderSlotGroup("Mañana", "manana", alternatives.manana)
            + renderSlotGroup("Tarde", "tarde", alternatives.tarde);
        results.innerHTML = `
            ${renderDateStrip(data.dias_cercanos)}
            ${renderSelectedDate(data.fecha)}
            ${groups || renderNoSlots(data.mensaje)}
        `;
    }

    function renderLegacyAvailability(data) {
        const groups = renderSlotGroup("Mañana", "manana", data.horarios_manana)
            + renderSlotGroup("Tarde", "tarde", data.horarios_tarde);
        results.innerHTML = `
            ${renderDateStrip(data.dias_cercanos)}
            ${renderSelectedDate(data.fecha)}
            ${groups || renderNoSlots(data.mensaje)}
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
                    type="button" data-public-calendar-day="${iso}"
                    aria-label="${escapeHtml(formatReadableDate(date))}${disabled ? ", fuera de la ventana de reserva" : ""}"
                    aria-pressed="${isSelected ? "true" : "false"}"
                    tabindex="${tabIndex}"${disabled ? " disabled" : ""}>${date.getDate()}</button>
            `);
        }

        calendarMonth.textContent = new Intl.DateTimeFormat("es-AR", {
            month: "long",
            year: "numeric",
        }).format(monthStart);
        calendarDays.innerHTML = cells.join("");
        if (calendarWindow && maximum) {
            calendarWindow.textContent = `Podés reservar hasta el ${formatDayMonth(maximum).toLowerCase()}.`;
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

        if (calendarToggle) {
            calendarToggle.hidden = false;
            calendarToggle.addEventListener("click", function () {
                const open = !visualCalendar.classList.contains("is-open");
                visualCalendar.classList.toggle("is-open", open);
                calendarToggle.setAttribute("aria-expanded", open ? "true" : "false");
                calendarToggle.querySelector("span").textContent = open ? "Ocultar calendario" : "Ver calendario";
            });
        }

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
            fechaControl.dispatchEvent(new Event("change", {bubbles: true}));
            if (calendarToggle && visualCalendar.classList.contains("is-open")) {
                calendarToggle.click();
            }
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

    form.addEventListener("keydown", function (event) {
        const radio = event.target.closest("[role='radio']");
        const group = radio ? radio.closest("[role='radiogroup']") : null;
        const steps = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1};
        if (!group || !(event.key in steps)) {
            return;
        }
        event.preventDefault();
        const radios = Array.from(group.querySelectorAll("[role='radio']"));
        const next = radios[(radios.indexOf(radio) + steps[event.key] + radios.length) % radios.length];
        next.focus();
        next.click();
    });

    form.addEventListener("click", function (event) {
        const professional = event.target.closest("[data-public-professional]");
        if (professional) {
            selectProfessional(professional.dataset.publicProfessional);
            if (smartScheduling) {
                // Only pointer clicks move focus forward; arrow keys keep it in the radiogroup.
                updateTypes(event.detail > 0);
            } else {
                updateAvailability();
            }
            return;
        }
        const service = event.target.closest("[data-public-service]");
        if (service) {
            preselectedType = "";
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

    results.addEventListener("keydown", function (event) {
        const slot = event.target.closest("[data-public-slot]");
        if (slot && event.key === " ") {
            event.preventDefault();
            slot.click();
        }
    });

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
            actionLabel.textContent = `${slot.dataset.publicSlot} h`;
            actionControl.dataset.href = slot.href;
            actionControl.disabled = false;
            actionControl.setAttribute("aria-disabled", "false");
            actionBar.classList.remove("is-empty");
            updateCompactSummary();
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
    results.querySelectorAll("[data-public-slot]").forEach(function (slot) {
        slot.setAttribute("role", "button");
        slot.setAttribute("aria-pressed", "false");
    });
    syncRovingTabindex(form.querySelector("[data-professional-picker]"));
    syncRovingTabindex(servicePicker);
    initializeCalendar();
    hideAction();
})();
