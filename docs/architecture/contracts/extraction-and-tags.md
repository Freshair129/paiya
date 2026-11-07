# สัญญากฎสกัดรุ่น หน่วยความจำ และสภาพ (A6a)

**สถานะ** แบบที่ใช้อ้างอิงสำหรับงาน A6a ตาม [Issue #48](https://github.com/Freshair129/paiya/issues/48) · **ผู้ดูแล** เจ้าของ Worker, API และฐานข้อมูล

หน้านี้กำหนดวิธีแปลง `items[].model` และ `items[].condition_text` ของ observation v1 เป็น `model_key`, `vram_gb` และ `condition` ที่ย้อนถึงหลักฐานได้ การเก็บผลในตาราง `tags` การสร้างค่าใน `listing_items` และตัวกรองของ `GET /v1/posts` ใช้คู่กับ [SRS](../../requirements/SRS.md) ข้อ 1 (Enrichment), FR-09, FR-10, AT-06, AT-12, [SPEC](../SPEC.md) ข้อ 4–5, [แบบจำลองข้อมูล](../data-model.md) และ [สัญญาสถานะรายชิ้น](item-status-and-watches.md) ข้อ 2

งานเสริมด้วยโมเดลในเครื่อง (A6b) และ MCP (A6c) จะเพิ่มหัวข้อในหน้านี้หรือสัญญาใหม่เมื่อเริ่มงานนั้น ผลจากโมเดลต้องไม่เปลี่ยนค่าที่สร้างตามหน้านี้จนกว่าจะผ่านกฎยืนยัน

## 1. ขอบเขตของกฎ `extract-v1.0`

- อ่านเฉพาะ `items[].model` (หา `model_key` และ `vram_gb`) และ `items[].condition_text` (หา `condition`) ของ item ที่มี `source_item_key` ไม่อ่านข้อความอิสระในโพสต์หรือคอมเมนต์ ซึ่งเป็นงานเสนอของ A6b
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

- ยืนยันได้เฉพาะเมื่อ `model_key` ของ item เดียวกันใน observation เดียวกันได้ผล `confirmed` มิฉะนั้นได้ `model_unknown`
- ค่าที่ไม่อยู่ในชุดหน่วยความจำของรุ่นนั้นตามข้อ 3 ได้ `vram_not_valid_for_model`
- พบหลายค่าที่ต่างกัน ได้ `ambiguous_vram`
- ข้อความระบุเพียง “3060” โดยไม่มีหน่วยความจำ ให้ `vram_gb` เป็น NULL ตาม FR-15

**สภาพ** ค้นใน `condition_text` ตามลำดับ

1. คำของ `used`: `มือ2`, `มือ 2`, `มือสอง`, `เหมือนใหม่`, `like new`, `used`, `second hand`, `ผ่านการใช้งาน` ตัดช่วงที่ตรงออกจากข้อความก่อนขั้นถัดไป เพื่อไม่ให้ `ใหม่` หรือ `new` ในคำเหล่านี้นับเป็นของใหม่
2. คำของ `new`: `มือ1`, `มือ 1`, `มือหนึ่ง`, `ของใหม่`, `ใหม่แกะกล่อง`, `ยังไม่แกะ`, `ซีล`, `new` (ทั้งคำ)
3. พบทั้งสองกลุ่ม ได้ `ambiguous_condition` ไม่พบกลุ่มใด ไม่สร้าง tag เช่น `ใช้งานได้`

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

ไล่ `evidence_keys` ของ item ตามลำดับ หลักฐานแรกที่ผ่านเกณฑ์หลักฐานของ [สัญญาสถานะรายชิ้น](item-status-and-watches.md) ข้อ 2 **และ** เมื่อใช้วิธีเทียบของข้อ 2 กับ `source_span.quote` แล้วพบค่าเดียวกัน (โทเคนที่ได้ `model_key` เดียวกัน เลขหน่วยความจำเดียวกัน หรือคำในกลุ่มสภาพเดียวกัน) เป็นหลักฐานของ tag เช่น `rtx3060` ใน item ตรงกับ `RTX 3060` ใน quote ส่วนหลักฐานที่ไม่มี `source_span` ไม่ผ่านข้อนี้

| ผล | `validation_result` | `value` | `reason` |
|---|---|---|---|
| ได้ค่าเดียวและมีหลักฐานผ่าน | `confirmed` | ค่าที่สกัด | NULL |
| ได้ค่าเดียวแต่ไม่มีหลักฐานผ่าน | `rejected` ผูกหลักฐานตัวแรกของ item | ค่าที่สกัด | `quote_mismatch` เมื่อมีหลักฐานที่ผ่านเกณฑ์บทบาทแต่ไม่มีข้อความนั้น นอกนั้นใช้เหตุแรกที่ตรงตามลำดับ `role_mismatch`, `actor_mismatch`, `seller_unknown`, `no_eligible_evidence` เหมือนสถานะ |
| ข้อความกำกวมหรือไม่อยู่ในตาราง | `rejected` ผูกหลักฐานตัวแรกของ item | NULL | `ambiguous_model`, `unknown_model`, `ambiguous_vram`, `vram_not_valid_for_model`, `model_unknown` หรือ `ambiguous_condition` |

ค่าของ `key` คือ `model_key`, `vram_gb` (เก็บเป็นข้อความของเลขจำนวนเต็ม) และ `condition` (`new` หรือ `used`) ทุกแถวมี `method = rule`, `rule_version = extract-v1.0`, `model_version` และ `confidence` เป็น NULL

## 5. ตาราง tags (migration 0007)

| คอลัมน์ | ข้อบังคับ |
|---|---|
| `id uuid` | PK |
| `observation_id`, `post_id` | อ้าง observation ของโพสต์เดียวกันแบบ composite เหมือน `price_facts` |
| `item_id?` | อ้าง `listing_items(id, post_id)` A6a มีค่าเสมอ |
| `evidence_id` | อ้าง evidence ของ observation และโพสต์เดียวกัน |
| `key text` | CHECK ใน `model_key`, `vram_gb`, `condition` |
| `value text?` | NOT NULL เมื่อ `confirmed` |
| `method text` | CHECK ใน `rule` (A6b เพิ่มค่าเมื่อเริ่มงาน) |
| `model_version text?`, `confidence numeric?` | NULL สำหรับ `rule` |
| `rule_version text` | NOT NULL |
| `validation_result text` | CHECK ใน `confirmed`, `rejected` |
| `reason text?` | NULL เมื่อ `confirmed` เท่านั้น และอยู่ในชุดเหตุของข้อ 4 |
| `created_at` | เวลาบันทึก |

UNIQUE(`observation_id`, `item_id`, `key`, `method`, `rule_version`) ทำให้ ingest หรือ rebuild ซ้ำไม่เพิ่มแถว migration ไม่สกัดข้อมูลเดิมเพราะ migration เป็น snapshot ที่ไม่ import โค้ดกฎ ข้อมูลเดิมเติมด้วยคำสั่งในข้อ 6

## 6. ค่าใน listing_items และการสร้างใหม่

ทำในธุรกรรมเดียวกับ ingest หลังบันทึก price/status facts ล็อกแถว `listing_items` ของชิ้นที่ได้ tag ใหม่ แล้วคำนวณจาก tag `confirmed` ของทุก observation ของชิ้นนั้น เรียง `(observed_at, observation_id)`

- `model_key` และ `condition` ใช้ค่าจาก observation ล่าสุดที่มี tag `confirmed` ของ key นั้น
- `vram_gb` ใช้ค่าจาก observation ล่าสุดที่มี tag `confirmed` ของ `vram_gb` **และ** tag `model_key` ที่ `confirmed` ใน observation เดียวกันเท่ากับ `model_key` ที่เลือก ไม่มีให้เป็น NULL
- observation ใหม่ที่ไม่ระบุค่าหรือได้ผลปฏิเสธไม่ล้างค่าเดิม observation เก่าที่มาถึงทีหลังเติมได้เพียงประวัติ ไม่ทับค่าจาก observation ที่ใหม่กว่า
- ค่าที่สร้างนี้เป็นข้อมูลอนุพันธ์ งานลบของ A8 ต้องคำนวณใหม่จาก tag ที่เหลือ

`uv run python -m paiya.cli extract rebuild` สกัด observation ทั้งหมดที่ยังไม่มี tag ของ `extract-v1.0` แล้วคำนวณค่าของทุกชิ้นใหม่ คำสั่งเพิ่มแถวที่ขาดด้วย `ON CONFLICT DO NOTHING` และไม่ลบแถว รันซ้ำได้ผลเท่าเดิม และพิมพ์จำนวน observation, tag ที่เพิ่ม และชิ้นที่เปลี่ยนค่า

## 7. ตัวกรองและผลของ GET /v1/posts

เพิ่มพารามิเตอร์ `model_key` (ข้อความไม่ว่าง), `vram_gb` (จำนวนเต็ม 1–99) และ `condition` (`new` หรือ `used`) ต่อจาก [สัญญาเส้นทางมือถือ](mobile-read-path.md) ข้อ 2 ค่าที่ผิดรูปตอบ 422 โพสต์ตรงเมื่อมีชิ้นใดชิ้นหนึ่งตรงทุกพารามิเตอร์ที่ส่ง รวมกับ `sale_status` และราคาบนชิ้นเดียวกัน พารามิเตอร์ที่ไม่ส่งไม่กรอง

ทุกชิ้นใน `GET /v1/posts` และ `GET /v1/posts/{id}` เพิ่มฟิลด์ `model_key`, `vram_gb` (number หรือ null) และ `condition` จาก `listing_items` ฟิลด์เดิม `model` (ข้อความดิบ) คงอยู่ ข้อมูลที่อ่านได้ยังอยู่ใน read context เดิม

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
