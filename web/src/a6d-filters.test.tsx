// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'

let root: Root
let container: HTMLDivElement
let fetchMock: ReturnType<typeof vi.fn>
const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
async function click(element: Element) { await act(async () => { (element as HTMLElement).click(); await tick() }) }
async function change(id: string, value: string) {
  await act(async () => {
    const select = container.querySelector<HTMLSelectElement>(id)!
    select.value = value
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await tick()
  })
}
const trigger = () => container.querySelector('.filter-button')!
const apply = () => container.querySelector<HTMLFormElement>('.filter-sheet form')!
async function submit() { await act(async () => { apply().dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await tick() }) }
const postCalls = () => fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith('/v1/posts?'))

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  window.history.replaceState(null, '', '/')
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container)
  fetchMock = vi.fn(async (url: string) => new Response(JSON.stringify(
    url === '/v1/models' ? { models: [{ model_key: 'rtx-3060', item_count: 2 }] }
      : url.startsWith('/v1/groups') ? { groups: [] } : { posts: [], next_cursor: null },
  ), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })
async function render() { await act(async () => { root.render(<App />); await tick() }) }

test.each([
  ['#model-key', 'rtx-3060', 'model_key', 'RTX 3060'],
  ['#vram-gb', '12', 'vram_gb', '12GB'],
  ['#item-condition', 'used', 'condition', 'มือสอง'],
  ['#item-warranty', 'active', 'warranty', 'มีประกัน'],
])('applies and clears %s', async (id, value, param, label) => {
  await render(); await click(trigger())
  expect(container.querySelector('#model-key')!.textContent).toContain('RTX 3060 (2)')
  await change(id, value); await submit()
  const query = new URL(postCalls().at(-1)!, 'http://localhost').searchParams
  expect([...query.entries()]).toEqual([['limit', '20'], [param, value]])
  expect(container.querySelector('.filter-count')!.textContent).toBe('1')
  expect(container.querySelector('.filter-chip')!.textContent).toBe(label)
  await click(trigger()); await change(id, ''); await submit()
  expect(new URL(postCalls().at(-1)!, 'http://localhost').searchParams.has(param)).toBe(false)
})

test('model load failure leaves other filters usable', async () => {
  fetchMock.mockImplementation(async (url: string) => url === '/v1/models'
    ? new Response('{}', { status: 500 })
    : new Response(JSON.stringify(url.startsWith('/v1/groups') ? { groups: [] } : { posts: [], next_cursor: null }), { status: 200 }))
  await render(); await click(trigger())
  expect(container.textContent).toContain('โหลดรายการรุ่นไม่สำเร็จ')
  await change('#item-warranty', 'none'); await submit()
  expect(new URL(postCalls().at(-1)!, 'http://localhost').searchParams.get('warranty')).toBe('none')
})

test('traps focus and closes with Escape', async () => {
  await render(); await click(trigger())
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)) })
  const first = container.querySelector<HTMLElement>('[aria-label="ปิดตัวกรอง"]')!
  const all = container.querySelector('[role="dialog"]')!.querySelectorAll('button:not(:disabled), select:not(:disabled), input:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])')
  const last = all[all.length - 1] as HTMLElement
  last.focus()
  await act(async () => { document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })) })
  expect(document.activeElement).toBe(first)
  await act(async () => { document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })) })
  expect(document.activeElement).toBe(last)
  await act(async () => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
  expect(container.querySelector('[role="dialog"]')).toBeNull()
  expect(document.activeElement).toBe(trigger())
})





test.each([true, false])('card and detail render item labels when present: %s', async (present) => {
  const attributes = present
    ? { model_key: 'rtx-3060', vram_gb: 12, condition: 'used', warranty: 'active', warranty_until: '2027-07', warranty_months_left: 8 }
    : { model_key: null, vram_gb: null, condition: null, warranty: null, warranty_until: null, warranty_months_left: null }
  const item = { item_id: 'i', source_item_key: 'i', model: null, sale_status: 'open', status_since: null, needs_review: false, ask: null, ...attributes }
  const post = { post_id: 'p', group_id: 'g', permalink: 'https://example.invalid', posted_at: null, last_success_at: null, has_unresolved_items: false, watched: false, thumbnail_evidence_id: null, items: [item] }
  const detail = { id: 'p', group_id: 'g', source_post_key: 'p', posted_at: null, seller_id: null, has_unresolved_items: false, current_items: [{ ...item, last_success_at: null }], observations: [] }
  fetchMock.mockImplementation(async (url: string) => new Response(JSON.stringify(
    url === '/v1/models' ? { models: [] } : url.startsWith('/v1/groups') ? { groups: [] }
      : url.includes('/timeline') ? { events: [], next_cursor: null }
        : url === '/v1/posts/p' ? detail : { posts: [post], next_cursor: null },
  ), { status: 200 }))
  await render()
  const card = container.querySelector('.post-card')!
  for (const label of ['RTX 3060 · 12GB', 'มือสอง', 'ประกันเหลือ 8 เดือน (ถึง ก.ค. 2570)']) {
    expect(card.textContent?.includes(label)).toBe(present)
  }
  if (!present) expect(card.querySelector('.item-attributes')).toBeNull()
  window.history.pushState(null, '', '/posts/p')
  await act(async () => { window.dispatchEvent(new PopStateEvent('popstate')); await tick() })
  await act(async () => { await tick() })
  const row = container.querySelector('.item-detail')!
  for (const label of ['RTX 3060 · 12GB', 'มือสอง', 'ประกันเหลือ 8 เดือน (ถึง ก.ค. 2570)']) {
    expect(row.textContent?.includes(label)).toBe(present)
  }
  if (!present) { expect(row.querySelector('h3')).toBeNull(); expect(row.querySelector('.item-attributes')).toBeNull() }
})

test('summarizes all four active filters and their count', async () => {
  await render(); await click(trigger())
  await change('#model-key', 'rtx-3060')
  await change('#vram-gb', '12')
  await change('#item-condition', 'used')
  await change('#item-warranty', 'active')
  await submit()
  expect(Array.from(container.querySelectorAll('.filter-chip'), (chip) => chip.textContent)).toEqual([
    'RTX 3060', '12GB', 'มือสอง', 'มีประกัน',
  ])
  expect(container.querySelector('.filter-count')!.textContent).toBe('4')
  expect([...new URL(postCalls().at(-1)!, 'http://localhost').searchParams.entries()]).toEqual([
    ['limit', '20'], ['model_key', 'rtx-3060'], ['vram_gb', '12'], ['condition', 'used'], ['warranty', 'active'],
  ])
})
