// @vitest-environment jsdom

import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, expect, test, vi } from 'vitest'
import App from './App'

afterEach(() => {
  vi.unstubAllGlobals()
})

test('renders the Paiya application shell', () => {
  expect(renderToStaticMarkup(<App />)).toContain('ป้ายยา')
})

test('shows the unlock screen when the API requires an owner session', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('fetch', (_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  }))
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)

  await act(async () => root.render(<App />))
  await act(async () => window.dispatchEvent(new Event('paiya:session-required')))

  expect(container.textContent).toContain('ปลดล็อกข้อมูลของคุณ')
  expect(container.querySelector('input[type="password"]')).not.toBeNull()
  await act(async () => root.unmount())
  container.remove()
})
