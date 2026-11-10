import { readFileSync } from 'node:fs'
import { test, expect } from './fixtures'

test('AT-11 preview and retry a confirmation whose first response was lost', async ({ page, baseURL }) => {
  if (!baseURL) throw new Error('Playwright baseURL is not configured')
  const passphraseFile = process.env.PAIYA_E2E_PASSPHRASE_FILE
  if (!passphraseFile) throw new Error('E2E owner passphrase file is not configured')
  const passphrase = readFileSync(passphraseFile, 'utf8').trim()
  const challenge = await page.request.get('/v1/auth/challenge')
  expect(challenge.status()).toBe(200)
  const { nonce } = await challenge.json()
  const unlock = await page.request.post('/v1/auth/unlock', {
    headers: { Origin: new URL(baseURL).origin, 'X-CSRF-Token': nonce },
    data: { passphrase },
  })
  expect(unlock.status()).toBe(200)
  const statuses: number[] = []
  const keys: string[] = []
  await page.route('**/v1/imports?*', async (route) => {
    keys.push(route.request().headers()['idempotency-key'])
    const response = await route.fetch()
    statuses.push(response.status())
    if (statuses.length === 1) await route.abort('failed')
    else await route.fulfill({ response })
  })
  await page.goto('/imports')
  await page.getByLabel('Permalink ตรงของโพสต์').fill('https://www.facebook.com/groups/synthetic-demo/posts/424242/')
  await page.getByLabel('ข้อความประกาศที่คัดลอกมา').fill('RTX 3060 12GB 5,800 บาท')
  await page.getByRole('button', { name: 'ดูตัวอย่าง' }).click()
  await expect(page.getByRole('heading', { name: 'รายการและหลักฐานที่แยกได้' })).toBeVisible()
  await expect(page.getByText('การที่ผู้ซื้อเห็นตัวเลข ไม่ได้พิสูจน์ว่าผู้แสดงความคิดเห็นเป็นผู้ขาย')).toBeVisible()
  await page.getByRole('button', { name: 'ยืนยันและบันทึก' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByLabel('ข้อความประกาศที่คัดลอกมา')).toHaveValue('RTX 3060 12GB 5,800 บาท')
  await page.getByRole('button', { name: 'ยืนยันและบันทึก' }).click()
  await expect(page.getByText('บันทึกการนำเข้าแล้ว')).toBeVisible()
  expect(statuses).toEqual([201, 200])
  expect(keys).toHaveLength(2)
  expect(keys[0]).toBeTruthy()
  expect(keys[0]).toBe(keys[1])
  expect(await page.evaluate(() => sessionStorage.getItem('paiya.import.idempotency-key'))).toBeNull()
})
