# สัญญาส่งข้อมูลจาก Collector ภายนอกเข้า paiya (inbox)

**ร่าง · เสนอเพื่ออนุมัติ** · หน้านี้กำหนดรูปแบบที่ Collector ภายนอกใช้ส่งผลการเก็บเข้า paiya ผ่านไดเรกทอรีในเครื่อง และสิ่งที่ paiya ทำเมื่อรับ ใช้คู่กับ [สัญญา Collector และ API](collector-and-query.md) ซึ่งเป็นเจ้าของ envelope `observation v1` ([schema](observation.v1.schema.json)) และ [สัญญาคิวค้นพบ](discovery-queue.md) ข้อ 4 ซึ่งเป็นเจ้าของ run summary

Collector เป็นโปรแกรมแยกที่เจ้าของรีโปพัฒนาและรันเอง โค้ดอยู่ใน subproject `collector/` ตาม [ADR 0006](../decisions/0006-collector-monorepo.md) ส่วน paiya runtime ไม่เปิดหรือส่งคำขอไปยังแหล่งต้นทางในขั้นใดของสัญญานี้

## 1. ขอบเขตและความเสี่ยง

| ส่วน | สถานะ | ระดับตาม AGENTS.md |
|---|---|---|
| รูปแบบ inbox และ `paiya inbox validate` ที่ตรวจไฟล์โดยไม่เขียนฐาน | ทำได้ทันที | ต่ำ |
| `paiya inbox ingest` ที่บันทึก envelope `source_kind = facebook_live` ลงฐาน และการสร้าง grant ของแหล่งจริง | รอเจ้าของรีโปเขียนอนุมัติใน Issue | สูง (เปิดรับข้อมูลแหล่งจริงตามเงื่อนไขระยะ B) |
| ตัว Collector ที่เปิด Facebook | โค้ดอยู่ใน `collector/` แต่รันแยกจาก paiya และเจ้าของรีโปตัดสินใจ/รับความเสี่ยงตาม ADR 0005–0006 และข้อ 6 | นอกขอบเขตของ paiya runtime |

## 2. โครงสร้าง inbox

กำหนดตำแหน่งด้วย `PAIYA_COLLECTOR_INBOX` ซึ่งต้องอยู่นอกรีโป นอกคลังภาพ และนอกไดเรกทอรีของ purge log

```
<inbox>/
  incoming/<run_id>/
    run.json                         # run summary หนึ่งไฟล์ต่อรอบ
    envelopes/<capture_id>.json      # observation v1 หนึ่งไฟล์ต่อโพสต์ที่แยกได้
    assets/<staging_key>             # ไฟล์ภาพที่ envelope อ้างใน assets
    READY                            # ไฟล์ว่าง เขียนเป็นไฟล์สุดท้าย
  processing/  done/  rejected/      # paiya เป็นเจ้าของ Collector ห้ามเขียน
```

- Collector เขียนทุกไฟล์ลง `incoming/.<run_id>.tmp/` ก่อน เขียน `READY` เป็นไฟล์สุดท้าย แล้วเปลี่ยนชื่อไดเรกทอรีเป็น `incoming/<run_id>/` แบบ atomic บน volume เดียวกัน
- paiya อ่านเฉพาะไดเรกทอรีที่มี `READY` และไม่อ่านชื่อที่ขึ้นต้นด้วยจุด
- หนึ่ง `run_id` ส่งได้ครั้งเดียว การส่งซ้ำด้วย `run_id` เดิมถูกปฏิเสธเป็น `run_duplicate`

## 3. `run.json`

ฟิลด์ตาม [สัญญาคิวค้นพบ](discovery-queue.md) ข้อ 4 โดย `run_id` ตรงกับชื่อไดเรกทอรี:

`run_id` (UUID), `group_key`, `grant_id` (UUID ของ grant ที่เจ้าของสร้างใน paiya), `job_kind` (`discover` หรือ `revisit`), `adapter_version`, `started_at`, `ended_at`, `session_result`, `sort_result`, `coverage`, `partial_reasons`, `stop_reason`, `cards_seen`, `cards_parsed`, `scroll_steps`, `error_code`

