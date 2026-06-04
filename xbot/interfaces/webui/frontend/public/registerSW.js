if ("serviceWorker" in navigator) {
    navigator.serviceWorker.getRegistrations()
        .then((registrations) => Promise.all(registrations.map((registration) => registration.unregister())))
        .then(() => {
            if ("caches" in window) {
                return caches.keys().then((keys) => Promise.all(keys.map((key) => caches.delete(key))));
            }
            return undefined;
        })
        .catch(() => undefined);
}
