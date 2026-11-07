import { expect, test } from 'vitest'
import { conditionLabel, modelLabel, warrantyLabel } from './labels'

test.each([
  ['rtx-3060', 'RTX 3060'],
  ['rtx-3060-ti', 'RTX 3060 Ti'],
  ['rtx-4070-ti-super', 'RTX 4070 Ti Super'],
  ['rx-7900-xtx', 'RX 7900 XTX'],
  ['gtx-1660-super', 'GTX 1660 Super'],
])('formats model %s with and without memory', (key, expected) => {
  expect(modelLabel(key, null, null)).toBe(expected)
  expect(modelLabel(key, 12, null)).toBe(`${expected} · 12GB`)
})

test('uses raw model when key is null', () => {
  expect(modelLabel(null, null, 'raw model')).toBe('raw model')
  expect(modelLabel(null, null, null)).toBeNull()
})

test.each([
  ['new', 'ของใหม่'], ['new_no_warranty', 'ของใหม่ ไม่มีประกัน'],
  ['used', 'มือสอง'], [null, null],
] as const)('formats condition %s', (value, expected) => {
  expect(conditionLabel(value)).toBe(expected)
})

test.each([
  ['none', null, null, 'ไม่มีประกัน'],
  ['active', null, null, 'มีประกัน'],
  ['active', '2027-07', 8, 'ประกันเหลือ 8 เดือน (ถึง ก.ค. 2570)'],
  ['active', '2027-07', 0, 'ประกันถึง ก.ค. 2570 · ครบกำหนดแล้ว'],
  ['active', '2027-13', 8, 'มีประกัน'],
  [null, null, null, null],
] as const)('formats warranty %s %s %s', (value, until, months, expected) => {
  expect(warrantyLabel(value, until, months)).toBe(expected)
})
