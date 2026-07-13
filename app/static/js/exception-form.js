(function () {
    "use strict";

    document.querySelectorAll("[data-excepcion-form]").forEach(function (form) {
        const allDay = form.querySelector("input[name='todo_el_dia']");
        const timeFields = form.querySelectorAll("[data-partial-time-field]");

        function refresh() {
            timeFields.forEach(function (field) {
                field.hidden = Boolean(allDay && allDay.checked);
            });
        }

        if (allDay) {
            allDay.addEventListener("change", refresh);
            refresh();
        }
    });
})();
