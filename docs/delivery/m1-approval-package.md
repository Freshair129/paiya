# แพ็กเกจขอคำตัดสินเจ้าของสำหรับ M1

**สถานะ:** ข้อเสนอเพื่อให้ Product owner และ Tech lead ตัดสินใจ · ADR 0002 ยังคงสถานะ “เสนอเพื่อรับรอง” · ยังไม่มีการนำ schema v2 ไปใช้
**ขอบเขตเอกสารรอบนี้:** ไฟล์นี้และแถวในสารบัญเท่านั้น งานระดับต่ำ ไม่มีการแก้สัญญา โค้ด หรือฐานข้อมูล

เอกสารนี้รวบรวมสิ่งที่ต้องตัดสินก่อน M1: แนวทาง ADR 0002, schema version ใหม่ และการแปลงจาก v1 งานรอบนี้ไม่เริ่ม implementation ใดที่เกี่ยวกับ schema_version หรือการนำเข้าจริง

## 1. สรุปคำตัดสินใน ADR 0002

1. **รับแนวทางทดลอง M โดยแยกจาก Collector ระยะ B**

   ข้อความต้นทาง: “การเปิดใช้ M ไม่เปิด B” — [ADR 0002, ขอบเขตที่เสนอ §3](../architecture/decisions/0002-source-strategy.md)

   หากเห็นชอบ M จะรับข้อมูลที่ผู้ซื้อส่งเองภายใต้ grant ของการนำเข้าด้วยมือ ส่วน Collector อัตโนมัติยังต้องรอสิทธิ์ของแพลตฟอร์มและกลุ่มตามเกณฑ์ B แยกต่างหาก ทางเลือกคือรอแหล่ง Facebook ที่มีสิทธิ์ก่อนเริ่ม M หรือประเมินแหล่งอื่นที่ได้รับอนุญาตก่อนออกแบบ adapter

2. **ให้การนำเข้าด้วยมือใช้ permalink ตรง ข้อความที่ผู้ซื้อส่ง และการตรวจตัวอย่างก่อนบันทึก**

   ข้อความต้นทาง: “ระบบไม่เปิด permalink หรือค้น Facebook ระหว่างนำเข้า” — [ADR 0002, ขอบเขตที่เสนอ §3](../architecture/decisions/0002-source-strategy.md) และ [สัญญาฝั่งผู้ซื้อ](../architecture/contracts/buyer-workflows.md)

   หากเห็นชอบ M1 จะแยก permalink ในเครื่อง ไม่ตาม redirect หรือเปิดต้นทาง ใช้ข้อความที่ผู้ซื้อส่งเพื่อแสดงตัวอย่างก่อนยืนยัน และรับภาพเฉพาะเมื่อ grant อนุญาต ผู้พูดที่พิสูจน์ไม่ได้คงเป็น unknown ทางเลือกคือเลื่อนการนำเข้าไปจนมี Collector ที่ได้รับอนุญาต ซึ่งไม่ให้ผลการทดลอง M ตามที่ PR-10 เสนอ การตามลิงก์หรือดึงข้อมูลจากต้นทางเป็นการเปลี่ยนขอบเขตระดับสูงและไม่อยู่ในข้อเสนอนี้

3. **คง observation v1 สำหรับข้อมูลเดิม และเสนอ v2 แยกสำหรับการนำเข้าเองกับราคาหลายองค์ประกอบ**

   ข้อความต้นทาง: “ใช้ observation v1 ที่มีอยู่กับ fixture/collector เดิม” — [ADR 0002, ขอบเขตที่เสนอ §4](../architecture/decisions/0002-source-strategy.md)

   หากเห็นชอบ ให้เพิ่มสัญญา v2 แยกไฟล์และตัวตรวจที่เลือกตาม schema_version โดยไม่แก้ความหมายของ v1 ข้อมูล v1 ที่ส่งซ้ำต้องให้ผลการ ingest เดิม ทางเลือกคือแก้ v1 โดยตรงซึ่งจะทำให้ fixture และ adapter เดิมเสี่ยงเปลี่ยน หรือรับ v2 โดยไม่รักษา v1 ซึ่งขัดกับข้อกำหนดความเข้ากันได้

