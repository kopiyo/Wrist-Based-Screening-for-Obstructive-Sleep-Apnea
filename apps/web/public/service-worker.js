self.addEventListener("push", event => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch { payload = {}; }
  const title = typeof payload.title === "string" ? payload.title : "Nightwise screening update";
  const body = typeof payload.body === "string" ? payload.body : "You have a screening update. Sign in to view details. Screening tool, not a diagnosis. Research use only.";
  event.waitUntil(self.registration.showNotification(title, { body, data: { url: "/" }, tag: "nightwise-screening" }));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(clients => {
    const client = clients.find(windowClient => "focus" in windowClient);
    return client ? client.focus() : self.clients.openWindow("/");
  }));
});
