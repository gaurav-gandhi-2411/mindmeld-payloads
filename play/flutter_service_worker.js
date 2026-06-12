'use strict';

// Self-destructing service worker: unregisters itself, clears all caches,
// and reloads clients so they pick up the redirect page instead of the cached Flutter app.

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      // Delete all caches so the old Flutter app shell is evicted.
      try {
        const cacheNames = await caches.keys();
        await Promise.all(cacheNames.map((name) => caches.delete(name)));
      } catch (e) {
        console.warn('[mindmeld-sw] Failed to clear caches:', e);
      }

      // Unregister this service worker — the new origin (Vercel) will register its own.
      try {
        await self.registration.unregister();
      } catch (e) {
        console.warn('[mindmeld-sw] Failed to unregister:', e);
      }

      // Claim all open clients, then navigate them so they re-request the page
      // and get the redirect HTML (which then forwards to playmindmeld.vercel.app).
      try {
        await self.clients.claim();
        const clients = await self.clients.matchAll({ type: 'window' });
        clients.forEach((client) => {
          if (client.url && 'navigate' in client) {
            client.navigate(client.url);
          }
        });
      } catch (e) {
        console.warn('[mindmeld-sw] Failed to reload clients:', e);
      }
    })()
  );
});
