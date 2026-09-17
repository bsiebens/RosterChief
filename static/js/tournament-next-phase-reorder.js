/*
 * "Start next phase"'s own drag-to-reorder: each group's standings table
 * gets its own reorder scope (.rank-reorder-body, one <tbody> per group,
 * never mixed across groups), a plain HTML5 drag-and-drop over the row
 * being dragged inserts it before/after the row underneath the pointer,
 * and every row's own hidden rank__<group>__<team> input (already carrying
 * its server-computed default -- see tournament_stage_next_phase.html) is
 * kept in sync with the row's live DOM position after every move. No
 * network round trip per drag -- the page's one shared "Push teams into
 * their slots" submit at the bottom is still what actually posts, same as
 * before the drag handle existed; this only ever changes what value that
 * submit carries.
 */
(() => {
    document.querySelectorAll(".rank-reorder-body").forEach((tbody) => {
        let dragged = null;

        const renumber = () => {
            tbody.querySelectorAll(".rank-reorder-row").forEach((row, index) => {
                const rank = index + 1;
                const number = row.querySelector(".rank-reorder-number");
                const input = row.querySelector(".rank-reorder-input");
                if (number) number.textContent = rank;
                if (input) input.value = rank;
            });
        };

        tbody.querySelectorAll(".rank-reorder-row").forEach((row) => {
            row.addEventListener("dragstart", () => {
                dragged = row;
                row.classList.add("opacity-40");
            });
            row.addEventListener("dragend", () => {
                row.classList.remove("opacity-40");
                dragged = null;
            });
            row.addEventListener("dragover", (event) => {
                event.preventDefault();
                if (!dragged || dragged === row) return;
                const rect = row.getBoundingClientRect();
                const insertBefore = event.clientY < rect.top + rect.height / 2;
                row.parentNode.insertBefore(dragged, insertBefore ? row : row.nextSibling);
                renumber();
            });
            row.addEventListener("drop", (event) => event.preventDefault());
        });
    });
})();