4. **วัดผลผู้ซื้อแยกจากการตัดสินใจเปิด Collector**

   ข้อความต้นทาง: “Tech lead รับผิดชอบตรวจสิทธิ์และความพร้อมของ B แยกกัน” — [ADR 0002, วิธีตัดสินใจหลังทดลอง](../architecture/decisions/0002-source-strategy.md)

   หากเห็นชอบ M5 จะวัดเวลาเลือกดีลและการกลับมาเพิ่มดีลที่สองภายในเจ็ดวัน โดยไม่ใช้ผลนั้นเป็นสิทธิ์เปิด B ทางเลือกคือไม่ใช้ตัวชี้วัดการกลับมา หรือเลื่อนการเก็บตัวชี้วัดจน M1–M4 และเงื่อนไข M5 พร้อมครบ การเริ่มนับก่อนเวลานั้นเป็นคำถามเรื่องการเปลี่ยนเกณฑ์รับมอบ ซึ่งต้องให้เจ้าของตัดสินในข้อ 6

## 2. ข้อเสนอ schema v2

ข้อเสนอนี้ยึด [ตารางแมป observation v2 ใน buyer-workflows.md](../architecture/contracts/buyer-workflows.md) และ [สัญญา v1 ปัจจุบัน](../architecture/contracts/collector-and-query.md) ชื่อ field ที่ระบุว่า “เสนอเพิ่ม” เป็นชื่อที่ต้องให้เจ้าของตัดสินก่อน implementation

| ตำแหน่ง / field | ข้อเสนอจาก v1 ไป v2 | ความต้องการ |
|---|---|---|
| payload.schema_version | ใช้ค่า const 2 ใน schema ไฟล์ใหม่ observation.v2.schema.json; schema v1 ยังคง const 1 | FR-14, AT-11 |
| envelope.source_kind | เพิ่ม manual_share; การนำเข้าเองต้องตรงกับ source_grants.source_kind และไม่ใช้ grant ของ facebook_live | FR-13/14, AT-09/11 |
| envelope.grant_id | ต้องไม่เป็น null สำหรับ manual_share และต้องชี้ grant ของกลุ่มที่ผู้ใช้เลือก ตรวจขอบเขตก่อนอ่าน body ใน preview และตรวจซ้ำก่อน commit | FR-13/14, AT-09/11 |
| envelope.job_kind และ page_check | เพิ่ม manual_import และอนุญาต page_check เป็น null เฉพาะ manual_import; ค่า session_result และ sort_result ใน capture_runs คง null เพราะไม่ใช่การตรวจ browser | FR-14, AT-11 |
| payload.group_key, source_post_key, permalink | คง field เดิม; แยกกลุ่มและ stable post key จาก permalink ที่รองรับในเครื่อง ลิงก์ที่ต้องตาม redirect หรือตีความไม่ได้ให้ปฏิเสธ | FR-14, AT-11 |
| payload.post_text (เสนอเพิ่ม) | เก็บข้อความที่ผู้ซื้อส่งใน snapshot ของ v2 เพื่อให้ตรวจ quote และ offset ได้ โดย field นี้บังคับเมื่อ source_kind เป็น manual_share | FR-07/14, AT-11 |
| evidence[].source_span | คงโครงสร้างเดิม ใช้ field=post_text พร้อม quote และ start/end เมื่อมี; ตัวตรวจ v2 ตรวจว่า quote ตรงกับข้อความต้นฉบับ | FR-07/14, AT-11 |
| items[].price → items[].price_facts[] | เปลี่ยนจากราคาเดียวเป็นรายการ facts ต่อชิ้นและ observation; แต่ละ fact มี kind, unit, currency, amount, amount_max? และ evidence_key ที่ชี้ evidence.local_key เดียวกัน | FR-07/15/17, AT-07/12/14 |
| price_facts[].kind | ใช้ ask, shipping, deposit, installment, offer หรือ unknown; ค่า ask ใช้ตั้งรับ/เทียบราคา, shipping ใช้คำนวณยอดเมื่อผูกกับชิ้นได้, deposit ไม่ใช่ ask | FR-15/17, AT-07/12/14 |
| price_facts[].unit | ใช้ each, bundle หรือ unknown ต่อ fact; bundle มีอยู่แล้วใน enum v1 จึงนำค่าเดิมมาใช้กับ fact แต่ละรายการ ไม่เพิ่มความหมายใหม่ | FR-15/17, AT-07/12/14 |
| payload.unassigned_price_facts[] (เสนอเพิ่ม) | เก็บ fact เช่นค่าส่งระดับโพสต์ที่ยังระบุชิ้นไม่ได้ โดยใช้โครงสร้างเดียวกับ price_facts แต่ไม่มี item key; เขียนเป็น price_facts.item_id=null | FR-17, AT-14 |
| HTTP header Idempotency-Key | ไม่ใช่ JSON field; จับคู่กับ jobs.logical_key ใน namespace ของเจ้าของเครื่อง และเก็บ request_digest ซึ่งครอบคลุม preview_digest, parser, permalink, ข้อความ, การจับคู่ชิ้น และ hash ของภาพ | FR-14, AT-11 |
| capture_id, run_id, observed_at, posted_at | คง field เดิม: capture_id/run_id ระบุการส่งหนึ่งรอบ, observed_at ใช้เวลาที่ server รับการยืนยัน, posted_at/source_time แยกเวลาโพสต์; ใช้ key เดิมเมื่อ retry และ key ใหม่เมื่อผู้ใช้ตั้งใจส่งรอบใหม่ ไม่เพิ่ม round_number ซ้ำซ้อน | FR-05/14, AT-11 |

