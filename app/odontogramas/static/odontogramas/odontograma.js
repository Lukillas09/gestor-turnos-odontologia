(function () {
    const chart = document.querySelector("#odontogramaChart");
    const modal = document.querySelector("#odontogramaModal");
    const form = document.querySelector("#odontogramaForm");

    if (!chart || !modal || !form || chart.dataset.canEdit !== "true") {
        return;
    }

    const saveMode = chart.dataset.saveMode || "ajax";
    const deferredInput = chart.dataset.deferredInput
        ? document.querySelector(chart.dataset.deferredInput)
        : null;
    const pendingStates = new Map();
    const colorByState = {
        sano: "neutro",
        caries: "rojo",
        restauracion_necesaria: "rojo",
        extraccion_indicada: "rojo",
        obturacion: "azul",
        corona: "azul",
        implante: "azul",
        conducto: "azul",
        protesis: "azul",
        sellador: "verde",
        temporal: "verde",
        control: "verde",
        ausente: "negro",
        extraido: "negro",
        fractura: "negro",
        observacion_especial: "negro",
    };
    const colorLabels = {
        neutro: "Sin color clínico",
        azul: "Azul: tratamiento realizado o existente",
        rojo: "Rojo: tratamiento pendiente",
        verde: "Verde: control u observación",
        negro: "Negro: ausente, extraído o especial",
    };
    const faceColorClasses = [
        "tooth-face-neutro",
        "tooth-face-azul",
        "tooth-face-rojo",
        "tooth-face-verde",
        "tooth-face-negro",
    ];

    const fields = {
        diente: form.querySelector("[name='diente']"),
        cara: form.querySelector("[name='cara']"),
        estado: form.querySelector("[name='estado_clinico']"),
        observacion: form.querySelector("[name='observacion']"),
        realizado: form.querySelector("[name='realizado']"),
        csrf: form.querySelector("[name='csrfmiddlewaretoken']"),
        saveButton: form.querySelector("[data-save-odontograma]"),
        error: document.querySelector("#odontogramaFormError"),
        title: document.querySelector("#odontogramaModalTitle"),
        subtitle: document.querySelector("#odontogramaModalSubtitle"),
        colorPreview: document.querySelector("#odontogramaColorPreview"),
        history: document.querySelector("#odontogramaHistory"),
    };
    let activeFace = null;

    function openModal(face) {
        activeFace = face;
        fields.diente.value = face.dataset.tooth;
        fields.cara.value = face.dataset.face;
        fields.estado.value = face.dataset.state || "sano";
        fields.observacion.value = face.dataset.observation || "";
        fields.realizado.checked = face.dataset.realized === "true";
        fields.title.textContent = `Diente ${face.dataset.tooth}`;
        fields.subtitle.textContent = `${face.dataset.faceLabel} · ${face.dataset.stateLabel || "Sin estado registrado"}`;
        clearError();
        updateColorPreview();
        modal.hidden = false;
        fields.estado.focus();
    }

    function closeModal() {
        modal.hidden = true;
        activeFace = null;
        clearError();
    }

    function clearError() {
        fields.error.hidden = true;
        fields.error.textContent = "";
    }

    function showError(message) {
        fields.error.textContent = message;
        fields.error.hidden = false;
    }

    function updateColorPreview() {
        const color = colorByState[fields.estado.value] || "neutro";
        fields.colorPreview.className = `odontograma-color-preview is-${color}`;
        fields.colorPreview.textContent = colorLabels[color];
    }

    function updateFace(face, estado) {
        face.classList.remove(...faceColorClasses);
        face.classList.add(`tooth-face-${estado.color}`);
        face.dataset.state = estado.estado_clinico;
        face.dataset.stateLabel = estado.estado_label;
        face.dataset.observation = estado.observacion || "";
        face.dataset.realized = estado.realizado ? "true" : "false";

        let title = face.querySelector("title");
        if (!title) {
            title = document.createElementNS("http://www.w3.org/2000/svg", "title");
            face.appendChild(title);
        }
        title.textContent = estado.tooltip;
    }

    function prependHistory(html) {
        if (!fields.history || !html) {
            return;
        }

        const empty = document.querySelector("#odontogramaHistoryEmpty");
        if (empty) {
            empty.remove();
        }

        const template = document.createElement("template");
        template.innerHTML = html.trim();
        fields.history.prepend(template.content.firstElementChild);
    }

    function buildDeferredState() {
        const selectedOption = fields.estado.options[fields.estado.selectedIndex];
        const color = colorByState[fields.estado.value] || "neutro";
        const estadoLabel = selectedOption ? selectedOption.textContent : fields.estado.value;
        const caraLabel = activeFace.dataset.faceLabel || fields.cara.value;
        const realizado = fields.realizado.checked;
        const observacion = fields.observacion.value || "";

        return {
            diente: Number(fields.diente.value),
            cara: fields.cara.value,
            cara_label: caraLabel,
            estado_clinico: fields.estado.value,
            estado_label: estadoLabel,
            color,
            observacion,
            realizado,
            tooltip: [
                `${fields.diente.value} - ${caraLabel}`,
                estadoLabel,
                realizado ? "Realizado" : "Pendiente",
                observacion,
            ].filter(Boolean).join("\n"),
        };
    }

    function saveDeferredState() {
        if (!deferredInput) {
            showError("No se pudo preparar el guardado del odontograma.");
            return;
        }

        const estado = buildDeferredState();
        const key = `${estado.diente}:${estado.cara}`;
        pendingStates.set(key, estado);
        deferredInput.value = JSON.stringify(Array.from(pendingStates.values()));
        updateFace(activeFace, estado);
        closeModal();
    }

    async function saveAjaxState() {
        clearError();

        try {
            const response = await fetch(chart.dataset.saveUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": fields.csrf.value,
                },
                body: JSON.stringify({
                    diente: fields.diente.value,
                    cara: fields.cara.value,
                    estado_clinico: fields.estado.value,
                    observacion: fields.observacion.value,
                    realizado: fields.realizado.checked,
                }),
            });
            const data = await response.json();

            if (!response.ok || !data.ok) {
                showError(data.error || "No se pudo guardar el estado dental.");
                return;
            }

            updateFace(activeFace, data.estado);
            prependHistory(data.historial_html);
            closeModal();
        } catch (error) {
            showError("No se pudo conectar con el servidor. Intentá nuevamente.");
        }
    }

    chart.addEventListener("click", function (event) {
        const face = event.target.closest(".tooth-face");
        if (face) {
            openModal(face);
        }
    });

    chart.addEventListener("keydown", function (event) {
        const face = event.target.closest(".tooth-face");
        if (!face) {
            return;
        }

        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openModal(face);
        }
    });

    modal.addEventListener("click", function (event) {
        if (event.target.matches("[data-close-modal]")) {
            closeModal();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (!modal.hidden && event.key === "Escape") {
            closeModal();
        }
    });

    fields.estado.addEventListener("change", updateColorPreview);

    fields.saveButton.addEventListener("click", function () {
        if (!activeFace) {
            return;
        }

        if (saveMode === "deferred") {
            saveDeferredState();
            return;
        }

        saveAjaxState();
    });
})();
