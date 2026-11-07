# สัญญากฎสกัดรุ่น หน่วยความจำ สภาพ และประกัน (A6a)

**สถานะ** แบบที่ใช้อ้างอิงสำหรับงาน A6a ตาม [Issue #48](https://github.com/Freshair129/paiya/issues/48) · **ผู้ดูแล** เจ้าของ Worker, API และฐานข้อมูล

หน้านี้กำหนดวิธีแปลง `items[].model` และ `items[].condition_text` ของ observation v1 เป็น `model_key`, `vram_gb`, `condition`, `warranty` และ `warranty_until` ที่ย้อนถึงหลักฐานได้ การเก็บผลในตาราง `tags` การสร้างค่าใน `listing_items` และตัวกรองของ `GET /v1/posts` ใช้คู่กับ [SRS](../../requirements/SRS.md) ข้อ 1 (Enrichment), FR-09, FR-10, FR-17 (ข้อความประกัน), AT-06, AT-12, AT-14, [SPEC](../SPEC.md) ข้อ 4–5, [แบบจำลองข้อมูล](../data-model.md) และ [สัญญาสถานะรายชิ้น](item-status-and-watches.md) ข้อ 2

งานเสริมด้วยโมเดลในเครื่อง (A6b) อยู่ใน [สัญญางานเสริมข้อมูล](local-model-enrichment.md) และ MCP (A6c) จะมีสัญญาเมื่อเริ่มงานนั้น ผลจากโมเดลต้องไม่เปลี่ยนค่าที่สร้างตามหน้านี้จนกว่าจะผ่านกฎยืนยัน

## 1. ขอบเขตของกฎ `extract-v1.0`

- อ่านเฉพาะ `items[].model` (หา `model_key` และ `vram_gb`) และ `items[].condition_text` (หา `condition`, `warranty` และ `warranty_until`) ของ item ที่มี `source_item_key` ไม่อ่านข้อความอิสระในโพสต์หรือคอมเมนต์ ซึ่งเป็นงานเสนอของ A6b
- ไม่จัดชนิดราคาใหม่ `price_kind` มาจาก adapter ตาม observation v1 (ดู Issue #48 ข้อ E2)
- `variant` ยังไม่สกัดในรุ่นนี้และคงเป็น NULL รุ่นย่อยอย่าง Ti, Super, XT และ XTX เป็นส่วนของ `model_key` เพราะเป็นคนละชิป
- ค่าที่ไม่พบในข้อความเป็น NULL ไม่ใช่ศูนย์หรือค่าเดา

## 2. การเทียบข้อความ

ก่อนเทียบ ทำ Unicode NFKC, แปลงเป็นตัวพิมพ์เล็ก และยุบช่องว่างต่อเนื่องเป็นช่องเดียว ทั้งกับข้อความของ item และ `source_span.quote` ของหลักฐาน

**รุ่น** หาโทเคนตามรูปแบบ `[geforce ][rtx|gtx|rx|radeon rx ]<เลข 4 หลัก>[ ][ti super|ti|super|xtx|xt]` ซึ่งต้องไม่ติดกับตัวอักษรหรือตัวเลขอื่นทั้งหน้าและหลัง ช่องว่างระหว่างส่วนมีหรือไม่มีก็ได้ เช่น `RTX 3060`, `rtx3060`, `3060ti`, `GeForce RTX 4070 Ti Super`

- เลขที่ไม่อยู่ในตารางข้อ 3 ไม่นับเป็นโทเคน (กันตัวเลขอื่นอย่างราคา)
- เลขที่อยู่ในตารางแต่ส่วนต่อท้ายไม่มีในตาราง เช่น `3090 super` ได้ผล `unknown_model`
- คำนำหน้าที่ระบุต้องตรงตระกูลในตาราง เช่น `gtx 3060` ได้ผล `unknown_model` ไม่ระบุคำนำหน้าใช้ตระกูลจากตาราง
- พบมากกว่าหนึ่ง `model_key` ที่ต่างกันใน item เดียว ได้ผล `ambiguous_model` โทเคนซ้ำของรุ่นเดียวกันนับเป็นหนึ่ง

**หน่วยความจำ** หาเลข 1–2 หลักที่ตามด้วย `gb`, `g`, `กิ๊ก` หรือ `กิก` (มีช่องว่างคั่นได้) และไม่ติดตัวอักษรละตินต่อท้าย เช่น `12GB`, `12 g`, `8กิ๊ก`

- ตรวจได้เฉพาะเมื่อข้อความรุ่นของ item เดียวกันได้ `model_key` ค่าเดียวตามข้อนี้ (ไม่ว่างและไม่ได้ `ambiguous_model` หรือ `unknown_model`) มิฉะนั้นได้ `model_unknown` จากนั้นหน่วยความจำผ่านการตรวจหลักฐานของข้อ 4 แยกจากรุ่น ค่าใน `listing_items` ใช้หน่วยความจำได้เฉพาะจาก observation ที่รุ่น `confirmed` ตามข้อ 6
- ค่าที่ไม่อยู่ในชุดหน่วยความจำของรุ่นนั้นตามข้อ 3 ได้ `vram_not_valid_for_model`
- พบหลายค่าที่ต่างกัน ได้ `ambiguous_vram`
- ข้อความระบุเพียง “3060” โดยไม่มีหน่วยความจำ ให้ `vram_gb` เป็น NULL ตาม FR-15

**สภาพ** ค้นใน `condition_text` ตามลำดับ

1. คำของ `used`: `มือ2`, `มือ 2`, `มือสอง`, `เหมือนใหม่`, `like new`, `used`, `second hand`, `ผ่านการใช้งาน` ตัดช่วงที่ตรงออกจากข้อความก่อนขั้นถัดไป เพื่อไม่ให้ `ใหม่` หรือ `new` ในคำเหล่านี้นับเป็นของใหม่
2. คำของ `new`: `มือ1`, `มือ 1`, `มือหนึ่ง`, `ของใหม่`, `ใหม่แกะกล่อง`, `ยังไม่แกะ`, `ซีล`, `new` (ทั้งคำ)
3. คำของ `unused` (ไม่เคยใช้งานแต่อาจเก็บไว้นาน): `ไม่เคยใช้งาน`, `ไม่เคยใช้`, `ไม่เคยลงเครื่อง`, `ไม่เคยเสียบไฟ`, `เก่าเก็บ`, `ค้างสต็อก`
4. พบคำ `used` ร่วมกับคำ `new` หรือ `unused` ได้ `ambiguous_condition` ไม่พบกลุ่มใด ไม่สร้าง tag สภาพ เช่น `ใช้งานได้`
5. ค่าของ tag สภาพมีสามค่า
   - `used`: พบเฉพาะคำ `used`
   - `new_no_warranty`: พบคำ `new` หรือ `unused` และ tag `warranty` ของ item เดียวกันใน observation เดียวกันเป็น `none` ที่ `confirmed` เช่น การ์ดที่ไม่เคยใช้หรือไม่เคยโดนไฟแต่เก็บจนหมดประกัน
   - `new`: พบคำ `new` หรือ `unused` และไม่เข้าข้อก่อนหน้า (มีประกันหรือไม่ได้ระบุเรื่องประกัน)

**ประกัน** ค้นใน `condition_text` หลังทำข้อ 2

1. คำของ `none`: `ไม่มีประกัน`, `หมดประกัน`, `ประกันหมด`, `no warranty`, `out of warranty` ตัดช่วงที่ตรงออกก่อนขั้นถัดไป เพื่อไม่ให้ `ประกัน` ในคำเหล่านี้นับว่ามีประกัน
2. คำของ `active`: `มีประกัน`, `ติดประกัน`, `ประกันเหลือ`, `ประกันถึง`, `ประกันศูนย์`, `ประกันร้าน`, `warranty`
3. พบทั้งสองกลุ่ม ได้ `ambiguous_warranty` ไม่พบกลุ่มใด ไม่สร้าง tag ประกัน
4. `warranty_until` (เดือนที่ประกันสิ้นสุด รูปแบบ `YYYY-MM`) หาเมื่อได้ `active` เท่านั้น จากรูปแบบใดรูปแบบหนึ่ง
   - `เหลือ N เดือน` หรือ `เหลือ N ปี` (N เป็นจำนวนเต็ม 1–120 เดือน หรือ 1–10 ปี) นับจากเดือนของเวลาฐาน เวลาฐานคือ `posted_at.value` เมื่อ precision เป็น `exact`, `minute`, `hour` หรือ `day` (ค่าใน observation v1) นอกนั้นหรือไม่มีค่าใช้ `observed_at` ทั้งสองแปลงเป็นวันตามเวลา Asia/Bangkok ตัวอย่าง โพสต์ 15 พ.ย. 2026 เหลือ 8 เดือน ได้ `2027-07`
   - `ถึง MM/YYYY`, `ถึง MM/YY` หรือ `ถึง <ชื่อเดือนไทยเต็มหรือย่อ> <ปี>` ปี ≥ 2500 เป็นพุทธศักราชให้ลบ 543 ปีสองหลักหมายถึง 20YY หรือ 25YY ตามที่ได้ค่าใกล้เวลาฐานที่สุด
   - พบหลายค่าที่ต่างกัน ได้ `ambiguous_warranty_until` เดือนที่ไม่มีจริง ได้ `warranty_date_invalid` ไม่พบรูปแบบ ไม่สร้าง tag `warranty_until` และ `warranty` ยังเป็น `active`
   - เดือนสิ้นสุดก่อนเดือนของเวลาฐาน ให้ tag `warranty` เป็น `none` (หมดประกันแล้วตามข้อความ) และ tag `warranty_until` เก็บเดือนนั้นตามที่ระบุ

## 3. ตารางรุ่น `extract-v1.0`

`model_key` ใช้รูปแบบ `<ตระกูล>-<เลข>[-<ส่วนต่อท้าย>]` ตัวพิมพ์เล็ก การเพิ่มหรือแก้แถวต้องออก `rule_version` ใหม่และแก้ตารางนี้ใน PR เดียวกับโค้ด

| ตระกูล | เลขและส่วนต่อท้าย → ชุดหน่วยความจำ (GB) |
|---|---|
| `gtx` | 1650 → 4 · 1660 → 6 · 1660 super → 6 · 1660 ti → 6 |
| `rtx` 20 | 2060 → 6, 12 · 2060 super → 8 · 2070 → 8 · 2070 super → 8 · 2080 → 8 · 2080 super → 8 · 2080 ti → 11 |
| `rtx` 30 | 3050 → 6, 8 · 3060 → 8, 12 · 3060 ti → 8 · 3070 → 8 · 3070 ti → 8 · 3080 → 10, 12 · 3080 ti → 12 · 3090 → 24 · 3090 ti → 24 |
| `rtx` 40 | 4060 → 8 · 4060 ti → 8, 16 · 4070 → 12 · 4070 super → 12 · 4070 ti → 12 · 4070 ti super → 16 · 4080 → 16 · 4080 super → 16 · 4090 → 24 |
| `rx` | 6600 → 8 · 6600 xt → 8 · 6650 xt → 8 · 6700 xt → 12 · 6750 xt → 12 · 6800 → 16 · 6800 xt → 16 · 6900 xt → 16 · 7600 → 8 · 7700 xt → 12 · 7800 xt → 16 · 7900 xt → 20 · 7900 xtx → 24 |

ตัวอย่าง `RTX 3060 Ti` → `rtx-3060-ti`, `4070 Ti Super` → `rtx-4070-ti-super`, `RX 7900 XTX` → `rx-7900-xtx`

## 4. หลักฐานและผลตรวจ

ทุกค่าที่ได้จากข้อ 2 (รวมกรณีที่ได้ผลปฏิเสธ) สร้าง tag หนึ่งแถวต่อ observation ต่อชิ้นต่อ `key` item ที่ไม่มี `evidence_keys` ไม่สร้าง tag

ไล่ `evidence_keys` ของ item ตามลำดับ หลักฐานแรกที่ผ่านเกณฑ์หลักฐานของ [สัญญาสถานะรายชิ้น](item-status-and-watches.md) ข้อ 2 **และ** เมื่อใช้วิธีเทียบของข้อ 2 กับ `source_span.quote` แล้วพบค่าเดียวกัน (โทเคนที่ได้ `model_key` เดียวกัน เลขหน่วยความจำเดียวกัน คำในกลุ่มสภาพหรือประกันเดียวกัน หรือข้อความระยะเวลา/วันที่ที่ได้เดือนสิ้นสุดเดียวกัน) เป็นหลักฐานของ tag เช่น `rtx3060` ใน item ตรงกับ `RTX 3060` ใน quote ส่วนหลักฐานที่ไม่มี `source_span` ไม่ผ่านข้อนี้

| ผล | `validation_result` | `value` | `reason` |
|---|---|---|---|
| ได้ค่าเดียวและมีหลักฐานผ่าน | `confirmed` | ค่าที่สกัด | NULL |
| ได้ค่าเดียวแต่ไม่มีหลักฐานผ่าน | `rejected` ผูกหลักฐานตัวแรกของ item | ค่าที่สกัด | `quote_mismatch` เมื่อมีหลักฐานที่ผ่านเกณฑ์บทบาทแต่ไม่มีข้อความนั้น นอกนั้นใช้เหตุแรกที่ตรงตามลำดับ `role_mismatch`, `actor_mismatch`, `seller_unknown`, `no_eligible_evidence` เหมือนสถานะ |
| ข้อความกำกวมหรือไม่อยู่ในตาราง | `rejected` ผูกหลักฐานตัวแรกของ item | NULL | `ambiguous_model`, `unknown_model`, `ambiguous_vram`, `vram_not_valid_for_model`, `model_unknown`, `ambiguous_condition`, `ambiguous_warranty`, `ambiguous_warranty_until` หรือ `warranty_date_invalid` |

ค่าของ `key` คือ `model_key`, `vram_gb` (เก็บเป็นข้อความของเลขจำนวนเต็ม), `condition` (`new`, `new_no_warranty` หรือ `used`), `warranty` (`active` หรือ `none`) และ `warranty_until` (`YYYY-MM`) สกัด `warranty` ก่อน `condition` เพราะค่า `new_no_warranty` อาศัยผลประกัน ทุกแถวมี `method = rule`, `rule_version = extract-v1.0`, `model_version` และ `confidence` เป็น NULL

## 5. ตาราง tags (migration 0007)

| คอลัมน์ | ข้อบังคับ |
|---|---|
| `id uuid` | PK |
| `observation_id`, `post_id` | อ้าง observation ของโพสต์เดียวกันแบบ composite เหมือน `price_facts` |
| `item_id?` | อ้าง `listing_items(id, post_id)` A6a มีค่าเสมอ |
| `evidence_id` | อ้าง evidence ของ observation และโพสต์เดียวกัน |
| `key text` | CHECK ใน `model_key`, `vram_gb`, `condition`, `warranty`, `warranty_until` |
| `value text?` | NOT NULL เมื่อ `confirmed` |
| `method text` | CHECK ใน `rule` (A6b เพิ่มค่าเมื่อเริ่มงาน) |
| `model_version text?`, `confidence numeric?` | NULL สำหรับ `rule` |
| `rule_version text` | NOT NULL |
| `validation_result text` | CHECK ใน `confirmed`, `rejected` |
| `reason text?` | NULL เมื่อ `confirmed` เท่านั้น และอยู่ในชุดเหตุของข้อ 4 |
| `created_at` | เวลาบันทึก |

migration เดียวกันเพิ่ม `listing_items.warranty text?` (CHECK ใน `active`, `none`) และ `listing_items.warranty_until date?` (วันที่ 1 ของเดือน) ขยาย CHECK ของ `listing_items.condition` ถ้ามี ให้รับ `new_no_warranty`

UNIQUE(`observation_id`, `item_id`, `key`, `method`, `rule_version`) ทำให้ ingest หรือ rebuild ซ้ำไม่เพิ่มแถว migration ไม่สกัดข้อมูลเดิมเพราะ migration เป็น snapshot ที่ไม่ import โค้ดกฎ ข้อมูลเดิมเติมด้วยคำสั่งในข้อ 6

## 6. ค่าใน listing_items และการสร้างใหม่

ทำในธุรกรรมเดียวกับ ingest หลังบันทึก price/status facts ล็อกแถว `listing_items` ของชิ้นที่ได้ tag ใหม่ แล้วคำนวณจาก tag `confirmed` ของทุก observation ของชิ้นนั้น เรียง `(observed_at, observation_id)`

- `model_key`, `condition` และ `warranty` ใช้ค่าจาก observation ล่าสุดที่มี tag `confirmed` ของ key นั้น
- `warranty_until` ใช้ค่าจาก observation ล่าสุดที่มี tag `confirmed` ของ `warranty_until` **และ** tag `warranty` ที่ `confirmed` ใน observation เดียวกันเท่ากับ `warranty` ที่เลือก ไม่มีให้เป็น NULL
- `vram_gb` ใช้ค่าจาก observation ล่าสุดที่มี tag `confirmed` ของ `vram_gb` **และ** tag `model_key` ที่ `confirmed` ใน observation เดียวกันเท่ากับ `model_key` ที่เลือก ไม่มีให้เป็น NULL
- observation ใหม่ที่ไม่ระบุค่าหรือได้ผลปฏิเสธไม่ล้างค่าเดิม observation เก่าที่มาถึงทีหลังเติมได้เพียงประวัติ ไม่ทับค่าจาก observation ที่ใหม่กว่า
- ค่าที่สร้างนี้เป็นข้อมูลอนุพันธ์ งานลบของ A8 ต้องคำนวณใหม่จาก tag ที่เหลือ

`uv run python -m paiya.cli extract rebuild` สกัด observation ทั้งหมดที่ยังไม่มี tag ของ `extract-v1.0` แล้วคำนวณค่าของทุกชิ้นใหม่ คำสั่งเพิ่มแถวที่ขาดด้วย `ON CONFLICT DO NOTHING` และไม่ลบแถว รันซ้ำได้ผลเท่าเดิม และพิมพ์จำนวน observation, tag ที่เพิ่ม และชิ้นที่เปลี่ยนค่า

## 7. ตัวกรองและผลของ GET /v1/posts

เพิ่มพารามิเตอร์ `model_key` (ข้อความไม่ว่าง), `vram_gb` (จำนวนเต็ม 1–99) `condition` (`new`, `new_no_warranty` หรือ `used`) และ `warranty` (`active` หรือ `none`) ต่อจาก [สัญญาเส้นทางมือถือ](mobile-read-path.md) ข้อ 2 ค่าที่ผิดรูปตอบ 422 โพสต์ตรงเมื่อมีชิ้นใดชิ้นหนึ่งตรงทุกพารามิเตอร์ที่ส่ง รวมกับ `sale_status` และราคาบนชิ้นเดียวกัน พารามิเตอร์ที่ไม่ส่งไม่กรอง

ทุกชิ้นใน `GET /v1/posts` และ `GET /v1/posts/{id}` เพิ่มฟิลด์ `model_key`, `vram_gb` (number หรือ null), `condition`, `warranty`, `warranty_until` (`YYYY-MM` หรือ null) และ `warranty_months_left` จาก `listing_items` โดย `warranty_months_left` คำนวณตอนอ่านเป็นจำนวนเดือนจากเดือนปัจจุบันตามเวลา Asia/Bangkok ถึง `warranty_until` (เดือนเดียวกันได้ 0) เป็น null เมื่อ `warranty_until` เป็น null และไม่ติดลบ เมื่อเลยเดือนสิ้นสุดแล้วให้เป็น 0 ส่วน `warranty` คงค่าตามหลักฐาน ฟิลด์เดิม `model` (ข้อความดิบ) คงอยู่ ข้อมูลที่อ่านได้ยังอยู่ใน read context เดิม

**`GET /v1/models`** (A6d) คืนรุ่นที่มีชิ้นซึ่งผู้อ่านเห็น `model_key` ได้ตามกติกาการอ่านของข้อนี้ ใน read context เดียวกับ `GET /v1/posts` เรียงตาม `model_key` ใช้เติมตัวเลือกรุ่นในหน้าแรก ไม่มีพารามิเตอร์ ตอบ `Cache-Control: no-store` เหมือน endpoint อื่น

```json
{"models": [{"model_key": "rtx-3060", "item_count": 2}, {"model_key": "rtx-3070", "item_count": 1}]}
```

`item_count` นับชิ้นที่ผู้อ่านเห็นค่านั้น ไม่นับชิ้นที่ค่าถูกซ่อนเพราะ tag ล่าสุดมาจาก grant อื่น

## 8. ตัวอย่างสำหรับเทสต์

| `model` / `condition_text` | quote ของหลักฐาน seller | ผล |
|---|---|---|
| `RTX 3060 12GB` / `ใช้งานได้` | `RTX 3060 12GB ราคา 5,500 บาท` | `rtx-3060` และ `12` confirmed ไม่มี tag สภาพ |
| `3060` / `มือสอง` | `3060 มือสอง` | `rtx-3060` confirmed, `used` confirmed, ไม่มี tag vram |
| `RTX 3060 Ti 12GB` | `RTX 3060 Ti 12GB` | `rtx-3060-ti` confirmed, vram rejected `vram_not_valid_for_model` |
| `3060 / 3070` | `3060 / 3070` | model rejected `ambiguous_model` value NULL, vram ไม่มี tag หรือ `model_unknown` ถ้าระบุหน่วยความจำ |
| `RTX 3070 8GB` | `การ์ดจอสภาพดี` | model และ vram rejected `quote_mismatch` |
| `RTX 3070 8GB` หลักฐานเป็นคอมเมนต์ผู้ซื้อ | `3070 8GB` | rejected ด้วยเหตุตามเกณฑ์สถานะ เช่น `role_mismatch` |
| `สภาพเหมือนใหม่` เป็น `condition_text` | มีคำนั้น | `used` confirmed |
| `มือ1 ประกันเหลือ 8 เดือน` โพสต์ 2026-11-15 (exact) | มีข้อความนั้น | `warranty` `active`, `warranty_until` `2027-07`, `condition` `new` |
| `ไม่เคยใช้งาน เก่าเก็บ หมดประกัน` | มีข้อความนั้น | `warranty` `none`, `condition` `new_no_warranty` |
| `ซีล ไม่ได้บอกประกัน` | `ซีล` | `condition` `new` ไม่มี tag ประกัน |
| `มือสอง ประกันถึง 03/2570` โพสต์ 2026-11-15 | มีข้อความนั้น | `warranty` `active`, `warranty_until` `2027-03`, `condition` `used` |
| `มือสอง ประกันถึง 01/2026` โพสต์ 2026-11-15 | มีข้อความนั้น | `warranty` `none`, `warranty_until` `2026-01`, `condition` `used` |
| `มีประกัน ไม่มีประกัน` | มีข้อความนั้น | `warranty` rejected `ambiguous_warranty` |