### ความเข้ากันได้และการแมป v1

- v1 ยังคงตรวจด้วย observation.v1.schema.json และ ingest ด้วยกฎเดิมทุกประการ รวมถึง source_kind=fixture, grant_id=null, page_check แบบ object และ items[].price
- เพิ่ม observation.v2.schema.json แยกจาก v1; การเลือก validator และเส้นทาง normalize ใช้ payload.schema_version โดยห้ามแก้ fixture v1 ให้กลายเป็น v2
- ขณะ ingest v1 เข้าสู่ storage เดียวกัน price ที่ไม่เป็น null แปลงเป็น price_facts หนึ่งแถว โดยคง kind, unit, amount, amount_max, currency และ evidence ที่มีอยู่; price=null แปลงเป็นไม่มี price fact การแปลงนี้ตรงกับ mapping ที่ระบบใช้อยู่แล้วใน [collector-and-query.md §การแปลง v1 ลงตาราง](../architecture/contracts/collector-and-query.md)
- เก็บ normalized_json ของ observation v1 ตาม envelope เดิม ไม่เขียนทับประวัติย้อนหลัง ไม่ต้อง backfill snapshot เก่า ส่วน v2 หนึ่งรายการราคาอาจสร้างหลายแถว price_facts และแต่ละแถวชี้ evidence ของตัวเอง
- การส่ง v1 เดิมซ้ำด้วย capture_id และ payload hash เดิมยังคืน observation เดิม; การส่ง v2 จะใช้กฎ idempotency ของ manual import แยกกัน ไม่ทำให้ replay fixture v1 เปลี่ยนผล

### ผลต่อ validator และ fixture

ปัจจุบัน [validation.py](../../src/paiya/ingest/validation.py) โหลด schema v1 ไฟล์เดียว, [service.py](../../src/paiya/ingest/service.py) รับเฉพาะ fixture และอ่าน items[].price โดยตรง, ส่วน [check_contracts.py](../../scripts/check_contracts.py) ตรวจ schema v1 กับกฎข้าม field

หลังได้รับอนุมัติ แผนตรวจคือ:

1. เพิ่ม schema v2 แยกไฟล์และ dispatcher ตาม schema_version; ห้ามเปลี่ยนไฟล์ schema v1 หรือผลของ v1 fixtures
2. เพิ่ม fixture v2 valid/invalid สำหรับ manual grant, page_check=null, quote/offset, หลาย price facts, amount_max, each/bundle, deposit, shipping ที่ผูกไม่ได้ และ evidence ที่ไม่มีหรือชี้ผิด scope
3. ขยาย check_contracts.py ให้ตรวจทั้งสอง schema และกฎข้าม field ได้แก่ source_kind/job_kind/page_check/grant consistency, การอ้าง evidence, quote กับข้อความต้นฉบับ, amount_max ไม่น้อยกว่า amount และคีย์ fact ไม่ซ้ำ
4. ทดสอบ idempotency, grant ที่ถูกเพิกถอน, และจำนวน request ออกไปยังต้นทางใน service/API tests เพราะ JSON Schema ตรวจ transaction, สิทธิ์ขณะ commit และ network boundary ไม่ได้
5. ใช้ข้อมูลสังเคราะห์เท่านั้น; check_contracts.py เป็นการตรวจสัญญา ไม่ใช่หลักฐานว่า API หรือ pilot ผ่าน AT-11

