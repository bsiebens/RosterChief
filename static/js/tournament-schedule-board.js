/*
 * The tournament schedule board's own drag-to-replan: dragging a match card
 * onto any point of any field's own timeline column computes the time that
 * point represents (from the column's own day-start + px-per-minute scale,
 * both read off its own data attributes -- see tournament_schedule_board.html),
 * snaps it to the nearest 5 minutes, and submits the page's one shared hidden
 * form to MatchRescheduleView -- the exact endpoint the plain table's own
 * inline "field + time" form already posts to. A blocked drop (double-
 * booking, a blackout window) is rejected and explained the same way an
 * invalid manual edit already is there: a full page reload showing the
 * notify() banner. No new AJAX/JSON endpoint -- this is a full-page-reload
 * progressive enhancement over a plain form, same as every other action in
 * this app (e.g. bulk-add-rows.js's own "clone from a <template>" idiom).
 */
(() => {
    const form = document.getElementById("schedule-board-reschedule-form");
    if (!form) return;

    const fieldInput = document.getElementById("schedule-board-field-input");
    const startInput = document.getElementById("schedule-board-start-input");
    const urlTemplate = form.dataset.urlTemplate;
    const placeholderPk = "00000000-0000-0000-0000-000000000000";

    const pad = (n) => String(n).padStart(2, "0");
    const toDatetimeLocal = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;

    document.querySelectorAll(".schedule-board-match").forEach((card) => {
        card.addEventListener("dragstart", (event) => {
            event.dataTransfer.setData("text/plain", card.dataset.matchPk);
            event.dataTransfer.effectAllowed = "move";
        });
    });

    document.querySelectorAll(".schedule-board-column").forEach((column) => {
        column.addEventListener("dragover", (event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
        });

        column.addEventListener("drop", (event) => {
            event.preventDefault();
            const matchPk = event.dataTransfer.getData("text/plain");
            if (!matchPk) return;

            const dayStart = new Date(column.dataset.dayStart);
            const pxPerMinute = Number(column.dataset.pxPerMinute);
            const offsetY = event.clientY - column.getBoundingClientRect().top;
            const minutes = Math.max(0, Math.round(offsetY / pxPerMinute / 5) * 5);
            const target = new Date(dayStart.getTime() + minutes * 60000);

            fieldInput.value = column.dataset.fieldPk;
            startInput.value = toDatetimeLocal(target);
            form.action = urlTemplate.replace(placeholderPk, matchPk);
            form.submit();
        });
    });
})();
