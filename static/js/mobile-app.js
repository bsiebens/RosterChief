// RosterChief member app -- registers the service worker and exposes
// window.rosterchiefPush.{subscribe,unsubscribe}() for M7 (Notifications)
// to wire a permission-request button to (browsers require a user gesture
// before Notification.requestPermission() will prompt, so this is never
// called automatically on page load).

if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/app/sw.js"));
}

function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = window.atob(base64);
    return Uint8Array.from([...raw].map((char) => char.charCodeAt(0)));
}

async function subscribe() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return false;

    const permission = await Notification.requestPermission();
    if (permission !== "granted") return false;

    const publicKey = document.body.dataset.vapidPublicKey;
    if (!publicKey) return false;

    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    await fetch("/app/push/subscribe/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": document.body.dataset.csrftoken || "" },
        body: JSON.stringify(subscription.toJSON()),
    });
    return true;
}

async function unsubscribe() {
    if (!("serviceWorker" in navigator)) return;
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    if (!subscription) return;

    await fetch("/app/push/subscribe/", {
        method: "DELETE",
        headers: { "Content-Type": "application/json", "X-CSRFToken": document.body.dataset.csrftoken || "" },
        body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
    await subscription.unsubscribe();
}

window.rosterchiefPush = { subscribe, unsubscribe };