- ค่าที่อนุญาตของ `coverage`, `partial_reasons`, `stop_reason` และ `error_code` เป็นค่าเดียวกับ `capture_runs` ในฐานข้อมูล
- รอบที่ `coverage = none` ส่งได้โดยไม่มี envelope เพื่อให้ paiya บันทึกช่วงขาด

## 4. envelope

ทุก envelope ต้องผ่าน [observation.v1.schema.json](observation.v1.schema.json) และ invariant ใน [สัญญา Collector](collector-and-query.md) ข้อ “ความหมายของฟิลด์และค่าที่อนุญาต” พร้อมข้อกำหนดเพิ่มสำหรับแหล่งจริง:

- `source_kind = facebook_live`
- `run_id` และ `grant_id` ตรงกับ `run.json`
- `job_kind` ตรงกับ `run.json`
- `capture_id` คงที่ต่อโพสต์ในหนึ่งรอบ ใช้ `uuid5(run_id, source_post_key)` และตรงกับชื่อไฟล์
- `payload.group_key` ตรงกับ `run.json`
- `assets` อ้างไฟล์ใต้ `assets/` ด้วย `staging_key`, `sha256`, `mime` และ `bytes` ที่ตรงกับไฟล์จริง
- ภาพเก็บเฉพาะส่วนที่เห็นสินค้าหรือหลักฐาน ตาม SRS ข้อ 6
- ห้ามมี cookie, token, ค่า session, รหัสผ่าน, ข้อมูลบัญชีของผู้ใช้ Collector เอง หรือ HTML ดิบในไฟล์ใด schema ปฏิเสธฟิลด์ที่ไม่รู้จักอยู่แล้ว

## 5. สิ่งที่ paiya ทำ

- **`paiya inbox validate <run_dir>`** (ไม่ต้องได้รับอนุมัติ)
  - ตรวจโครงสร้าง, `run.json`, ทุก envelope, ความตรงกันของ ID, และ hash ของ assets
  - สแกนหาค่าที่ดูเหมือน cookie หรือ token
  - พิมพ์รายงาน JSON
  - ไม่เขียนฐานข้อมูลและไม่ย้ายไฟล์
  - Collector ใช้คำสั่งนี้ตรวจผลของตัวเองได้
- **`paiya inbox ingest`** (รออนุมัติ) ย้ายรอบไป `processing/` แล้วทำตามลำดับ:
  1. ตรวจแบบเดียวกับ validate
  2. ตรวจ grant ว่าใช้ได้และมี `capture` ทั้งตอนเริ่มและก่อน commit ตาม A2
  3. บันทึก `capture_runs` จาก `run.json`
  4. ingest ทีละ envelope ผ่านเส้นทางเดิม `capture_id` เดิมกับ `payload_hash` เดิมได้ผลเดิม ส่วน hash ต่างเป็น conflict ของ envelope นั้น
  5. ย้ายไป `done/` พร้อม `result.json` (สถานะราย envelope) หรือ `rejected/` พร้อมเหตุผล
  - grant ที่ถูกเพิกถอนทำให้ทั้งรอบถูกปฏิเสธ
  - การลบและการ replay หลังกู้คืนใช้กติกาเดิม
- paiya ไม่เปิด URL ใน envelope และไม่ติดต่อแหล่งต้นทาง

## 6. ข้อกำหนดฝั่ง Collector

ขั้นตอนฝั่งผู้ผลิต ข้อจำกัดของ revisit และ 429 กับเกณฑ์รับงานอยู่ใน [สเปกโปรแกรม Collector ภายนอก](../external-collector.md)

paiya บังคับข้อเหล่านี้ไม่ได้ จึงเป็นความรับผิดชอบของผู้รัน Collector ซึ่งตรงกับ [สเปกสถาปัตยกรรม](../SPEC.md) ข้อ 3 และ 6