### ผลต่อฐานข้อมูลและ migration

ตรวจโครงสร้างปัจจุบันจาก migrations ถึง 0010_backup_runs, [tables.py](../../src/paiya/store/tables.py) และ [data-model.md](../architecture/data-model.md) แล้ว ข้อเสนอ v2 ไม่ต้อง rewrite/drop/เปลี่ยนชนิดคอลัมน์ และไม่ต้องเพิ่มคอลัมน์สำหรับ M1:

| งาน | DDL ที่เสนอ | ผลต่อข้อมูล v1 |
|---|---|---|
| M1 | ไม่มีตารางหรือคอลัมน์ใหม่; ใช้ jobs.logical_key/request_digest/result, capture_runs.source_kind/session_result/sort_result, observations.normalized_json/payload_hash/capture_id, evidence และ price_facts ที่มีอยู่ | snapshot v1 ไม่เปลี่ยน |
| M2 | เพิ่ม observations.commit_position และ sequence สำหรับลำดับ commit; สร้าง buy_rules, rule_item_states และ alerts ตาม data-model.md | เพิ่มค่าให้แถวเดิมได้โดยไม่ลบข้อมูล |
| M3 | ไม่มี DDL ตามแบบจำลองปัจจุบัน; watches.item_id และคีย์ post/item มีอยู่จาก A4 | ไม่เปลี่ยน |
| M4 | ไม่มี DDL ตามแบบจำลองปัจจุบัน; price_facts รองรับหลายแถวต่อ observation, amount_max, currency, unit และ evidence_id อยู่แล้ว | v1 ราคาเดิมยังเป็นหนึ่ง fact |

ดังนั้นการเปลี่ยนฐานข้อมูลที่คาดไว้เป็น additive เท่านั้น งาน M2 เป็น migration ระดับกลางตาม AGENTS.md การ DROP, เปลี่ยนชนิดคอลัมน์ที่มีข้อมูล หรือแปลง snapshot v1 แบบทำลายข้อมูลจะเป็นงานความเสี่ยงสูงและอยู่นอก allowlist นี้ ต้องหยุดและขอคำตัดสินใหม่

## 3. ขอบเขตและการตรวจรับ M1

M1 ครอบคลุม PR-10 / FR-14 / AT-11: ผู้ซื้อเลือก grant ที่ใช้ได้ วาง permalink ตรงกับข้อความ ดูตัวอย่างที่ไม่สร้าง observation แล้วค่อยยืนยัน การแนบภาพเป็น optional และขึ้นกับ scope ของ grant ระบบแยก URL ในเครื่อง ไม่เปิด URL และไม่ส่ง request ไป Facebook ข้อความที่คัดลอกมายืนยันผู้พูดไม่ได้

| เกณฑ์ | หลักฐานที่ต้องได้ | ชื่อ test ที่เสนอ |
|---|---|---|
| วางลิงก์แล้วไม่มีคำขอไป Facebook | ทดสอบ preview และ commit ด้วย direct permalink; instrument network client ให้จำนวน request ไป origin ของ Facebook เท่ากับศูนย์ และไม่มี redirect-follow | test_preview_and_commit_make_zero_facebook_requests |
| Preview ยังไม่บันทึก | จำนวน observations เป็น 0 หลัง preview | test_preview_does_not_create_observation |
| ยืนยันหนึ่งครั้ง | ข้อความสังเคราะห์สองชิ้นสร้าง post เดียว, observation เดียว และเปิด quote ของราคาได้ | test_confirm_creates_one_observation_with_item_price_evidence |
| Duplicate submit | retry ด้วย Idempotency-Key เดิมและ body เดิมคืน IDs เดิม; จำนวน observations ยังเป็น 1 | test_retry_same_idempotency_key_saves_once |
| ใช้ key เดิมกับเนื้อหาใหม่ | ตอบ 409 และไม่เพิ่ม observation | test_reused_idempotency_key_with_changed_body_returns_conflict |
| ส่งรอบใหม่โดยตั้งใจ | key ใหม่สร้าง observation ที่สองใต้ post เดิม แต่ข้อเท็จจริงซ้ำไม่เพิ่ม business event | test_new_idempotency_key_adds_observation_without_duplicate_events |
| สิทธิ์เปลี่ยนหลัง preview | revoke grant ก่อน commit แล้ว commit ถูกปฏิเสธและไม่เขียนแถว | test_revoked_grant_after_preview_blocks_commit |
| ถอยหลังเข้ากันได้ | fixture v1 ทั้งชุดยัง validate และ ingest แล้วได้ผลเดิม | test_v1_fixtures_validate_and_ingest_unchanged |

