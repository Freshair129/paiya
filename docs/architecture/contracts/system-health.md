# สัญญาสุขภาพระบบ (O2)

หน้านี้ขยายแถว “สุขภาพระบบ” ใน [สเปกสถาปัตยกรรม](../SPEC.md) ข้อ 7 และ SRS ข้อ 6 (structured log, disk ใกล้เต็ม) ให้เป็น log, endpoint, หน้าจัดการ และเกณฑ์ตรวจ ตามแถว O2 ใน [ROADMAP](../../delivery/ROADMAP.md) ใช้คู่กับ [สัญญาการสำรอง](backup-and-restore.md) ข้อ 4 (`backup status`) และ [สัญญา enrich](local-model-enrichment.md) (AI backlog)

## 1. Structured log

- ทุก process ของ paiya เขียน log เป็น JSON หนึ่งบรรทัดต่อเหตุการณ์ออก stderr MCP ใช้ stderr อยู่แล้วตาม [สัญญา MCP](mcp-read.md)
- ฟิลด์บังคับ: `ts` (UTC ISO-8601), `level`, `event`
- ฟิลด์ตามเหตุการณ์:

| `event` | ฟิลด์ |
|---|---|
| `capture_run_finished` | `run_id`, `group_id`, `job_kind`, `adapter_version`, `result` (`coverage`), `reason` (`stop_reason` หรือ `error_code`), `duration_ms`, `queue_wait_ms`, `cards_seen`, `cards_parsed`, `duplicate_cards_skipped` (จำนวนการ์ด stable key ซ้ำที่ข้ามใน discovery; งาน revisit เป็น 0) |
| `media_failed` | `run_id`, `evidence_local_key`, `reason` (`media_reason`) |
| `disk_low` | `path_kind` (`media` หรือ `backup`), `free_bytes`, `min_free_bytes` |
| `enrich_job_finished` | `job_id`, `result`, `reason`, `duration_ms` |
| `backup_run_finished` | `backup_run_id`, `result`, `reason`, `duration_ms` |
| `purge_finished` | `purge_id`, `result`, `pending_count` |

- ห้ามมีข้อความประกาศ คอมเมนต์ ชื่อผู้ใช้ URL ต้นทาง path เต็มของปลายทางสำรอง หรือ secret ใน log ใช้ได้เฉพาะ ID ภายใน จำนวน และรหัสเหตุผล

## 2. ดิสก์ใกล้เต็ม

- `PAIYA_DISK_MIN_FREE_BYTES` (ค่าเริ่มต้น 5 GiB) เป็นพื้นที่ว่างขั้นต่ำของอุปกรณ์ที่เก็บคลังภาพ
- ก่อน ingest เขียนภาพใหม่ ให้ตรวจพื้นที่ว่าง (`shutil.disk_usage`) เมื่อต่ำกว่าเกณฑ์:
  - ไม่เขียนไฟล์
  - บันทึกหลักฐานด้วย `media_state = capture_failed` และ `media_reason = disk_low` ซึ่งเป็นค่าที่ [สัญญา Collector](collector-and-query.md) อนุญาตอยู่แล้ว
  - บันทึก observation และข้อเท็จจริงอื่นตามปกติ (SRS: ผลที่ขาดภาพยังรายงานตามหลักฐานที่มี)
  - เขียน log `disk_low`
- ภาพที่มี hash อยู่แล้วในคลังไม่ต้องเขียนไฟล์ใหม่ จึงผูกได้ตามปกติ

## 3. `GET /v1/admin/health` และ `paiya health`

- ใช้การเข้าถึงแบบเดียวกับ endpoint อื่นใต้ `/v1` ตาม [ADR 0004](../decisions/0004-local-access-and-mcp.md)
- คืนเฉพาะจำนวนและเวลา ไม่มีเนื้อหาประกาศ
- CLI `paiya health` พิมพ์ JSON เดียวกัน

| ส่วน | ค่า |
|---|---|
| `queue` | ต่อ `kind`: จำนวนงาน `queued` ที่เลยกำหนดเกิน `PAIYA_JOB_OVERDUE_MINUTES` (ค่าเริ่มต้น 15) และอายุของงานที่เลยกำหนดนานที่สุดเป็นนาที |
| `coverage` | ต่อกลุ่ม: เวลาตรวจสำเร็จล่าสุด, coverage ของรอบล่าสุด, จำนวน `capture_gaps` และช่วงขาดล่าสุดใน 7 วัน |
| `media` | 24 ชั่วโมงล่าสุด: `captured`, `capture_failed`, อัตราสำเร็จ = captured / (captured + capture_failed) หรือ `null` เมื่อตัวหารเป็นศูนย์ และจำนวน `disk_low` |
| `disk` | `media`: `free_bytes`, `total_bytes`, `min_free_bytes`, `state` (`ok`/`low`) และ `backup` แบบเดียวกันเมื่อตั้ง `PAIYA_BACKUP_DIR` |
| `backup` | ผลของ `backup status`: เวลาสำรองสำเร็จล่าสุดจริง, `age_hours`, `state` (`ok` เมื่ออายุ ≤ 26 ชั่วโมง, `stale` เมื่อเกิน, `never` เมื่อยังไม่เคย), รอบล้มเหลวล่าสุด |
| `ai_backlog` | จำนวนงาน enrich ตามสถานะ และอายุงาน queued ที่เก่าที่สุด |
| `checked_at` | เวลาที่ตอบ |

## 4. หน้าจัดการใน PWA

- เส้นทาง `/admin/health` แสดงทุกส่วนในข้อ 3 เป็นข้อความไทยที่ความกว้างมือถือก่อน
- ค่า `low`, `stale`, `never` หรือ queue ที่เลยกำหนดแสดงเป็นสถานะเตือนพร้อมข้อความอธิบาย
- ระยะ A ยังไม่มีระบบแจ้งเตือน (AT-12 ระยะ M) “แจ้งเหตุ” ในระยะนี้จึงหมายถึง log `disk_low` และสถานะเตือนในหน้านี้

## 5. เกณฑ์ตรวจ (fixture)

- **ดิสก์ใกล้เต็ม:** จำลองพื้นที่ว่างต่ำกว่าเกณฑ์ด้วยการแทนที่ฟังก์ชันวัดพื้นที่ในเทสต์ ingest แล้ว:
  - ไม่มีไฟล์ใหม่
  - หลักฐานเป็น `capture_failed`/`disk_low`
  - observation และสถานะยังบันทึก
  - มี log `disk_low`
  - หน้าจัดการแสดง `low` (ROADMAP O2)
- **เวลาสำรองจริงเมื่อเครื่องปิดช่วงนัด:** มีรอบสำรองสำเร็จเก่ากว่า 26 ชั่วโมงและไม่มีรอบใหม่ หน้าจัดการต้องแสดงเวลาจริงของรอบนั้นและ `stale` ไม่ใช่เวลาที่นัดไว้ (ROADMAP O2)
- **ค่าในแต่ละส่วน:** queue ที่เลยกำหนด ช่องว่างการตรวจ อัตราภาพสำเร็จ และ AI backlog ตรงกับข้อมูล fixture ที่เตรียม รวมกรณีตัวหารเป็นศูนย์
- **log:** ทุกเหตุการณ์ในข้อ 1 เป็น JSON ที่มีฟิลด์บังคับ และไม่มีข้อความจาก fixture ที่ใส่ไว้เป็น canary
