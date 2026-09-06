self.addEventListener('push', event => {
  const payload = event.data?.json() ?? {}
  event.waitUntil(self.registration.showNotification(payload.title || '목포 청년 정책', {
    body: payload.body || '새 정책 알림이 있습니다.',
    icon: '/favicon.svg',
    badge: '/favicon.svg',
    tag: payload.tag || 'mokpo-youth-policy',
    data: { url: payload.url || '/' },
  }))
})

self.addEventListener('notificationclick', event => {
  event.notification.close()
  const target = event.notification.data?.url || '/'
  event.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windows => {
    const existing = windows.find(client => new URL(client.url).origin === self.location.origin)
    return existing ? existing.focus().then(() => existing.navigate(target)) : clients.openWindow(target)
  }))
})