AT-03 เป็นข้อกำหนดผู้เขียน/ผู้ขาย ไม่ใช่การยืนยันจากข้อความที่ผู้ซื้อนำมาวาง: evidence ที่ระบุ role=seller ต้องตรงกับ role และ actor_key ของ comment version และผู้ขายของ post เดียวกันก่อนสร้าง reserved/sold การ paste ข้อความอย่างเดียวต้องคงผู้พูดเป็น unknown และไม่สร้างสถานะขายหรือแจ้งเตือนจากคำยืนยันนั้น หลักฐานตรวจรับอยู่ใน [SRS AT-03](../requirements/SRS.md) และ [buyer-acceptance.md](../quality/buyer-acceptance.md)

ชื่อ test เพิ่มเติมที่เสนอ:

- test_manual_copy_keeps_author_unknown_and_does_not_confirm_reserved_or_sold
- test_seller_evidence_with_buyer_comment_role_does_not_confirm_status
- test_seller_evidence_with_mismatched_actor_key_does_not_confirm_status

## 4. ทะเบียนความเสี่ยงและเงื่อนไขอนุมัติ

| ส่วนงาน | ระดับตาม AGENTS.md | เหตุผล / เงื่อนไข |
|---|---|---|
| แพ็กเกจนี้และแถวสารบัญ | ต่ำ | เอกสารเสนอเพื่อการตัดสินใจ ไม่มี implementation |
| การเปลี่ยนสถานะ ADR 0002 | ต่ำในฐานะการแก้เอกสาร; เป็นคำตัดสินของ Product owner และ Tech lead | เจ้าของรีโปรับรอง ADR 0002 แล้ว 9 พ.ย. 2026 (Issue #77) |
| ออก schema_version 2 และรับ v2 envelope | สูง | AGENTS.md ระบุการออก schema_version ใหม่ ต้องมีอนุมัติเป็นลายลักษณ์อักษรจาก repo owner ใน Issue ก่อนเริ่ม implementation |
| M1 endpoint/JSON contract และกฎราคา/สถานะ | กลาง | เพิ่ม endpoint/JSON ตามสัญญาและเปลี่ยนกฎราคา/สถานะ; ต้องระบุ FR/AT และทดสอบกรณีผิดพลาด |
| M1 ตาม redirect, เปิด URL หรือเรียก Facebook | สูง | เข้าข่ายการส่ง request ไปแหล่งจริง; ขัดกับ scope M1 นี้ จึงไม่รวมในอนุมัติ |
| M2 เพิ่มตาราง/คอลัมน์ | กลาง | migration แบบเพิ่มตาม DDL allowlist ด้านล่าง; ห้ามลบหรือเปลี่ยนชนิดข้อมูล |
| M2–M4 กฎตั้งรับ Watch และการเทียบราคา | กลาง | เปลี่ยนกฎตัดสินสถานะ/ราคาและรูปแบบ endpoint ตาม FR-15–17 |
| M5 การเก็บผลผู้ซื้อ | ต่ำสำหรับการเตรียมเอกสาร/fixture; ต้องผ่านเกณฑ์ pilot ใน Roadmap ก่อนนับเป็นผลรับมอบ | แยกผลสังเคราะห์ ผลบนเครื่อง และผลผู้ใช้ออกจากกัน |
| C1 เปิดใช้ Collector ระยะ B | สูง | เป็นคนละ scope ต้องผ่านเงื่อนไข B และการอนุมัติเฉพาะ ไม่ได้รับสิทธิ์จาก M หรือแพ็กเกจนี้ |

ก่อนเริ่มงานใดที่ emit schema_version 2 repo owner ต้องเขียนใน Issue ให้ครบทั้งสามรายการต่อไปนี้:

1. **ขอบเขต:** ระบุ M1–M4 ที่อนุญาต, manual_share เท่านั้น, local permalink parse, no Facebook/source requests, การรักษา v1, fixture สังเคราะห์ และกรณีล้มเหลวที่ต้องทดสอบ พร้อม FR/AT ที่ครอบคลุม
2. **หมายเลข schema:** ระบุชัดว่าอนุญาต schema_version=2 และอนุมัติการเปลี่ยน field ตามข้อ 2 รวมชื่อ payload.post_text และ payload.unassigned_price_facts[]
3. **ตารางและคอลัมน์ที่อนุญาตให้เปลี่ยน:** M1/M3/M4 ไม่มี DDL; สำหรับ M2 อนุญาตเฉพาะ observations.commit_position และ sequence observation_commit_position_seq กับตารางใหม่ buy_rules, rule_item_states และ alerts โดยใช้คอลัมน์ตาม data-model.md เท่านั้น:
   - buy_rules: id, name, revision, baseline_position, enabled, criteria, budget, currency, created_at, updated_at
   - rule_item_states: rule_id, rule_revision, item_id, last_observation_id, match_state, last_definite_match
   - alerts: id, rule_id, rule_revision, watch_id, item_id, observation_id, evidence_id, kind, cause_key, reason, created_at, read_at

รายการนี้เป็น allowlist ของข้อเสนอ ไม่ใช่การอนุมัติให้แก้ schema หาก implementation ต้องใช้ table/column อื่นให้หยุดและขอขยาย scope ใน Issue ก่อน

## 5. แผนหลังมีคำตัดสิน

ลำดับตาม [ROADMAP.md ส่วน M และ C1](ROADMAP.md):

**M1 → (M2 ‖ M3 ‖ M4) → M5 → C1**

M1 ต้องรอ A1/A2, ผลคำตัดสิน ADR 0002 และ Issue ที่ระบุอนุมัติ schema_version=2 ก่อน implementation; พัฒนาเฉพาะ fixture ได้ตามขอบเขตที่ owner ระบุ M5 ยังต้องรอ M1–M4, A5, A8 และการทดสอบซ้ำบนเครื่องตาม Roadmap; C1 ต้องรอ A9, O1–O4 และ M5 สำหรับการนำเข้าเอง

### Alembic revision plan

หัวปัจจุบันที่ตรวจจาก migration tree คือ 0010_backup_runs ซึ่งต่อจาก 0009_purge_jobs:

| งาน | revision ID ที่จัดสรรในข้อเสนอ | down_revision | หมายเหตุ |
|---|---|---|---|
| M1 | ไม่มี | — | v2 ใช้คอลัมน์และตารางปัจจุบัน |
| M2 | 0011_m2_buyer_rules | 0010_backup_runs | เพิ่ม commit_position/sequence และตาราง rules/states/alerts ตาม allowlist |
| M3 | ไม่มี | — | ใช้ watches.item_id ที่มีอยู่ |
| M4 | ไม่มี | — | ใช้ price_facts ที่มีอยู่ |
| M5 / C1 | ไม่มี | — | ไม่มี DDL ตามแบบจำลองปัจจุบัน |

จัดสรร revision เดียวที่จำเป็นให้ M2 และหลีกเลี่ยง no-op migrations หรือหลาย Alembic heads ที่ไม่เพิ่ม schema จริง M2, M3 และ M4 ยังทำงานใน worktrees แยกกันได้โดย branch ทั้งสามออกจาก commit M1 เดียวกัน; M3/M4 ไม่สร้าง migration ส่วน M2 เป็นผู้เขียน migration และ schema change เท่านั้น หากพบ DDL เพิ่มให้กลับมาขออนุมัติ allowlist ก่อน

### ไฟล์ร่วมและลำดับรวมงาน

- src/paiya/store/tables.py: มอบ M2 เป็นผู้แก้หลักสำหรับ tables.py; M3/M4 ใช้ตารางปัจจุบัน
- src/paiya/cli.py: M2 เพิ่มคำสั่งกฎ, M3 อาจขยาย watch; ให้ M2 รวมก่อน แล้ว M3 rebase และเพิ่มส่วนของตน
- web/src/App.tsx: เป็น router ปัจจุบันของเว็บ; ให้แต่ละ worktree พัฒนาหน้าเป็น component แยก และให้ integrator รวม route ตามลำดับ M1, M2, M3, M4 เพื่อลดการแก้ไฟล์ router พร้อมกัน
- src/paiya/api/app.py: รวม router ใหม่โดย integrator คนเดียวตามลำดับเดียวกับหน้าเว็บ

ลำดับ merge ที่เสนอ: M1 ก่อน แล้ว M2, M3, M4 ตามลำดับรวมไฟล์ร่วม จากนั้น M5 และ C1 เมื่อผ่านเกณฑ์ของแต่ละงาน การทำงานของ M2/M3/M4 ที่ไม่แตะไฟล์ร่วมทำคู่ขนานได้ แต่ต้อง rebase และตรวจซ้ำบนหัวล่าสุดก่อน merge

### ประมาณ effort

เป็นการวางแผนเป็น O-round ไม่ใช่ชั่วโมงทำงานที่วัดได้: commit และ merge ของ O1a, O2, O4 รวมถึงเอกสาร O3 อยู่ในวันที่ 9 พ.ย. 2026 ใน local history; งาน O2 และ O4 กระจายหลายไฟล์และมีชุดทดสอบขนาดใหญ่ จึงใช้หนึ่ง scoped O-round เป็นหน่วยเทียบหยาบเท่านั้น commit timestamp ไม่ได้บอกเวลาทำงานจริง

| งาน | ประมาณการ |
|---|---|
| M1 | 2–3 O-rounds: validator/ingest compatibility, API/permission/idempotency และหน้าฟอร์มมือถือ |
| M2 | 2 O-rounds: migration, baseline ordering, rule evaluation, alert replay และ UI |
| M3 | 1–2 O-rounds: item watch, source permission และ notification scope; reuse schema A4 |
| M4 | 1–2 O-rounds: compare read path, evidence, unknown shipping และ UI 2–3 ชิ้น |
| M5 | 1 O-round เตรียมโจทย์/เครื่องมือและ 1 O-round รายงาน; ต้องมีช่วงสังเกตตามปฏิทิน 7 วันเมื่อเริ่มตามเกณฑ์ที่เจ้าของเลือก |
| C1 | ประมาณ 0.5–1 O-round หลังได้หลักฐานจาก A9, O1–O4 และ M5 |

## 6. คำถามเปิดสำหรับเจ้าของ

| คำถาม | ทางเลือก | ข้อแนะนำ |
|---|---|---|
| รับแนวทาง ADR 0002 สำหรับ M แยกจาก B หรือไม่ | รับข้อเสนอ M แบบ manual-only / ปฏิเสธแล้วรอแหล่งที่ได้รับอนุญาต / ให้ศึกษาช่องทางอื่นก่อน | รับขอบเขต manual-only เพื่อทดลอง PR-10 โดยไม่เปิด Collector |
| อนุญาตเลข schema_version ใด | ออก v2 / เลื่อนการเปลี่ยนสัญญา / แก้ v1 | ระบุ schema_version=2 และรักษา v1 ตามข้อเสนอ |
| ใช้ชื่อ payload.post_text และ payload.unassigned_price_facts[] ใน v2 หรือไม่ | ใช้ชื่อที่เสนอ / เปลี่ยนชื่อก่อนตรึง schema / เลื่อน field ระดับโพสต์ | ใช้ชื่อที่เสนอเพื่อให้ quote ตรวจซ้ำได้และไม่บังคับผูกค่าส่งกับชิ้นที่ยังไม่ทราบ |
| อนุญาตภาพแนบภายใต้ grant แบบใด | ปิดภาพใน M1 / เปิดเมื่อ grant มี media scope / ใช้ภาพทุกครั้งที่แนบ | เปิดเฉพาะเมื่อ grant ระบุ media scope พร้อม retention; ข้อความยังทำงานได้โดยไม่มีภาพ |
| **การเปลี่ยนเกณฑ์รับมอบ:** ให้ M5 เริ่มช่วงสังเกต 7 วันเมื่อ M1+M2 พร้อมหรือไม่ | เริ่มนับการกลับมาใช้งานได้ก่อน M3/M4 / รอ M1–M4, A5, A8 และการทดสอบบนเครื่องตาม Roadmap | รอเงื่อนไข M5 ปัจจุบัน การเลือกเริ่มหลัง M1+M2 จะเปลี่ยน acceptance criteria และตัวหาร/บริบทของ pilot จึงต้องมีคำตัดสินเป็นลายลักษณ์อักษรของ owner และแก้เอกสารเกณฑ์ในงานแยกก่อนนับเป็นผลรับมอบ |

ADR 0002 รับรองแล้ว (Issue #77) คำถามข้อแรกในตารางจึงได้คำตอบแล้ว งานความเสี่ยงสูงยังไม่เริ่ม จนกว่าจะมีอนุมัติ `schema_version` ตามข้อ 4 เป็นลายลักษณ์อักษรใน Issue
