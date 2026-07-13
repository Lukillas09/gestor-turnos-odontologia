(function () {
    "use strict";

    const form = document.querySelector("[data-profile-form]");
    if (!form) {
        return;
    }

    const input = form.querySelector("input[name='foto_perfil']");
    const image = document.querySelector("[data-profile-photo-image]");
    const initial = document.querySelector("[data-profile-photo-initial]");
    const positionX = form.querySelector("input[name='foto_posicion_x']");
    const positionY = form.querySelector("input[name='foto_posicion_y']");
    let previewUrl = null;

    function updatePosition() {
        if (image) {
            image.style.objectPosition = `${positionX ? positionX.value : 50}% ${positionY ? positionY.value : 50}%`;
        }
    }

    if (input && image) {
        input.addEventListener("change", function () {
            const file = input.files && input.files[0];
            if (!file) {
                return;
            }
            if (previewUrl) {
                URL.revokeObjectURL(previewUrl);
            }
            previewUrl = URL.createObjectURL(file);
            image.src = previewUrl;
            image.hidden = false;
            if (initial) {
                initial.hidden = true;
            }
            updatePosition();
        });
    }

    [positionX, positionY].filter(Boolean).forEach(function (control) {
        control.addEventListener("input", updatePosition);
    });
    window.addEventListener("pagehide", function () {
        if (previewUrl) {
            URL.revokeObjectURL(previewUrl);
        }
    });
    updatePosition();
})();
