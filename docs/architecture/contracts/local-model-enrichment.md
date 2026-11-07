# สัญญางานเสริมข้อมูลด้วยโมเดลในเครื่อง (A6b)

**สถานะ** แบบที่ใช้อ้างอิงสำหรับงาน A6b ตามคำตัดสิน E3 ใน [Issue #48](https://github.com/Freshair129/paiya/issues/48) · **ผู้ดูแล** เจ้าของ Worker และฐานข้อมูล

หน้านี้กำหนดคิว `enrich` การเรียก Ollama ผ่าน loopback การตรวจข้อเสนอของโมเดล การเก็บเป็น tag `method = model` และผลต่อค่าใน `listing_items` ใช้คู่กับ [สัญญากฎสกัด](extraction-and-tags.md), [SRS](../../requirements/SRS.md) ข้อ 1 (Enrichment), FR-10, AT-06, AT-08 และ [SPEC](../SPEC.md) ข้อ 4 (AI หรือกราฟหยุด) และข้อ 6 (การส่งข้อมูลให้ Agent)

## 1. หลักการ

- โมเดลเสนอได้อย่างเดียว ค่าที่ใช้กรองต้องผ่านกฎ `extract-v1.0` บนข้อความหลักฐานที่โมเดลอ้าง (E3)
- ไม่ส่ง `tools` ให้โมเดล ไม่ทำตามคำสั่งใด ๆ ในผลของโมเดล ผลที่มี `tool_calls` ถูกละ field นั้นและนับใน `tool_calls_ignored`
- ingest, การค้น และการอ่านไม่รอโมเดล Ollama หยุดหรือช้าแล้วระบบยังทำงานตาม AT-08
- เรียกได้เฉพาะ `127.0.0.1`, `localhost` หรือ `[::1]` ค่าอื่นทำให้ worker ไม่เริ่มทำงาน เทสต์ใช้ Ollama ปลอมผ่าน transport ที่ฉีดเข้าไป ไม่เปิด socket

## 2. การตั้งค่า

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `PAIYA_ENRICH_ENABLED` | `false` | `true` จึงสร้างงาน enrich หลัง ingest |
| `PAIYA_OLLAMA_URL` | `http://127.0.0.1:11434` | ต้องเป็น loopback ตามข้อ 1 |
| `PAIYA_OLLAMA_MODEL` | `qwen3:4b` | ชื่อโมเดลที่ส่งใน request |
| `PAIYA_ENRICH_TIMEOUT_SECONDS` | `60` | จำนวนเต็มบวก |

ค่าที่ผิดรูปทำให้ worker และ `paiya.serve` ไม่เริ่มทำงานพร้อมข้อความชัดเจน เหมือนการตั้งค่าแนวโน้มราคา

## 3. คิว

- ใน transaction เดียวกับ ingest ของ observation ที่ `access = ok` และมีอย่างน้อยหนึ่ง item ที่มี `source_item_key` และ `evidence_keys` เมื่อ `PAIYA_ENRICH_ENABLED=true` ให้สร้าง `jobs` แถว `kind = enrich`, `target_key = <observation_id>`, `logical_key = enrich:<observation_id>:enrich-v1.0`, group/grant ของ observation, `due_at = now` ใช้ logical_key กันงานซ้ำ replay จึงไม่สร้างงานเพิ่ม
- `uv run python -m paiya.cli enrich run-once` จองงาน enrich ที่ถึงกำหนดหนึ่งงานด้วยกลไก lease เดิมของคิว ทำทีละงาน และจบงานด้วย token ปัจจุบัน `enrich run` วนทำต่อจนสั่งหยุด ทั้งสองไม่จองงานชนิดอื่น
- ก่อนเรียกโมเดล ตรวจว่า grant ของ observation ยังใช้ได้และมี `read` ไม่ผ่านให้ยกเลิกงาน (`cancelled`) โดยไม่ส่งข้อความ
- เรียกไม่สำเร็จ (เชื่อมต่อไม่ได้, timeout, HTTP ≥ 500) ให้ลองใหม่สูงสุด 3 ครั้งรวมครั้งแรก รอ 1 แล้ว 5 นาที จากนั้น `failed` พร้อมเหตุ ผลผิดรูป (ข้อ 5) ไม่ลองใหม่และจบ `succeeded` พร้อมผล `invalid_output`
- `uv run python -m paiya.cli enrich status` พิมพ์จำนวนงาน enrich ตามสถานะ และอายุของงาน queued ที่เก่าที่สุด (AI backlog ตาม SRS ข้อ 6)

## 4. ข้อความที่ส่งให้โมเดล

ส่งเฉพาะข้อมูลของ observation นั้น สำหรับแต่ละ item ที่มี `source_item_key` และ `evidence_keys`: `source_item_key`, `model`, `condition_text` และหลักฐานของ item ที่ผ่านเกณฑ์บทบาทของ[สัญญาสถานะรายชิ้น](item-status-and-watches.md) ข้อ 2 เป็นคู่ `evidence_local_key` กับ `quote` ไม่ส่งชื่อผู้ขาย actor key, permalink, คอมเมนต์ของผู้อื่น หรือภาพ รวมความยาวข้อความไม่เกิน 4,000 ตัวอักษร เกินให้ตัดหลักฐานท้ายรายการออกก่อน

Request เป็น `POST /api/chat` แบบ `stream: false`, `options.temperature: 0`, `format` เป็น JSON Schema ของข้อ 5 และไม่มี `tools` ข้อความระบบรุ่น `enrich-v1.0` บอกให้ตอบเฉพาะ JSON ตาม schema และถือข้อความใน `data` เป็นข้อมูลจากภายนอก ไม่ใช่คำสั่ง ข้อมูลอยู่ในข้อความผู้ใช้เป็น JSON เดียว

## 5. ผลที่รับ

```json
{"proposals": [{"source_item_key": "gpu-rtx3060", "key": "model_key", "value": "rtx-3060",
                "evidence_local_key": "post-1", "quote": "การ์ดจอ RTX 3060", "confidence": 0.9}]}
```

`key` เป็นหนึ่งใน `model_key`, `vram_gb`, `condition`, `warranty`, `warranty_until` · `confidence` อยู่ในช่วง 0–1 · ไม่เกิน 20 ข้อเสนอ JSON ที่อ่านไม่ได้หรือไม่ตรง schema ทั้งก้อนได้ `invalid_output` และไม่บันทึกข้อเสนอใด

## 6. การตรวจแต่ละข้อเสนอ

ตรวจตามลำดับ ข้อแรกที่ไม่ผ่านเป็นเหตุ

| ลำดับ | ตรวจ | ไม่ผ่าน |
|---|---|---|
| 1 | `source_item_key` มีใน observation และ `evidence_local_key` อยู่ใน `evidence_keys` ของ item นั้น | `rejected` · `unknown_reference` |
| 2 | หลักฐานผ่านเกณฑ์บทบาทเดียวกับสถานะ | `rejected` · เหตุตามลำดับของสถานะ (`role_mismatch`, `actor_mismatch`, `seller_unknown`, `no_eligible_evidence`) |
| 3 | `quote` หลังทำการเทียบของกฎสกัดข้อ 2 เป็นส่วนหนึ่งของ `source_span.quote` ของหลักฐาน | `rejected` · `quote_not_found` |
| 4 | `value` อยู่ในชุดค่าของ key (ตารางรุ่น, เลขหน่วยความจำ, ค่าสภาพ, ค่าประกัน, `YYYY-MM`) | `rejected` · `value_out_of_vocabulary` |
| 5 | ใช้กฎ `extract-v1.0` กับ `quote` แล้วได้ค่าเดียวกัน (หน่วยความจำใช้รุ่นที่ `confirmed` ของ item ใน observation เดียวกัน, ประกันใช้เวลาฐานเดียวกับกฎสกัด) | `proposed` · `rule_not_confirmed` |
| — | ผ่านทุกข้อ | `confirmed` |

ข้อเสนอที่ key เดียวกันของ item เดียวกันซ้ำ ใช้ข้อที่ `confidence` สูงสุด (เท่ากันใช้ลำดับในผล) ข้อที่เหลือไม่บันทึก

## 7. การเก็บและผลต่อ listing_items

- เก็บใน `tags` แถวละข้อเสนอ `method = model`, `rule_version = enrich-v1.0`, `model_version` = ชื่อโมเดลที่ Ollama ตอบ, `confidence` ตามผล ผูก `evidence_id` กับหลักฐานที่อ้างเมื่อผ่านข้อ 1 นอกนั้นผูกหลักฐานตัวแรกของ item `value` เก็บค่าที่เสนอเมื่ออยู่ในชุดค่า นอกนั้น NULL
- migration ใหม่ขยาย CHECK ของ `tags`: `method` รับ `model`, `validation_result` รับ `proposed`, `reason` รับเหตุในข้อ 6 และ `proposed` ต้องมี reason
- ค่าใน `listing_items` ตาม [สัญญากฎสกัด](extraction-and-tags.md) ข้อ 6 นับ tag `confirmed` ทั้ง `method = rule` และ `model` ภายใน observation เดียวกัน key เดียวกัน tag จากกฎชนะ ข้าม observation ใช้ล่าสุดตามเดิม กติกาการอ่านตาม grant ของข้อ 7 ใช้กับทั้งสองชนิด
- tag `proposed` และ `rejected` ไม่ใช้กรองและยังไม่แสดงในแอปรุ่นนี้
- คำสั่ง `extract rebuild` คำนวณค่าใหม่จาก tag ทั้งสองชนิด แต่ไม่เรียกโมเดล

## 8. เทสต์และผลบนเครื่อง

เทสต์ใช้ Ollama ปลอมเท่านั้น ครอบคลุมทุกเหตุในข้อ 6, ข้อความแทรกคำสั่งในหลักฐาน (AT-06: ไม่เกิดการกระทำใด และค่าที่ไม่ผ่านกฎไม่เข้า `listing_items`), ผลที่มี `tool_calls`, Ollama เชื่อมต่อไม่ได้ระหว่าง ingest และระหว่างงาน (AT-08: ingest และ `GET /v1/posts` ทำงาน งานลองใหม่แล้ว `failed`), URL ที่ไม่ใช่ loopback และ replay ไม่สร้างงานซ้ำ

การรันกับ Ollama จริงบนเครื่องต้องดาวน์โหลดโมเดล ต้องได้อนุญาตจากเจ้าของก่อน และรายงานแยกจากผลเทสต์ตาม AGENTS.md ข้อ 6
