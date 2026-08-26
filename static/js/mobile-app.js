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
    // Every early return here used to fail silently -- the "Enable" button's
    // own click handler flipped its card away regardless of the outcome, so
    // a missing VAPID key (nothing to configure client-side, purely a server
    // deployment gap) or a denied permission looked identical to success:
    // nothing happened, and nothing said why. console.warn at least gets it
    // into the browser console; notifications.html's own click handler is
    // what surfaces the user-visible failure state now.
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
        console.warn("rosterchiefPush: push notifications aren't supported in this browser.");
        return false;
    }

    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
        console.warn(`rosterchiefPush: notification permission was ${permission}, not granted.`);
        return false;
    }

    const publicKey = document.body.dataset.vapidPublicKey;
    if (!publicKey) {
        console.warn("rosterchiefPush: no VAPID public key configured on the server (DJANGO_VAPID_PUBLIC_KEY) -- push is not set up for this deployment.");
        return false;
    }

    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    const response = await fetch("/app/push/subscribe/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": document.body.dataset.csrftoken || "" },
        body: JSON.stringify(subscription.toJSON()),
    });
    if (!response.ok) {
        console.warn(`rosterchiefPush: the server rejected the subscription (${response.status}).`);
        return false;
    }
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
