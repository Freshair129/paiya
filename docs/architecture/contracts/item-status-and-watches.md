# สัญญาสถานะรายชิ้น ประวัติผู้ขาย และการติดตาม (ระยะ A)

**สถานะ** แบบที่ใช้อ้างอิงสำหรับงาน A4 ตามคำตัดสิน E1–E6 ใน [Issue #26](https://github.com/Freshair129/paiya/issues/26) · **ผู้ดูแล** เจ้าของ Worker, API และฐานข้อมูล

หน้านี้กำหนดวิธีสร้างข้อเท็จจริงราคาและสถานะจาก observation v1, การสร้าง `sale_events` และ `current_items`, timeline, หน้าผู้ขาย และงานตรวจ permalink ใช้คู่กับ [SRS](../../requirements/SRS.md) ข้อ 3–4, FR-04 ถึง FR-08, [แบบจำลองข้อมูล](../data-model.md) ข้อ 4–5, [สัญญา Collector](collector-and-query.md) และ [สัญญาคิวค้นโพสต์](discovery-queue.md)

## 1. ข้อเท็จจริงราคา

ทุก item ที่มี `source_item_key` และ `price` สร้าง `price_facts` หนึ่งแถวต่อ observation `fact_key = price:<source_item_key>:<kind>:<unit>` (รวมชิ้นตามที่ data-model ข้อ 4 กำหนด เพื่อให้ไม่ซ้ำภายใน observation ที่มีหลายชิ้น) ใช้หลักฐานตัวแรกใน `evidence_keys` ถ้า item ไม่มี `evidence_keys` ไม่สร้าง price fact เพราะ `evidence_id` บังคับมีค่า ค่าดิบยังอยู่ใน `normalized_json` (A4 ไม่แยกค่าส่งหรือมัดจำจากข้อความ ซึ่งเป็นงาน A6/A7)

## 2. ข้อเท็จจริงสถานะ

**ตารางป้ายสถานะ** (`rule_version = status-v1.0`) ใช้กับ `items[].source_status_label` เทียบแบบตรงทั้งข้อความหลังตัดช่องว่างหัวท้าย

| ป้าย | `sale_status` |
|---|---|
| เปิดขาย | `open` |
| ติดจอง | `reserved` |
| ขายแล้ว | `sold` |

ป้ายอื่นหรือไม่มีป้ายไม่สร้าง status fact ระบบไม่แปลความหมายจากข้อความอิสระในโพสต์หรือคอมเมนต์ งานนั้นเป็นกฎสกัดของ A6 การเพิ่มป้ายต้องออก `rule_version` ใหม่และแก้ตารางนี้

**หลักฐานที่ใช้ยืนยันได้** ไล่ `evidence_keys` ของ item ตามลำดับ หลักฐานแรกที่ผ่านข้อใดข้อหนึ่งเป็นหลักฐานของ fact

1. `role = source_label`
2. `role = seller` และ `source_span.field = post_text`
3. `role = seller` และ `source_span.field = comment_text` โดยคอมเมนต์ที่อ้างมี `role = seller` และ `actor_key` เท่ากับ `seller.external_actor_key` ของโพสต์ซึ่งต้องไม่เป็น null

| ผล | `validation_result` | `reason` |
|---|---|---|
| มีป้ายในตารางและมีหลักฐานผ่าน | `confirmed` | NULL |
| มีป้ายในตารางแต่ไม่มีหลักฐานผ่าน | `rejected` ผูกหลักฐานตัวแรกของ item | `role_mismatch` (role ของหลักฐานต่างจากคอมเมนต์), `actor_mismatch` (ผู้พูดไม่ใช่ผู้ขายของโพสต์), `seller_unknown` (โพสต์ไม่มีผู้ขายที่มี key) หรือ `no_eligible_evidence` เลือกเหตุแรกตามลำดับนี้ที่ตรง |
| item ไม่มี `evidence_keys` | ไม่สร้าง fact | — |

`fact_key = status:label` ซึ่งไม่ซ้ำเพราะ UNIQUE ของ status_facts รวม `item_id` อยู่แล้ว จึงมีหนึ่ง fact ต่อชิ้นต่อ observation `effective_at`/`precision` มาจาก `source_time` ของคอมเมนต์เมื่อหลักฐานเป็น `comment_text` นอกนั้นเป็น NULL กับ `unknown` envelope ที่ `access` ไม่ใช่ `ok` ไม่มี item จึงไม่สร้าง fact และไม่เปลี่ยนสถานะเดิม

## 3. การสร้าง sale_events และ current_items

ทำในธุรกรรมเดียวกับ ingest ของ observation นั้น lock แถว `current_items` ของแต่ละชิ้นที่ได้ fact ใหม่ (สร้างแถวถ้ายังไม่มี) แล้วสร้าง `sale_events` ของชิ้นใหม่ทั้งชุดจาก fact ที่ `confirmed` เรียงตาม `(COALESCE(effective_at, observed_at), observed_at, observation_id)` event เกิดเมื่อสถานะต่างจาก fact confirmed ก่อนหน้า หรือเป็น fact แรก `cause_key = status:<evidence_id>` `seq` เริ่มที่ 1

| คอลัมน์ `current_items` | ค่า |
|---|---|
| `status_event_id` | event สุดท้ายตามลำดับ (สถานะปัจจุบัน) |
| `status_fact_id` | fact confirmed สุดท้ายตามลำดับ ใช้เป็น “หลักฐานสถานะล่าสุด” แม้สถานะไม่เปลี่ยน เช่น เฉลย `status_evidence` ของ S03 |
| `last_observation_id`, `last_success_at` | observation ล่าสุดตาม `observed_at` ที่มีชิ้นนี้ |
| `ask_fact_id`, `shipping_fact_id` | price fact ชนิด `ask`/`shipping` ของ observation ล่าสุดที่มีชนิดนั้น |
| `needs_review` | true เมื่อมี fact confirmed สองแถวที่ `effective_at` ไม่เป็น NULL และเท่ากัน แต่สถานะต่างกัน |

ชิ้นที่ไม่มี fact confirmed มีสถานะ `unknown` ช่วง gap ไม่สร้าง event (ข้อ 5 ของสัญญาคิวค้นโพสต์) ชิ้นที่ไม่มี `source_item_key` ไม่สร้างแถวชิ้นสินค้า (E3) แต่หน้าโพสต์แสดง `has_unresolved_items = true` เมื่อ observation ล่าสุดมีรายการเช่นนั้น

## 4. อ่านสถานะและ timeline

`GET /v1/posts/{id}` เพิ่มฟิลด์ระดับโพสต์ `has_unresolved_items` และ `current_items` (เรียงตาม `source_item_key`)

```jsonc
"current_items": [
  {"item_id": "…", "source_item_key": "sku3060", "sale_status": "reserved",
   "status_since": "2026-11-07T09:30:00Z", "status_evidence_id": "…",
   "last_success_at": "2026-11-07T10:00:00Z", "needs_review": false,
   "ask": {"amount": "5500.00", "currency": "THB", "unit": "each", "evidence_id": "…"}}
]
```

`status_since` คือ `COALESCE(effective_at, observed_at)` ของ event ปัจจุบัน `ask` เป็น null เมื่อไม่มี ask fact

`GET /v1/posts/{id}/timeline?limit=50&cursor=<opaque>` (สูงสุด 100) คำนวณจากตาราง ณ เวลาอ่าน เรียงเวลาเก่าไปใหม่ด้วย `(at, kind, id)`

| `kind` | เกิดเมื่อ | ฟิลด์เพิ่ม |
|---|---|---|
| `status_changed` | ทุก `sale_events` | `item_id`, `from`, `to`, `evidence_id` |
| `price_changed` | ask ของชิ้นต่างจาก ask ของ observation ก่อนหน้าที่มี ask (ครั้งแรกไม่นับ) | `item_id`, `from`, `to`, `evidence_id` |
| `comment_added` | version แรกของคอมเมนต์ที่มี key | `comment_id`, `role` |
| `comment_edited` | `body_hash` ต่างจาก version ก่อนหน้าของคอมเมนต์เดียวกัน | `comment_id` |
| `comment_count_changed` | `source_reported_comment_count` ต่างจาก observation ก่อนหน้าที่มีค่า โดยไม่มีคอมเมนต์ใหม่ที่อ่านได้ใน observation นั้น | `from`, `to` |
| `access_changed` | `access` ต่างจาก observation ก่อนหน้า | `from`, `to` |

ทุกแถวมี `id` (คีย์คงที่จากชนิดและแถวต้นทาง), `kind`, `at` (เวลา UTC), `observation_id`

## 5. หน้าผู้ขาย

`GET /v1/sellers/{seller_id}` อ่านเฉพาะโพสต์ใน group ของ read context (E4) ผู้ขายที่ไม่มีโพสต์ใน context ตอบ 404 `not_found`

```jsonc
{"id": "…", "identity_scope": "synthetic-s01", "first_seen_at": "…", "group_ids": ["…"],
 "post_count": 2, "sold_post_count": 1,
 "posts": [{"post_id": "…", "permalink": "…", "first_seen_at": "…", "has_sold_item": true}]}
```

`first_seen_at` คือ `observed_at` แรกของโพสต์ใด ๆ ของผู้ขายใน context `sold_post_count` นับโพสต์ที่มีชิ้นใดมีสถานะปัจจุบัน `sold` ชื่อแสดงผลไม่ใช้รวมตัวตน ผู้ขายสอง key ที่ชื่อเหมือนกันเป็นสองแถว ยังไม่มี offer cluster และการรวมข้ามกลุ่ม

## 6. Watch และงานตรวจ permalink

**จัดการผ่าน service และ CLI** (E5) `uv run python -m paiya.cli watches <add|remove|hot|list> …` ยังไม่มี HTTP สำหรับเขียน `GET /v1/watches` อ่านได้ภายใต้ read context ระยะ A ระดับ Watch ใน A4 คือทั้งโพสต์ (`item_id` เป็น NULL) ส่วน Watch รายชิ้นเป็นงาน M3

| การกระทำ | กติกา |
|---|---|
| add | สร้าง Watch ของโพสต์ (ซ้ำแล้วคืนแถวเดิม) `saved_at = now`, `next_due_at = saved_at + 24 ชม.` |
| hot | ตั้ง `hot_until = now + 24 ชม.` (ตัวเลือกที่ผู้ใช้เปิด ไม่ใช่ค่าเริ่มต้น) Watch ที่ `hot_until > now` ทั้งระบบมีได้ 10 แถว เกินแล้วปฏิเสธด้วย `hot_watch_limit_reached` |
| remove | ลบ Watch และยกเลิกงาน revisit ที่ `queued` ของโพสต์นั้นถ้าไม่มีเหตุอื่นให้ตรวจ |

**การวางงาน revisit** ทำใน `schedule_due` หลังวาง discover ต่อกลุ่มที่เปิดและมี grant ใช้ได้ โพสต์หนึ่งมีงาน revisit ที่ `queued` หรือ `leased` ได้ครั้งละหนึ่งงาน (partial unique index บน `jobs(target_key) WHERE kind = 'revisit' AND state IN ('queued','leased')`) `target_key = <post_id>`, `logical_key = revisit:<post_id>:<due slot>`

| เหตุ | ถึงกำหนดเมื่อ |
|---|---|
| เร่งรอบ | `hot_until > now` ทุก slot 10 นาที |
| โพสต์ใหม่ | ภายใน 24 ชั่วโมงหลัง `observed_at` แรกของโพสต์ ทุก slot ของกลุ่ม และยังมีชิ้นที่ไม่ใช่ `sold`/`withdrawn` |
| รอบช้า | มี Watch, พ้นสองเหตุแรก, `next_due_at ≤ now` และยังมีชิ้นที่ไม่ใช่ `sold`/`withdrawn` (ถ้าทุกชิ้นปิดแล้ว หยุดจนกว่าจะมีหลักฐานเปิดใหม่) |

งบ revisit ต่อกลุ่มต่อ slot ของกลุ่มคือ 10 งาน (ตั้งค่าได้) เลือกจากรายการที่ค้างนานที่สุดก่อน รายการที่เกินงบรอ slot ถัดไป `claim_next` จองงาน discover ที่ถึงกำหนดก่อน revisit โดยยังมี browser job ได้ครั้งละหนึ่งงานทั้งระบบ เมื่อ revisit สำเร็จ Watch ที่อยู่ในรอบช้าได้ `next_due_at = now + 24 ชม.`

**Revisit script สำหรับทดสอบ** `fixtures/revisits/<ID>/script.json` ตรวจด้วย `fixtures/revisits/script.schema.json`: `{"revisit_version": 1, "group_key": "…", "visits": [{"source_post_key": "…", "at": "<UTC>", "access": "ok", "envelope": "envelopes/<file>.json"}]}` `envelope` เป็น null เมื่อ `access` ไม่ใช่ `ok` adapter ส่ง envelope ด้วย `job_kind = revisit` และ `page_check = {"session": "ok", "sort": "not_applicable"}` ข้อมูลส่วนหัวของ run ใช้ `coverage = complete` เมื่ออ่านได้ และ `none` พร้อม `error_code` เมื่อเปิดไม่ได้

`GET /v1/watches` คืน `{"watches": [{"post_id", "saved_at", "hot_until", "next_due_at", "last_success_at", "overdue_since"}]}` `overdue_since` คือ `next_due_at` เมื่อเลยกำหนดแล้วยังไม่สำเร็จ หน้า UI แสดงเป็น “ไม่ได้ตรวจตั้งแต่ …” ด้วย `last_success_at`

## 7. เกณฑ์ที่ A4 พิสูจน์และการแบ่งงาน

| ส่วน | AT | fixture |
|---|---|---|
| A4a สถานะและราคา | AT-03, AT-08 | S01–S04 และ scenario ใหม่ (หลักฐานผู้ขายชี้คอมเมนต์ผู้ซื้อ, actor ไม่ตรง, ขาย 1 จาก 2, revisit ที่เปิดไม่ได้หลังเปิดขาย) |
| A4b timeline และคอมเมนต์ | AT-04 | S04 และ scenario ใหม่ (5 คอมเมนต์ผู้เขียนเดียวโหลดครบ, ยอด 5→7, แก้ไขคอมเมนต์) |
| A4c ผู้ขาย | AT-06 (ส่วนตัวตน) | scenario ใหม่ (ชื่อซ้ำสอง key) |
| A4d Watch และ revisit | AT-10 (ส่วน Watch) | revisit script และนาฬิกาที่ควบคุมได้ |

เฉลยของ scenario ใหม่ต้องผ่านรีวิวของเจ้าของก่อนใช้ตรวจรับ ระหว่างนั้นเทสต์ของแต่ละ PR ใช้ envelope สังเคราะห์ที่สร้างในเทสต์
