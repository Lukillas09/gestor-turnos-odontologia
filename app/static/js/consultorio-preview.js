(function () {
    "use strict";

    const form = document.querySelector("[data-consultorio-form]");
    if (!form) {
        return;
    }

    function field(name) {
        return form.querySelector(`[name='${name}']`);
    }

    const fields = {
        name: field("nombre_corto"), commercialName: field("nombre_comercial"),
        title: field("titulo_portada"), welcome: field("texto_bienvenida"),
        color: field("color_principal"), logo: field("logo"), removeLogo: field("quitar_logo"),
        address: field("direccion"), locality: field("localidad"), province: field("provincia"),
        phone: field("telefono"), email: field("email"),
    };
    const preview = {
        card: form.querySelector("[data-preview-card]"), logo: form.querySelector("[data-preview-logo]"),
        name: form.querySelector("[data-preview-name]"), title: form.querySelector("[data-preview-title]"),
        welcome: form.querySelector("[data-preview-welcome]"), colorText: form.querySelector("[data-preview-color-text]"),
        address: form.querySelector("[data-preview-address]"), phone: form.querySelector("[data-preview-phone]"),
        email: form.querySelector("[data-preview-email]"),
    };
    const fallbackInitials = form.dataset.previewInitials || "GT";
    const fallbackLogo = form.dataset.previewLogo || "";

    function textValue(input, fallback) {
        const value = input && input.value.trim();
        return value || fallback;
    }

    function initials(value) {
        const words = value.trim().split(/\s+/).filter(Boolean);
        if (!words.length) {
            return fallbackInitials;
        }
        return (words.length === 1 ? words[0].slice(0, 2) : words[0][0] + words[1][0]).toUpperCase();
    }

    function renderLogo(src) {
        const name = textValue(fields.name, textValue(fields.commercialName, "Gestor de Turnos"));
        const fallback = document.createElement("span");
        fallback.textContent = initials(name);
        preview.logo.replaceChildren(fallback);
        if (src) {
            const image = document.createElement("img");
            image.src = src;
            image.alt = "";
            preview.logo.replaceChildren(image);
        }
    }

    function updateText() {
        const name = textValue(fields.name, textValue(fields.commercialName, "Gestor de Turnos"));
        const address = [fields.address, fields.locality, fields.province]
            .map(function (input) { return input && input.value.trim(); })
            .filter(Boolean).join(", ");
        preview.name.textContent = name;
        preview.title.textContent = textValue(fields.title, "Reservá tu turno odontológico de forma simple");
        preview.welcome.textContent = textValue(fields.welcome, "Elegí un profesional, seleccioná un horario y enviá tu solicitud.");
        preview.address.textContent = address || "Datos de contacto opcionales";
        preview.phone.textContent = textValue(fields.phone, "Teléfono opcional");
        preview.email.textContent = textValue(fields.email, "Email opcional");
        if (!fields.logo || !fields.logo.files.length) {
            renderLogo(fields.removeLogo && fields.removeLogo.checked ? "" : fallbackLogo);
        }
    }

    function updateColor() {
        const value = fields.color && fields.color.value ? fields.color.value.toUpperCase() : "#2563EB";
        if (/^#[0-9A-F]{6}$/.test(value)) {
            preview.card.style.setProperty("--preview-color", value);
            preview.colorText.textContent = value;
        }
    }

    function updateLogo() {
        if (fields.removeLogo && fields.removeLogo.checked) {
            if (fields.logo) {
                fields.logo.value = "";
            }
            renderLogo("");
        } else if (fields.logo && fields.logo.files.length) {
            const reader = new FileReader();
            reader.addEventListener("load", function () { renderLogo(reader.result); });
            reader.readAsDataURL(fields.logo.files[0]);
        } else {
            renderLogo(fallbackLogo);
        }
    }

    Object.values(fields).filter(Boolean).forEach(function (input) {
        input.addEventListener("input", function () { updateText(); updateColor(); });
        input.addEventListener("change", function () { updateText(); updateColor(); updateLogo(); });
    });
    updateText();
    updateColor();
    updateLogo();
})();
