import { readFileSync } from 'node:fs'
import { test, expect } from './fixtures'

test.use({ screenshot: 'off', trace: 'off' })

const passphraseFile = process.env.PAIYA_E2E_PASSPHRASE_FILE
if (!passphraseFile) throw new Error('E2E owner passphrase file is not configured')
const passphrase = readFileSync(passphraseFile, 'utf8').trim()

test('owner unlocks the app after a session-required read', async ({ page }) => {
  let unlocked = false
  await page.context().clearCookies()
  await page.route('**/v1/posts*', async (route) => {
    if (unlocked) {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: { code: 'session_required', message: 'A valid owner session is required' },
      }),
    })
  })
  await page.route('**/v1/auth/unlock', async (route) => {
    const response = await route.fetch()
    unlocked = response.ok()
    await route.fulfill({ response })
  })

  await page.goto('/')
  const input = page.getByLabel('รหัสผ่านเจ้าของเครื่อง')
  await expect(input).toBeVisible()
  await input.fill(passphrase)
  await page.getByRole('button', { name: 'ปลดล็อก' }).click()

  await expect(input).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: 'เมนูหลัก' })).toBeVisible()
})
