import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'
import App from './App'

test('renders the Paiya application shell', () => {
  expect(renderToStaticMarkup(<App />)).toContain('ป้ายยา')
})