- เจ้าของบันทึกคำตัดสิน ขอบเขต วัตถุประสงค์ และ retention ของกลุ่มไว้ใน grant ของ paiya ก่อนรัน ตาม [ADR 0005](../decisions/0005-owner-directed-collection.md) ซึ่งระบุความเสี่ยงต่อเงื่อนไขของแพลตฟอร์มและบัญชีที่เจ้าของรับเอง
- ยืนยันการเรียงล่าสุดก่อนนับเป็นรอบเรียงล่าสุด อ่านแบบมีขอบเขตด้วย overlap และหยุดตามงบเวลา
- เจอหน้าให้ล็อกอิน checkpoint หรือ challenge ให้หยุดทันที และส่งรอบด้วย `error_code` ที่ตรง
- เจอ 429 ให้เคารพ `Retry-After`
- ทำทีละงานด้วยโปรไฟล์เฉพาะ
- ห้ามใช้เครื่องมือหรือเทคนิคหลบการตรวจจับบอท
- ไม่เก็บข้อมูลเกินที่ grant อนุญาต

## 7. รายการที่เจ้าของต้องระบุเมื่ออนุมัติ `inbox ingest`

1. กลุ่มที่อนุญาต และคำตัดสินของเจ้าของตาม ADR 0005 (ไม่ใส่ข้อมูลจริงใน Issue สาธารณะ)
2. scope ของ grant (`capture`, `read`, media) และ retention
3. การเปิด `source_kind = facebook_live` ใน ingest
4. วิธีสร้าง grant ของแหล่งจริงในเครื่อง ซึ่งงาน implementation จะเสนอเป็นคำสั่ง CLI

## 8. Prompt สำหรับ Codex ที่สร้าง Collector

เจ้าของใช้ข้อความนี้ส่งให้ Codex ในรีโปหรือ worktree ของ Collector ได้ตรง ๆ

```text
Task: build an external Collector that writes its results into the paiya inbox format.

Producer behavior and acceptance: docs/architecture/external-collector.md. Start with offline discovery; see its compatibility gates before emitting revisit or rate-limit summaries.

Contract (source of truth, read all of it first, from the paiya repo):
- docs/architecture/contracts/collector-inbox.md (this delivery contract: layout, run.json, envelope rules, Collector obligations)
- docs/architecture/contracts/collector-and-query.md (observation v1 field meanings and invariants)
- docs/architecture/contracts/observation.v1.schema.json (JSON Schema; additionalProperties is false)
- docs/architecture/contracts/discovery-queue.md section 4 (run summary fields, coverage, overlap, stop reasons, checkpoint)
- docs/architecture/contracts/item-status-and-watches.md section 2 (evidence roles: seller / other / source_label)
- docs/architecture/SPEC.md sections 3 and 6 (bounded reading, stop rules, Retry-After)

Output, per run:
- write <inbox>/incoming/.<run_id>.tmp/ with run.json, envelopes/<capture_id>.json, assets/<staging_key>, then READY last, then atomically rename the directory to <inbox>/incoming/<run_id>/
- source_kind = "facebook_live"; run_id, grant_id, job_kind and group_key match run.json; capture_id = uuid5(run_id, source_post_key)
- assets: only cropped images that show the item or the evidence; sha256, mime and bytes must match the file
- never write cookies, tokens, session values, passwords, your own account identifiers or raw HTML into any output file

Behaviour you must keep:
- run only for groups whose grant_id the owner gives you; one job at a time; a dedicated browser profile
- confirm the "newest first" sort before calling a run sorted; read with overlap and stop on the time budget; report partial coverage honestly
- on a login page, checkpoint or challenge: stop at once and deliver the run with the matching error_code; on HTTP 429 honour Retry-After
- do not use any bot-detection evasion, stealth plugins or fingerprint spoofing

Verification:
- unit tests on saved, synthetic HTML fixtures only; tests make no network calls
- every produced run directory must pass `uv run python -m paiya.cli inbox validate <run_dir>` from the paiya repo with zero problems
- report: files changed, how each obligation above is met, validator output for a synthetic run
```
