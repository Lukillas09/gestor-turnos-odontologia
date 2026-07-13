(function () {
    "use strict";

    document.querySelectorAll("[data-review-form]").forEach(function (form) {
        const submitButton = form.querySelector("[data-review-submit]");
        const actionInputs = Array.from(form.querySelectorAll("[data-review-action]"));
        const fieldGroup = document.querySelector("[data-review-field-group]");
        const fieldCheckboxes = Array.from(document.querySelectorAll("[data-review-field-checkbox]"));

        function syncReviewState() {
            const selectedAction = actionInputs.find(function (input) { return input.checked; });
            if (!selectedAction) {
                return;
            }
            if (submitButton && selectedAction.dataset.buttonLabel) {
                submitButton.textContent = selectedAction.dataset.buttonLabel;
            }
            const canSelectFields = selectedAction.value === "aplicar_campos";
            if (fieldGroup) {
                fieldGroup.classList.toggle("is-disabled", !canSelectFields);
            }
            fieldCheckboxes.forEach(function (checkbox) {
                checkbox.disabled = !canSelectFields;
            });
        }

        actionInputs.forEach(function (input) {
            input.addEventListener("change", syncReviewState);
        });
        syncReviewState();
    });
})();
