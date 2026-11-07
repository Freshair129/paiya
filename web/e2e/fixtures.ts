import { test as base, expect } from '@playwright/test'

const LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost'])

// E2E specs import test and expect from this module to apply the guard to every context.
export const test = base.extend<{ localRequestGuard: void }>({
  localRequestGuard: [
    async ({ context }, use) => {
      await context.route('**/*', async (route) => {
        let hostname = ''
        try {
          hostname = new URL(route.request().url()).hostname
        } catch {
          // Invalid and non-HTTP URLs have no allowed local host.
        }
        if (!LOCAL_HOSTS.has(hostname)) {
          await route.abort('blockedbyclient')
          return
        }
        await route.continue()
      })
      await use()
    },
    { auto: true },
  ],
})

export { expect }
