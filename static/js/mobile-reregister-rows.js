/*
 * The registration editor sheet on the Register screen (mobile/templates/mobile/
 * _registration_editor_sheet.html). The list, the editor and every action are plain form
 * posts that work without JavaScript; this only upgrades the sheet: it opens as a modal
 * dialog when htmx swaps the editor in (Add a registration / Edit / a person, role or
 * team change) or when the server renders it already open (a failed validation), and
 * closes on Cancel or a tap on the backdrop.
 */
(() => {
    const dialog = document.getElementById("registration-editor");
    if (!dialog || typeof dialog.showModal !== "function") return;

    const openModal = () => {
        // A server-rendered <dialog open> is non-modal -- reopen it as a modal.
        if (dialog.open && !dialog.matches(":modal")) dialog.close();
        if (!dialog.open) dialog.showModal();
    };

    if (dialog.hasAttribute("data-autoopen")) openModal();

    document.body.addEventListener("htmx:afterSwap", (event) => {
        if (event.detail.target && event.detail.target.id === "registration-editor-body") openModal();
    });

    dialog.addEventListener("click", (event) => {
        const target = event.target instanceof Element ? event.target : null;
        if (!target) return;
        if (target.closest("[data-editor-cancel]")) {
            event.preventDefault();
            dialog.close();
            return;
        }
        // A tap on the backdrop (the dialog element itself, outside its box) closes it.
        if (target === dialog) {
            const box = dialog.getBoundingClientRect();
            if (event.clientY < box.top || event.clientX < box.left || event.clientX > box.right) dialog.close();
        }
    });
})();
