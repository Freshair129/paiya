# สัญญาคิวค้นโพสต์และรอบตรวจกลุ่ม (ระยะ A)

**สถานะ** แบบที่ใช้อ้างอิงสำหรับงาน A3 ตามคำตัดสิน D1–D5 ใน [Issue #18](https://github.com/Freshair129/paiya/issues/18) · **ผู้ดูแล** เจ้าของ Worker และฐานข้อมูล

หน้านี้กำหนดวิธีวางรอบตรวจกลุ่ม จองงาน อ่านฟีดสังเคราะห์ และบันทึกความครอบคลุมของแต่ละรอบ ใช้คู่กับ [SRS](../../requirements/SRS.md) FR-01 ถึง FR-03 และ FR-13, [สเปกสถาปัตยกรรม](../SPEC.md) ข้อ 3–4, [แบบจำลองข้อมูล](../data-model.md) และ [สัญญา Collector](collector-and-query.md) ซึ่งกำหนดการบันทึก observation หนึ่งโพสต์

## 1. ขอบเขตของ A3

- อ่านฟีดจาก **feed script** สังเคราะห์ผ่าน adapter ปลอม (D1) ไม่มี browser, profile, session หรือคำขอเครือข่าย การขับ Chromium จริงเริ่มใน B1
- จัดการกลุ่มผ่าน service และ CLI ในเครื่อง (D5) ยังไม่มี HTTP endpoint สำหรับเขียน ส่วนการอ่านผลรอบเปิดผ่าน `GET /v1/groups/{id}/runs` ภายใต้ read context ระยะ A
- งาน revisit, Watch และการตัดสินสถานะเป็นของ A4 ส่วน AT-04 แบ่งดังนี้ A3 รับผิดชอบ partial, gap และข้อห้ามสร้าง event สมมติข้ามช่วงที่ขาด A4 รับผิดชอบตรรกะคอมเมนต์และสถานะ

## 2. กลุ่มและสิทธิ์

| การกระทำ | กติกา |
|---|---|
| เพิ่มกลุ่ม | ต้องมี grant ที่ใช้ได้และมี `capture` ระยะ A ใช้ `source = fixture` เท่านั้น `poll_minutes` เป็น 30 หรือ 60 |
| จำนวนกลุ่ม | กลุ่มที่ `enabled = true` และมี `poll_minutes` ได้ไม่เกิน 5 กลุ่ม คำสั่งที่ทำให้เกินถูกปฏิเสธด้วย `group_limit_reached` โดยไม่เปลี่ยนข้อมูล |
| เปลี่ยนรอบ | ยกเลิกงาน `queued` ของกลุ่มในธุรกรรมเดียวกัน แล้วรอบถัดไปใช้ค่าใหม่ |
| พัก / เปิดต่อ | พักตั้ง `enabled = false` และยกเลิกงาน `queued` ช่วงที่พักไม่นับเป็น gap เปิดต่อแล้ววางงานของ slot ล่าสุด |
| ก่อนเริ่มงาน | ตรวจ grant ซ้ำตอนจองงาน หากเพิกถอน หมดอายุ หรือไม่มี `capture` ให้งานเป็น `cancelled` พร้อม `error_code = grant_unavailable` |

CLI รันด้วย `uv run python -m paiya.cli groups <add|set-interval|pause|resume|list>` และเรียก service ชุดเดียวกับที่เทสต์ใช้ ทุกคำสั่งพิมพ์ผลเป็น JSON หนึ่งบรรทัด และคืน exit code ไม่เป็นศูนย์เมื่อถูกปฏิเสธ

## 3. รอบเวลาและคิวงาน

**Slot** คือเวลา UTC ที่หารด้วย `poll_minutes` ลงตัวนับจาก epoch เช่น รอบ 30 นาทีได้ :00 และ :30 ฟังก์ชันวางรอบและจองงานรับเวลา `now` เป็นพารามิเตอร์เพื่อให้เทสต์ควบคุมนาฬิกาได้

| ขั้น | กติกา |
|---|---|
| วางรอบ | สำหรับกลุ่มที่เปิดและมี grant ใช้ได้ ให้ slot ล่าสุดที่ ≤ `now` มีงาน discover หนึ่งงาน `logical_key = discover:<group_id>:<slot ISO-8601 UTC>` ถ้ามีงานอยู่แล้วไม่สร้างซ้ำ |
| รวมรอบที่พลาด | ถ้ามีงาน `queued` ของ slot เก่ากว่า slot ล่าสุด ให้ยกเลิกงานเก่า สร้างงานของ slot ล่าสุดหนึ่งงาน และบันทึก `capture_gaps` ตั้งแต่ slot ของงานเก่าถึง slot ล่าสุดด้วย `reason = missed_slots` และ `compensated_by_job_id` เป็นงานใหม่ |
| จอง | เลือกงาน `queued` ที่ `due_at ≤ now` ด้วย `FOR UPDATE SKIP LOCKED` ตั้ง `state = leased`, `lease_token` ใหม่, `lease_until = now + lease` แล้ว commit ก่อนเริ่มอ่าน มี browser job ที่ `leased` ได้ครั้งละหนึ่งงานทั้งระบบ |
| ต่ออายุและจบงาน | ต่อ lease และจบงานได้เฉพาะผู้ถือ `lease_token` ปัจจุบัน จบสำเร็จเป็น `succeeded` |
| lease หมดอายุ | งานกลับเป็น `queued`, `retry_count + 1`, `due_at = now + backoff` และใช้ `capture_runs` แถวเดิม |
| ล้มเหลวเกินจำนวน | เมื่อ `retry_count` ถึงค่าสูงสุดให้เป็น `failed` และบันทึก `capture_gaps` ของ slot นั้นด้วย `reason = run_failed` |

ค่าเริ่มต้นที่ตั้งค่าได้: lease 5 นาที, retry สูงสุด 3 ครั้ง, backoff 1, 2, 4 นาที, งบต่อรอบ 120 วินาทีหรือ 30 ขั้นการเลื่อน, overlap 3 การ์ด ค่าทั้งหมดอ่านจาก configuration และเทสต์กำหนดเองได้

หลัง restart สถานะทั้งหมดอยู่ในฐานข้อมูล งานที่ `leased` แต่ `lease_until` ผ่านไปแล้วถูกจองใหม่ตามกติกา lease หมดอายุ การตั้งค่ารอบและการพักคงอยู่หลัง restart (AT-01)

## 4. การอ่านฟีดและความครอบคลุม

Adapter รับกลุ่ม งบ และ overlap แล้วคืน **run summary** ภายใน worker (D4) พร้อม envelope observation v1 ของแต่ละโพสต์ที่แยกได้ run summary ไม่ใช่สัญญาข้ามระบบและไม่เปลี่ยน `schema_version` Worker บันทึก summary ลง `capture_runs` และส่ง envelope เข้า ingest ของ A2 ด้วย `run_id` ของรอบนั้น ingest ใช้ `capture_runs` และ `jobs` ที่ scheduler สร้างไว้ และสร้างเองเฉพาะ envelope ที่เล่นซ้ำจากไฟล์ fixture โดยตรง

| ฟิลด์ใน `capture_runs` | ความหมาย |
|---|---|
| `started_at`, `ended_at` | เวลาเริ่มอ่านและเวลาจบ `ended_at` เป็น NULL ระหว่างทำงาน |
| `session_result`, `sort_result` | ค่าเดียวกับ `page_check` คือ `ok`/`required`/`unknown` และ `verified_latest`/`unverified` |
| `cards_seen`, `cards_parsed` | จำนวนการ์ดที่โหลดเห็นทั้งหมดรวม pinned และจำนวนที่แยก stable key และสร้าง envelope ได้ |
| `scroll_steps` | จำนวนขั้นการเลื่อนที่ใช้ |
| `stop_reason` | `overlap_reached`, `feed_end`, `time_budget_exhausted`, `scroll_budget_exhausted`, `access_blocked` |
| `coverage` | `complete`, `partial`, `none` เป็น NULL ระหว่างทำงาน |
| `partial_reasons` | อาร์เรย์ของ `sort_unverified`, `budget_before_overlap`, `parse_failures` ว่างเมื่อ `coverage` ไม่ใช่ `partial` |
| `error_code` | `session_required`, `challenge_required`, `permission_denied`, `grant_unavailable`, `adapter_error` หรือ NULL |

**Overlap** ถึงเมื่อพบการ์ดที่ไม่ปักหมุดและไม่ใช่โพสต์แนะนำติดกันตามจำนวน overlap ซึ่ง `source_post_key` มีอยู่แล้วใน `posts` ของกลุ่มก่อนรอบนี้เริ่ม การ์ดปักหมุดและแนะนำยังนำเข้าได้แต่ไม่นับต่อเนื่องและไม่ตัดลำดับ

**ตัดสิน coverage**

1. `access_blocked` หรือไม่มีการ์ดที่อ่านได้ → `none` และตั้ง `error_code`
2. มีเหตุใดเหตุหนึ่งต่อไปนี้ → `partial` พร้อมเหตุทุกข้อที่เข้าเงื่อนไข: `sort_result = unverified` (FR-02), หยุดด้วยงบก่อนถึง overlap (FR-03 รวมรอบแรกที่ยังไม่มีโพสต์เดิม), มีการ์ดที่แยก stable key ไม่ได้
3. นอกนั้น → `complete`

**Checkpoint** `groups.discovery_checkpoint` เก็บ `{"run_id": "<uuid>", "top_keys": ["<source_post_key>", …], "advanced_at": "<UTC>"}` โดย `top_keys` คือคีย์ของการ์ดที่ไม่ปักหมุดและไม่ใช่โพสต์แนะนำสูงสุด 20 รายการแรกตามลำดับฟีด เลื่อนเฉพาะเมื่อ `coverage = complete` รอบ partial และ none ไม่เปลี่ยน checkpoint แต่โพสต์ที่อ่านได้ยังบันทึก observation ตามสิทธิ์

## 5. ช่วงที่ขาด

ตาราง `capture_gaps` เก็บหนึ่งแถวต่อช่วงที่ไม่มีการตรวจตามนัด (D3) เหตุมีสองค่าคือ `missed_slots` (เครื่องหลับ ปิด หรือ worker ไม่ทำงาน) และ `run_failed` การพักกลุ่มไม่สร้าง gap รอบ `partial` ไม่ใช่ gap แต่แสดงผ่าน coverage ข้อมูลที่เห็นก่อนและหลัง gap ห้ามใช้สร้าง event ที่ไม่ได้เห็นภายในช่วงนั้น (SRS ข้อ 4)

## 6. Feed script สำหรับทดสอบ

ไฟล์ `fixtures/feeds/<ID>/feed.json` ตรวจด้วย `fixtures/feeds/feed.schema.json` และเฉลย `expected.json` ตรวจด้วย `fixtures/feeds/expected.schema.json` ซึ่งสร้างในงาน A3 ตามโครงด้านล่าง เฉลยของคนต้องผ่านรีวิวของเจ้าของก่อนใช้ตรวจรับ

```jsonc
{
  "feed_version": 1,
  "group_key": "synthetic-feed-s05",
  "runs": [
    {
      "label": "r1",
      "started_at": "2026-11-06T02:00:00Z",
      "session": "ok",
      "sort": "verified_latest",
      "access": "ok",
      "load_limit": null,
      "cards": [
        {"source_post_key": "s05-pin", "pinned": true, "suggested": false,
         "parse": "ok", "envelope": "envelopes/s05-pin.json"},
        {"source_post_key": "s05-p3", "pinned": false, "suggested": false,
         "parse": "ok", "envelope": "envelopes/s05-p3.json"}
      ]
    }
  ]
}
```

`load_limit` คือจำนวนการ์ดที่ฟีดโหลดให้ได้ก่อนหยุดโหลด NULL หมายถึงโหลดได้ครบรายการ `access` ใช้ค่าตามสัญญา v1 `parse: failed` หมายถึงการ์ดที่แยก stable key ไม่ได้ ซึ่งไม่มี envelope adapter ปลอมอ่านการ์ดทีละขั้นตามงบที่ได้รับ ขั้นละ 5 การ์ด

เฉลยต่อรอบระบุ `coverage`, `stop_reason`, `partial_reasons`, `cards_seen`, `cards_parsed`, `checkpoint_advanced`, `labelled_visible_keys` (stable key ที่คนติดป้ายว่าโหลดให้เห็นในขอบเขตรอบ) และ `ingested_keys`

## 7. เกณฑ์ที่ A3 พิสูจน์

| AT | ส่วนที่ A3 ตรวจ | ตัวเศษ / ตัวหาร |
|---|---|---|
| AT-01 | เพิ่มได้ 5 กลุ่ม กลุ่มที่ 6 ถูกปฏิเสธ เปลี่ยนรอบและพักแล้วมีผลหลัง restart งานค้างตรวจ grant ใหม่ เริ่มจาก fixture ได้โดยไม่มี login | — |
| AT-02 | pinned, reordering, sort หาย และหยุดก่อน overlap ให้ coverage และ stop_reason ตรงเฉลย | `|ingested_keys ∩ labelled_visible_keys|` / `|labelled_visible_keys|` ต่อรอบและรวมทุกรอบ ต้อง ≥ 95% รายงานทั้งสองตัวเลข |
| AT-04 (ส่วน A3) | รอบ partial แสดงเหตุและยอดแยกกัน เครื่องหลับแล้วได้รอบชดเชยหนึ่งงานพร้อม gap ไม่มี event ใดสร้างจากช่วง gap | — |

## 8. การอ่านผลรอบ

`GET /v1/groups/{group_id}/runs?limit=20&cursor=<opaque>` ใช้ read context และกติกา error ของ [สัญญา Collector](collector-and-query.md) `limit` สูงสุด 100 เรียงจากรอบใหม่ไปเก่า

```jsonc
{
  "group_id": "6f1c2a1e-0000-4000-8000-000000000002",
  "runs": [
    {"run_id": "6f1c2a1e-0000-4000-8000-000000000004", "job_kind": "discover",
     "scheduled_for": "2026-11-06T02:00:00Z", "started_at": "2026-11-06T02:00:03Z",
     "ended_at": "2026-11-06T02:01:10Z", "coverage": "partial",
     "partial_reasons": ["sort_unverified"], "stop_reason": "overlap_reached",
     "cards_seen": 12, "cards_parsed": 12, "session_result": "ok",
     "sort_result": "unverified", "error_code": null}
  ],
  "gaps": [{"gap_from": "2026-11-06T00:30:00Z", "gap_to": "2026-11-06T02:00:00Z",
            "reason": "missed_slots"}],
  "next_cursor": null
}
```

`gaps` คืนช่วงที่ซ้อนกับช่วงเวลาของรอบในหน้านั้น
