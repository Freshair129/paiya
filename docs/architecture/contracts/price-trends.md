# สัญญาแนวโน้มราคาตั้งขาย (A7a)

**สถานะ** แบบที่ใช้อ้างอิงสำหรับงาน A7a ตามคำตัดสิน G1–G4 ใน [Issue #38](https://github.com/Freshair129/paiya/issues/38) · **ผู้ดูแล** เจ้าของ API และฐานข้อมูล

หน้านี้กำหนดชุดตัวอย่างที่เทียบกันได้ การคำนวณค่ากลาง เกณฑ์ข้อมูลไม่พอ และรูปแบบ `GET /v1/prices/trends` ที่ MCP `get_price_trend` ใช้ service เดียวกัน ใช้คู่กับ [SRS](../../requirements/SRS.md) ข้อ 8, FR-12, AT-10 และ [แบบจำลองข้อมูล](../data-model.md) ข้อ 3–4 A7a ทำเครื่องคำนวณและปิดส่วนราคาของ AT-10 ส่วน A7b ปิดความแม่นยำราคาของ AT-07 หลังกฎสกัดของ A6

## 1. ask ที่ยืนยันได้

`price_facts` เพิ่ม `validation_result` (`confirmed`, `rejected`) และ `reason` โดยใช้เงื่อนไขหลักฐานเดียวกับสถานะใน [สัญญาสถานะรายชิ้น](item-status-and-watches.md) ข้อ 2 (`rule_version = price-v1.0`) แถวเดิมเติมค่าด้วยกติกาเดียวกันใน migration ผลเทียบราคาใช้เฉพาะ `confirmed`

## 2. ชุดตัวอย่าง

- **หนึ่งข้อเสนอ = หนึ่งชิ้นสินค้า** (`listing_items`) ยังไม่รวมข้ามโพสต์ด้วย `offer_clusters` (G4) ผลระบุข้อจำกัดนี้ใน `limitations`
- **ตัวอย่างของข้อเสนอ** คือ ask แรกที่ยืนยันได้ตาม `observed_at` เร็วสุดของชิ้นนั้น คำนวณใหม่ทุกครั้งที่อ่าน จึงคงเดิมเมื่อ replay และ poll ซ้ำไม่เพิ่ม n การลดราคาภายหลังแสดงใน timeline ไม่สร้างตัวอย่างใหม่ วันของตัวอย่างคือวันตาม Asia/Bangkok ของ `observed_at` นั้น
- **กุญแจเทียบ** `model_key`, `variant`, `vram_gb`, `condition`, `unit`, `currency` ต้องตรงกันทุกค่า ชิ้นที่ `model_key` เป็น NULL ไม่เข้าชุดและนับใน `excluded.unclassified` ค่ารุ่นเหล่านี้มาจากกฎสกัดของ A6 เทสต์ของ A7a ตั้งค่าในข้อมูลสังเคราะห์โดยตรง
- **ตัดออก** `price_kind` ที่ไม่ใช่ `ask` (ค่าส่ง มัดจำ ค่างวด ยอดเสนอซื้อ), `unit` ที่เป็น `bundle` หรือ `unknown`, ราคาที่มี `amount_max` (ช่วงราคา, G2), ask ที่ `rejected` ทุกกรณีนับใน `excluded` แยกตามเหตุ
- **สกุลเงิน** เทียบภายในสกุลเดียว ไม่แปลงค่า

## 3. ช่วงเวลาและค่าสถิติ

ให้ `D` เป็นเวลา 00:00 Asia/Bangkok ของวันที่ขอ ช่วงปัจจุบันคือ `[D − 7 วัน, D)` ช่วงก่อนคือ `[D − 14 วัน, D − 7 วัน)` service รับ `now` จากผู้เรียกเพื่อให้เทสต์กำหนดได้ ใช้กลุ่มชุดเดียวกันในสองช่วง (ระยะ A คือกลุ่มของ read context)

ต่อช่วงคำนวณ `n` (จำนวนข้อเสนอ), `sellers` (จำนวน `seller_id` ที่ไม่เป็น NULL และไม่ซ้ำ ข้อเสนอที่ไม่มีผู้ขายนับใน n แต่ไม่นับในผู้ขาย), `median`, `p25`, `p75` ด้วย `percentile_cont` ช่วงที่ n < 10 หรือ sellers < 3 มี `status = insufficient_data` และไม่แสดงค่าสถิติ

**ทิศทาง** เมื่อทั้งสองช่วงมีข้อมูลพอและ median ช่วงก่อนมากกว่า 0 ให้ `change_pct = 100 × (median_ปัจจุบัน / median_ก่อน − 1)` ปัดสองตำแหน่ง ≥ +5 คือ `up`, ≤ −5 คือ `down` นอกนั้น `flat` งดบอกทิศทาง (`direction = null`) เมื่อข้อมูลไม่พอ หรือเมื่อความครอบคลุมต่างกัน (G3): กลุ่มที่มี `poll_minutes` มีรอบ discover สำเร็จน้อยกว่าครึ่งของจำนวน slot ที่นัดในช่วงใดช่วงหนึ่ง หรือชุดกลุ่มที่มีรอบสำเร็จในสองช่วงไม่เท่ากัน กลุ่มที่ไม่มี `poll_minutes` ไม่นำมาตัดสินความครอบคลุม

ค่าตั้งต้นทั้งหมด (7 วัน, 10 ข้อเสนอ, 3 ผู้ขาย, ±5%) อ่านจาก configuration และคืนใน `settings` ของผลทุกครั้ง

## 4. `GET /v1/prices/trends`

พารามิเตอร์ `model_key` (บังคับ), `variant`, `vram_gb`, `condition`, `unit` (ค่าเริ่มต้น `each`), `currency` (ค่าเริ่มต้น `THB`) ค่าที่ไม่ส่งหมายถึงค่า NULL ของฟิลด์นั้น ไม่ใช่ “ทุกค่า” ไม่มี `model_key` ตอบ 422 ใช้ read context และกติกา error ของ [สัญญา Collector](collector-and-query.md)

```jsonc
{
  "label": "ราคาตั้งขาย",
  "filters": {"model_key": "rtx-3060", "variant": null, "vram_gb": 12, "condition": "used",
              "unit": "each", "currency": "THB"},
  "window": {"timezone": "Asia/Bangkok",
             "current": {"from": "2026-10-31T17:00:00Z", "to": "2026-11-07T17:00:00Z"},
             "previous": {"from": "2026-10-24T17:00:00Z", "to": "2026-10-31T17:00:00Z"}},
  "current": {"status": "ok", "n": 12, "sellers": 5,
              "median": "5800.00", "p25": "5500.00", "p75": "6100.00"},
  "previous": {"status": "insufficient_data", "n": 4, "sellers": 2,
               "median": null, "p25": null, "p75": null},
  "direction": null, "direction_reason": "insufficient_data", "change_pct": null,
  "excluded": {"unclassified": 3, "non_ask": 2, "bundle_or_unknown_unit": 1,
               "range": 1, "unconfirmed": 0},
  "limitations": ["one_offer_per_item"],
  "rule_version": "trend-v1.0",
  "settings": {"window_days": 7, "min_offers": 10, "min_sellers": 3, "flat_band_pct": 5}
}
```

`direction_reason` เป็น `insufficient_data`, `coverage_differs` หรือ null จำนวนเงินเป็นข้อความทศนิยมสองตำแหน่ง UI แสดงว่าเป็นราคาตั้งขายเสมอ ไม่ใช่ราคาที่ขายได้จริง

## 5. เกณฑ์ที่ A7a พิสูจน์

| AT | ส่วนที่ตรวจ |
|---|---|
| AT-10 (ราคา) | poll ซ้ำของชิ้นเดิมไม่เพิ่ม n; ชุดต่ำกว่าเกณฑ์ขึ้น `insufficient_data` |
| AT-07 (การตัดออก) | ค่าส่ง มัดจำ ยอดเสนอซื้อ ราคายกชุด และช่วงราคาไม่เข้าชุดเทียบ และนับใน `excluded` |

ความแม่นยำ ≥95% ของ amount, currency และรายการตาม AT-07 วัดหลังกฎสกัดของ A6 ในงาน A7b
