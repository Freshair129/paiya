import { test as base, expect } from '@playwright/test'
const LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost'])
export const test = base.extend<{ localRequestGuard: void }>({
  localRequestGuard: [async ({ context }, use) => {
    const blocked: string[] = []
    await context.route('**/*', async (route) => {
      let hostname = ''
      try { hostname = new URL(route.request().url()).hostname } catch { /* blocked below */ }
      if (!LOCAL_HOSTS.has(hostname)) { blocked.push(route.request().url()); await route.abort('blockedbyclient'); return }
      await route.continue()
    })
    await use()
    expect(blocked, `External requests blocked: ${blocked.join(', ')}`).toEqual([])
  }, { auto: true }],
})
export { expect }
