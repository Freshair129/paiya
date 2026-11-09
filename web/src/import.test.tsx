// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'

let root: Root
let container: HTMLDivElement
let fetchMock: ReturnType<typeof vi.fn>
let commitStatus = 503
const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
const link = 'https://www.facebook.com/groups/demo-group/posts/42/'
const draft = 'RTX 3060 12GB 5,800 บาท'
const preview = {
  source_kind: 'manual_share', permalink: link, parser_version: '1', preview_digest: 'digest',
  items: [{ source_item_key: 'item-1', model: 'RTX 3060', evidence_keys: ['e1'], price_facts: [{ kind: 'ask', amount: '5800', unit: 'THB', currency: 'THB', evidence_key: 'e1' }] }],
  evidence: [{ local_key: 'e1', role: 'price', source_span: { field: 'post_text', quote: draft, start: 0, end: draft.length }, media_state: 'not_captured' }],
  unresolved_item_keys: [], unresolved_fields: [{ field: 'ask_price', source_item_key: 'item-2' }], unassigned_price_facts: [],
}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  window.history.replaceState(null, '', '/imports')
  window.sessionStorage.clear()
  commitStatus = 503
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  fetchMock = vi.fn(async (url: string) => {
    if (url.startsWith('/v1/groups')) return json({ groups: [{ group_id: 'g1', group_key: 'demo-group' }] })
    if (url === '/v1/auth/csrf') return json({ csrf_token: 'csrf' })
    if (url.startsWith('/v1/imports/preview')) return json(preview)
    if (url.startsWith('/v1/imports?')) return commitStatus === 200
      ? json({ post_id: 'p', observation_id: 'o', item_ids: [], source_kind: 'manual_share', observed_at: 'now' })
      : json({ detail: { code: 'preview_digest_mismatch', field: 'preview_digest' } }, commitStatus)
    return json({ posts: [], next_cursor: null })
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); window.sessionStorage.clear() })
async function render() { await act(async () => { root.render(<App />); await tick() }) }
async function setValue(selector: string, value: string) {
  const element = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector)!
  const prototype = element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  await act(async () => { Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value); element.dispatchEvent(new Event('input', { bubbles: true })); await tick() })
}
async function submit() { await act(async () => { container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await tick() }) }
async function confirm() { await act(async () => { container.querySelector<HTMLButtonElement>('.import-confirm')!.click(); await tick() }) }
async function fill() { await setValue('#import-permalink', link); await setValue('#import-text', draft) }

test('shows quotes, unresolved fields and author caveat; keeps draft and key until a successful retry', async () => {
  await render(); await fill(); await submit()
  expect(container.textContent).toContain('RTX 3060')
  expect(container.textContent).toContain('5,800')
  expect(container.textContent).toContain(draft)
  expect(container.textContent).toContain('ช่องที่ยังระบุไม่ได้')
  expect(container.textContent).toContain('ไม่ได้พิสูจน์ว่าผู้แสดงความคิดเห็นเป็นผู้ขาย')
  await confirm()
  expect(container.textContent).toContain('นำเข้าข้อมูลไม่สำเร็จ')
  expect(container.querySelector<HTMLTextAreaElement>('#import-text')!.value).toBe(draft)
  const key = window.sessionStorage.getItem('paiya.import.idempotency-key')
  expect(key).toBeTruthy()
  commitStatus = 200
  await confirm()
  const commits = fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/v1/imports?'))
  expect(commits).toHaveLength(2)
  expect(commits.map(([, options]) => options.headers['Idempotency-Key'])).toEqual([key, key])
  expect(window.sessionStorage.getItem('paiya.import.idempotency-key')).toBeNull()
  expect(container.textContent).toContain('บันทึกการนำเข้าแล้ว')
})

test('422 retains the draft and identifies the field', async () => {
  fetchMock.mockImplementation(async (url: string) => {
    if (url.startsWith('/v1/groups')) return json({ groups: [{ group_id: 'g1', group_key: 'demo-group' }] })
    if (url === '/v1/auth/csrf') return json({ csrf_token: 'csrf' })
    return json({ detail: { code: 'invalid_permalink', field: 'permalink' } }, 422)
  })
  await render(); await fill(); await submit()
  expect(container.querySelector<HTMLInputElement>('#import-permalink')!.value).toBe(link)
  expect(container.querySelector<HTMLTextAreaElement>('#import-text')!.value).toBe(draft)
  expect(container.querySelector('#import-permalink')!.getAttribute('aria-invalid')).toBe('true')
  expect(container.textContent).toContain('ลิงก์ระบุตัวโพสต์ไม่ได้')
})

test('starting a new submission clears the stored confirmation key', async () => {
  await render(); await fill(); await submit(); await confirm()
  expect(window.sessionStorage.getItem('paiya.import.idempotency-key')).toBeTruthy()
  await act(async () => { container.querySelector<HTMLButtonElement>('.import-reset')!.click(); await tick() })
  expect(window.sessionStorage.getItem('paiya.import.idempotency-key')).toBeNull()
  expect(container.querySelector<HTMLTextAreaElement>('#import-text')!.value).toBe('')
})

test('409 preserves the draft and a new preview uses a new confirmation key', async () => {
  commitStatus = 409
  await render(); await fill(); await submit(); await confirm()
  expect(container.textContent).toContain('หากข้อมูลเปลี่ยน ให้ตรวจตัวอย่างใหม่และใช้คีย์การยืนยันใหม่')
  expect(container.querySelector<HTMLInputElement>('#import-permalink')!.value).toBe(link)
  expect(container.querySelector<HTMLTextAreaElement>('#import-text')!.value).toBe(draft)
  const oldKey = window.sessionStorage.getItem('paiya.import.idempotency-key')
  expect(oldKey).toBeTruthy()
  await submit(); await confirm()
  const commits = fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/v1/imports?'))
  expect(commits).toHaveLength(2)
  expect(commits[1][1].headers['Idempotency-Key']).not.toBe(oldKey)
})
