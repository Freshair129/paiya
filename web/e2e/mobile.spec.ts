import { test, expect } from './fixtures'

test('AT-05 captured image within three taps', async ({ page }) => {
  await page.goto('/')
  const card = page.locator('.post-card-link').filter({ has: page.locator('img') }).first()
  await expect(card).toBeInViewport()
  let taps = 0
  await card.click(); taps++
  const rows = page.locator('.link-list a[href^="/evidence/"]')
  await expect(rows.first()).toBeVisible()
  const hrefs = await rows.evaluateAll((els) => els.map((e) => e.getAttribute('href')))
  let target = ''
  for (const href of hrefs) {
    const response = await page.request.get(`/v1${href}`)
    const evidence = await response.json()
    if (evidence.media?.href) { target = href!; break }
  }
  expect(target).not.toBe('')
  await page.locator(`a[href="${target}"]`).click(); taps++
  const image = page.getByAltText('ภาพ crop จากหลักฐานของประกาศ')
  await expect(image).toBeVisible()
  await expect.poll(() => image.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0)
  await expect(page.getByRole('link', { name: 'เปิดโพสต์ต้นทาง' })).toBeVisible()
  expect(taps).toBeLessThanOrEqual(3)
  console.log(`AT-05 tap count: ${taps}`)
})

test('capture failed preserves reason, quote and permalink', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: /เปิดประกาศ RTX 2060/ }).click()
  await page.getByRole('link', { name: /เปิดหลักฐาน 1/ }).click()
  await expect(page.getByText('ไม่มีภาพหลักฐานในรอบนี้')).toBeVisible()
  await expect(page.getByText(/เก็บภาพไม่สำเร็จ · synthetic demo capture failed/)).toBeVisible()
  await expect(page.locator('blockquote')).not.toBeEmpty()
  await expect(page.getByRole('link', { name: 'เปิดโพสต์ต้นทาง' })).toHaveAttribute('href', /example\.invalid/)
})

test('filter narrows feed and exposes active filters on empty results', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.post-card-link')).toHaveCount(3)
  await page.getByRole('button', { name: /ตัวกรอง/ }).click()
  await page.getByLabel('สถานะการขาย').selectOption('reserved')
  await page.getByLabel('ราคาตั้งแต่').fill('5000')
  await page.getByRole('button', { name: 'แสดงผล' }).click()
  await expect(page.locator('.post-card-link')).toHaveCount(1)
  await page.getByRole('button', { name: /ตัวกรอง/ }).first().click()
  await page.getByLabel('ราคาตั้งแต่').fill('9999999')
  await page.getByRole('button', { name: 'แสดงผล' }).click()
  await expect(page.getByText('ยังไม่พบสินค้า')).toBeVisible()
  await expect(page.getByText(/ตัวกรองที่ใช้:/)).toBeVisible()
  await page.getByRole('button', { name: 'ล้างตัวกรอง' }).last().click()
  await expect(page.locator('.post-card-link')).toHaveCount(3)
})

test('no service worker and API responses are no-store', async ({ page }) => {
  const apiResponses: string[] = []
  page.on('response', (response) => { if (new URL(response.url()).pathname.startsWith('/v1/')) apiResponses.push(response.headers()['cache-control'] ?? '') })
  await page.goto('/')
  await expect(page.locator('.post-card-link')).toHaveCount(3)
  expect(apiResponses.length).toBeGreaterThan(0)
  expect(apiResponses.every((value) => value.includes('no-store'))).toBe(true)
  expect(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length)).toBe(0)
})


test('keyboard reaches card, evidence and permalink using Tab and Enter', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.post-card-link').first()).toBeVisible()
  async function tabTo(selector: string) {
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press('Tab')
      if (await page.locator(selector).evaluateAll((els) => els.some((el) => el === document.activeElement))) return
    }
    throw new Error(`Could not focus ${selector}`)
  }
  await tabTo('.post-card-link')
  await page.keyboard.press('Enter')
  await expect(page.locator('.link-list a[href^="/evidence/"]').first()).toBeVisible()
  await tabTo('.link-list a[href^="/evidence/"]')
  await page.keyboard.press('Enter')
  await expect(page.locator('.source-link')).toBeVisible()
  await tabTo('.source-link')
  await expect(page.locator('.source-link')).toBeFocused()
})

test('mobile pages have no horizontal overflow and accessible touch targets', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.post-card-link').first()).toBeVisible()
  await expect(page.locator('.post-card-link').first().getByText(/ราคาตั้งขาย/)).toBeVisible()
  await expect(page.locator('.post-card-link').first().locator('.status-pill')).toBeVisible()
  await expect(page.locator('.post-card-link').first().getByText(/ตรวจสำเร็จล่าสุด/)).toBeVisible()
  const postHref = await page.locator('.post-card-link').first().getAttribute('href')
  await page.goto(postHref!)
  await expect(page.locator('.link-list a[href^="/evidence/"]').first()).toBeVisible()
  const evidenceHref = await page.locator('.link-list a[href^="/evidence/"]').first().getAttribute('href')
  const sellerHref = await page.locator('a[href^="/sellers/"]').first().getAttribute('href')
  for (const path of ['/', postHref!, evidenceHref!, sellerHref!, '/watches', '/groups']) {
    await page.goto(path)
    await expect(page.locator('main h1')).toBeVisible()
    await expect(page.locator('[role="status"]')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth), path).toBeLessThanOrEqual(360)
    const undersized = await page.locator('a,button,input,select,textarea').evaluateAll((elements) => elements.filter((e) => {
      const r = e.getBoundingClientRect(); const s = getComputedStyle(e)
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && (r.width < 44 || r.height < 44)
    }).map((e) => `${e.outerHTML.slice(0, 180)} ${e.getBoundingClientRect().width}x${e.getBoundingClientRect().height}`))
    expect(undersized, path).toEqual([])
  }
})

test('capture the seven synthetic mobile screens', async ({ page }, testInfo) => {
  const response = await page.request.get('/v1/posts')
  const data = await response.json()
  const captured = data.posts.find((p: { thumbnail_evidence_id: string | null }) => p.thumbnail_evidence_id)
  const missing = data.posts.find((p: { permalink: string }) => p.permalink.endsWith('/capture-failed'))
  const failedPost = await (await page.request.get(`/v1/posts/${missing.post_id}`)).json()
  const failedId = failedPost.observations[0].evidence_ids[0]
  const detail = await (await page.request.get(`/v1/posts/${captured.post_id}`)).json()
  const screens = [
    ['feed', '/'], ['post', `/posts/${captured.post_id}`],
    ['evidence-image', `/evidence/${captured.thumbnail_evidence_id}`],
    ['evidence-missing', `/evidence/${failedId}`], ['seller', `/sellers/${detail.seller_id}`],
    ['watches', '/watches'], ['groups', '/groups'],
  ]
  for (const [name, path] of screens) {
    await page.goto(path)
    await expect(page.locator('main h1')).toBeVisible()
    await expect(page.locator('[role="status"]')).toHaveCount(0)
    if (name === 'evidence-image') await expect.poll(() => page.locator('.evidence-image-frame img').evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0)
    const filename = testInfo.outputPath(`${name}.png`)
    await page.screenshot({ path: filename })
    if (process.env.PAIYA_EVIDENCE_DIR) {
      const { copyFile, mkdir } = await import('node:fs/promises')
      const { join } = await import('node:path')
      await mkdir(process.env.PAIYA_EVIDENCE_DIR, { recursive: true })
      await copyFile(filename, join(process.env.PAIYA_EVIDENCE_DIR, `${name}.png`))
    }
  }
})
