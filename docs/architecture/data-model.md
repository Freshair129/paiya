# แบบจำลองข้อมูลป้ายยา

**สถานะ** แบบออกแบบสำหรับ PostgreSQL 17 · 7 พฤศจิกายน 2026 · เจ้าของเอกสารคือทีม Backend และ Data

เมื่อโพสต์ขายการ์ดจอสองใบเปลี่ยนราคาเพียงใบเดียว ระบบต้องตอบได้ว่าชิ้นไหนเปลี่ยน ใครเป็นผู้บอก พบเมื่อไร และเงื่อนไขตั้งรับข้อไหนเข้าเกณฑ์ หน้านี้กำหนดตาราง คีย์ และกฎที่จะใช้เขียน migration และ integration test ส่วน ERD แสดงความสัมพันธ์เดียวกันเป็นภาพ แบบนี้รวมการนำเข้าด้วยมือและการแจ้งเตือนที่เสนอในรอบนี้ ข้อมูลที่ adapter v1 ส่งได้ในปัจจุบันให้ยึดไฟล์สัญญา [ERD](views/erd.md) · [สัญญา observation v1](contracts/collector-and-query.md) · [สัญญาฝั่งผู้ซื้อและร่าง v2](contracts/buyer-workflows.md) · [SRS](../requirements/SRS.md) · [ADR การเก็บข้อมูล](decisions/0001-observation-store.md)

## 1. หน่วยที่ใช้ระบุตัวตน

| หน่วย | หนึ่งระเบียนแทนอะไร | ตัวอย่างสังเคราะห์ |
|---|---|---|
| โพสต์ | ประกาศต้นทางหนึ่งโพสต์ในหนึ่งกลุ่ม | P1 มี permalink เดียว |
| ชิ้นสินค้า | ข้อเสนอหนึ่งชิ้นที่แยกตัวตนได้ภายในโพสต์ หรือชุดที่ขายรวม | I1 = RTX 3060 12GB, I2 = RTX 3070 8GB ใน P1 |
| Observation | สิ่งที่ได้จากโพสต์หนึ่งในการส่งด้วยมือหรือการตรวจหนึ่งครั้ง | O1 พบ I1 ราคา 6,300 บาท, O2 พบ I1 ลดเหลือ 5,800 บาท |
| หลักฐานและข้อเท็จจริง | หลักฐานคือข้อความหรือภาพ ณ รอบนั้น ข้อเท็จจริงคือสิ่งที่ตีความและตรวจสอบจากหลักฐาน | ข้อความ “3060 5,800 ส่ง 150” ใน O2 รองรับราคาสินค้ากับค่าส่งคนละจำนวน |
| การตั้งรับและการติดตาม | กฎตั้งรับอยู่ได้ตั้งแต่ยังไม่มีประกาศ ส่วนการติดตามชี้ไปที่โพสต์หรือชิ้นที่พบแล้ว | R1 หา 3060 12GB ไม่เกิน 6,000 บาท ส่วน W1 ติดตาม I1 ใน P1 |

ตารางธุรกิจใช้ `id uuid` เป็น PK เว้นแต่ระบุคีย์อื่น ชนิดหรือสถานะที่ระบุเป็นชื่อกลุ่มใช้ `text` พร้อม CHECK ตามค่าที่สัญญาอนุญาต ค่า true/false ใช้ `boolean` เวลาใช้ `timestamptz` เก็บเป็น UTC แล้วแสดงตาม Asia/Bangkok คอลัมน์ลงท้าย `_id` ในพจนานุกรมหมายถึง FK ไปตารางที่ระบุ ยกเว้น `capture_id` ซึ่งเป็นคีย์ข้อความจากผู้ส่ง เครื่องหมาย `?` หมายถึงรับ NULL ตัวเลขและวันเวลาที่ต้นทางไม่ได้บอกเก็บเป็น NULL พร้อมเหตุผลเมื่อจำเป็น เงินใช้ `numeric(14,2)` กับสกุลเงิน `char(3)` ตาม ISO 4217 และแปลงจาก decimal string ตอนนำเข้า ระบบ MVP ใช้เจ้าของเครื่องหนึ่งคน การกระทำของผู้ซื้อผูกกับ session ของเจ้าของเครื่องที่ backend ตรวจแล้ว ส่วนสิทธิ์อ่านและเก็บข้อมูลกำหนดรายแหล่ง

## 2. แหล่งข้อมูลและรอบการเก็บ

| ตาราง | คอลัมน์และความสัมพันธ์ | ข้อบังคับเมื่อเขียน |
|---|---|---|
| `groups` | `source text`, `group_key text`, `canonical_url text`, `enabled boolean`, `poll_minutes smallint?`, `discovery_checkpoint jsonb?` | UNIQUE(source, group_key) กลุ่มที่ใช้ส่งด้วยมืออย่างเดียวให้รอบตรวจเป็น NULL กลุ่ม live เลือก 30 หรือ 60 นาที |
| `source_grants` | `group_id → groups`, `source_kind text`, `scopes text[]`, `authority_ref text`, `valid_from`, `valid_until?`, `revoked_at?`, `retention_days integer` | หนึ่งแหล่งมีหลาย grant ได้ เก็บหลักฐานอ้างอิงที่มาของสิทธิ์ แยก read, capture, media, comments, seller_history, analytics, agent_export ตามการใช้ บังคับ retention_days > 0 |
| `jobs` | `group_id → groups`, `grant_id → source_grants`, `kind text`, `target_key text`, `logical_key text`, `request_digest text?`, `due_at`, `state`, `lease_token uuid?`, `lease_until?`, `retry_count integer` | UNIQUE(logical_key) งานตรวจหนึ่งโพสต์ใช้คีย์จากแหล่ง ชนิดงาน เป้าหมาย และรอบนัด งานส่งด้วยมือใช้คีย์จากการกดยืนยันของลูกค้า เก็บ digest คู่กับคีย์และผูกถึงผลการบันทึกในธุรกรรมเดียวกัน |
| `capture_runs` | `id` ตรงกับ run_id, `job_id → jobs`, `group_id`, `grant_id`, `source_kind`, `started_at`, `ended_at?`, `session_result?`, `sort_result?`, `coverage`, `error_code?` | UNIQUE(job_id) สำหรับงานเก็บหนึ่งรอบ การต่อ lease หรือ retry ใช้ run เดิม หนึ่งรอบ discovery อาจได้หลายโพสต์ งาน enrich/purge ไม่มีรอบเก็บ |
| `observations` | `run_id → capture_runs`, `post_id → posts`, `group_id`, `grant_id`, `capture_id text`, `commit_position bigint`, `observed_at`, `source_time?`, `source_time_precision`, `access`, `coverage`, `parser_version`, `payload_hash`, `normalized_json jsonb` | UNIQUE(capture_id) และ UNIQUE(run_id, post_id) โดย capture_id คือคีย์ของการเก็บโพสต์หนึ่งโพสต์ในงานหนึ่งงาน คงเดิมทุก retry บันทึก snapshot ตามขอบเขตสิทธิ์ ที่ระดับนี้เก็บ `source_reported_comment_count?`, จำนวนที่อ่านได้ และจำนวนผู้เขียนที่ยืนยันได้ เพื่อเปิดดูได้แม้คอมเมนต์ที่อ่านมีเพียงบางส่วน |

**นำเข้าด้วยมือ** ตรวจ session และ grant ที่ครอบคลุม `manual_share` ก่อนรับข้อความหรือตัวไฟล์ ใช้ permalink ระบุแหล่งด้วยการแยก URL ในเครื่อง เมื่อระบุ post key ที่เสถียรไม่ได้ ให้แจ้งแก้ข้อมูลก่อนยืนยัน เก็บเฉพาะข้อความและภาพที่ผู้ใช้ส่งภายในขอบเขตที่ตกลง ระบบแยกผลตรวจ session และ sort ของเบราว์เซอร์ออกจากการยืนยันตัวผู้ใช้ ตามร่าง observation v2 ให้ใช้ `source_kind=manual_share`, `job_kind=manual_import`, `page_check=null` แล้วบันทึก session_result และ sort_result เป็น NULL การนำเข้าจากกลุ่มหนึ่งไม่ให้สิทธิ์ตั้งงานอ่านกลุ่มนั้นผ่านเบราว์เซอร์

**Retry กับการส่งรอบใหม่** backend เก็บ header `Idempotency-Key` ใน `jobs.logical_key` โดยใส่ประเภท manual และ namespace เจ้าของเครื่องไว้ในคีย์ ผูก request_digest กับ preview_digest ซึ่งครอบคลุมรุ่น parser แหล่ง ข้อความ การจับคู่ชิ้น และ hash ของภาพ เมื่อ commit ให้ชี้จาก job ผ่าน capture_run ไป observation ที่สร้าง ส่วน preview ที่ยกเลิกไม่สร้างแถวธุรกิจ ลูกค้าเก็บคีย์การส่งเดิมเมื่อเครือข่ายขาด หลังยืนยันแล้ว retry คีย์เดิมและเนื้อหาเดิมคืนผลเดิม ใช้คีย์เดิมแต่เนื้อหาต่างให้รายงาน conflict การกดยืนยันอัปเดตครั้งใหม่สร้าง observation ใหม่แม้ข้อเท็จจริงคงเดิม โดยมีเวลา “ผู้ใช้ส่งล่าสุด” ของตนเอง `payload_hash` ใช้ตรวจเนื้อหาของการส่งเดียวกัน การพบข้อมูลเดิมคนละรอบมีความหมายต่อความสด

**แยกข้อมูลจำลอง** ใช้ฐาน fixture หรือ namespace ที่แยกจากข้อมูลของผู้ซื้อ แม้ envelope v1 อนุญาต grant_id เป็น NULL สำหรับ fixture ภายในฐานทดสอบให้ผูกกับ grant ของ fixture ที่ seed ไว้ การค้น แนวโน้มราคา และการแจ้งในเครื่องผู้ซื้ออ่านเฉพาะข้อมูลจริงที่ยังมีสิทธิ์

## 3. โพสต์ รายการสินค้า และหลักฐาน

| ตาราง | คอลัมน์และความสัมพันธ์ | เงื่อนไข |
|---|---|---|
| `sellers` | `source`, `identity_scope`, `external_actor_key text` | UNIQUE(source, identity_scope, external_actor_key) สร้างตัวบุคคลเมื่อมี stable ID ชื่อแสดงผลเป็นข้อมูลที่สังเกตตาม grant ถ้าพบแค่ชื่อให้คง seller_id เป็น NULL |
| `posts` | `group_id → groups`, `source_post_key`, `permalink`, `seller_id? → sellers`, `posted_at?` | UNIQUE(group_id, source_post_key) ผู้ขายที่สรุปบนโพสต์ต้องย้อนถึง observation ที่ยังอ่านได้ เรียกประวัติผู้ขายเฉพาะกลุ่มที่อนุญาตให้เห็น |
| `listing_items` | `post_id → posts`, `source_item_key text?`, `model_key text?`, `variant text?`, `vram_gb smallint?`, `condition text?`, `unit text`, `resolution text` | UNIQUE(post_id, source_item_key) เมื่อ key มีค่า ชิ้นที่ยังแยกไม่ได้เก็บเป็น unresolved/ชุด ใช้ตัวระบุรุ่นและหลักฐานเพื่อยืนยันการจับคู่ข้ามรอบ ชื่อตรงหรือลำดับบนหน้าตรงอย่างเดียวไม่พอ |
| `comments` | `post_id → posts`, `source_comment_key text?`, `parent_id? → comments` | คอมเมนต์ที่มี stable key ใช้ UNIQUE(post_id, source_comment_key) คอมเมนต์ที่ขาด key ผูกเฉพาะการพบครั้งนั้น parent ต้องอยู่โพสต์เดียวกัน |
| `comment_versions` | `comment_id → comments`, `observation_id → observations`, `post_id`, `role`, `actor_key?`, `body text`, `source_time?`, `body_hash` | เก็บรุ่นของข้อความและผู้พูด ณ ครั้งที่พบ จุดอ้างอิงหลักฐานเป็น version นั้น การไม่พบในรอบที่โหลดไม่ครบคงประวัติเดิม |
| `media_assets` | `storage_key text`, `sha256`, `mime`, `bytes bigint`, `width`, `height` | UNIQUE(storage_key) ใช้ hash ของไฟล์จริงเพื่อตรวจความครบถ้วน การแชร์ไฟล์ซ้ำภายในเครื่องตรวจสิทธิ์แยกตาม evidence ทุกครั้ง |
| `evidence` | `observation_id → observations`, `post_id`, `grant_id`, `item_id? → listing_items`, `local_key text`, `role`, `source_span jsonb`, `comment_version_id?`, `media_asset_id?`, `media_state`, `media_reason?`, `captured_at` | UNIQUE(observation_id, local_key) ข้อความจากโพสต์เก็บ quote พร้อมตำแหน่งเมื่อมี คอมเมนต์ต้องชี้รุ่นที่ถูกอ้าง `captured` ต้องมีไฟล์ รูปขาดให้คงข้อความและเหตุผลไว้ |
| `tags` | `post_id`, `item_id?`, `evidence_id → evidence`, `key`, `value`, `method`, `model_version?`, `rule_version`, `confidence?`, `validation_result` | แท็กรุ่น สภาพ หรือคำกล่าวเรื่องประกันย้อนถึงข้อความเดิมได้ เฉพาะค่าที่ผ่านกฎตรวจใช้เป็นเงื่อนไขกรองและแจ้งเตือน |
| `offer_clusters`, `offer_cluster_members` | กลุ่มข้อเสนอเก็บ `id` ส่วนสมาชิกใช้ `item_id → listing_items` เป็น PK, `cluster_id → offer_clusters`, `evidence_id → evidence`, `rule_version` | ใช้เมื่อตรวจยืนยันได้ว่าสองประกาศเป็นข้อเสนอเดียวกัน ราคาหรือรูปเหมือนอย่างเดียวคงเป็นคนละข้อเสนอ นับ median จากข้อเสนอที่มีสิทธิ์และย้อนดูแต่ละ permalink ได้ |

**การบังคับข้ามตาราง** ให้ parent มี UNIQUE composite key ที่ FK อ้างถึง เช่น `posts(id, group_id)`, `source_grants(id, group_id)`, `capture_runs(id, group_id, grant_id)` และ `observations(id, post_id, grant_id)` เพื่อให้โพสต์กับ grant ของ observation อยู่ในกลุ่มเดียวกัน หลักฐานอ้าง `(observation_id, post_id, grant_id)` ไปยัง observation และ `(item_id, post_id)` ไปยัง `listing_items(id, post_id)` หากระบุชิ้น ตรวจแบบเดียวกันที่ facts และ alerts สำหรับข้อความคอมเมนต์ให้ตรวจว่า comment version ของหลักฐานอ้างถึงข้อความซึ่งเห็นใน observation เดียวกัน บทบาทและตัวระบุผู้พูดต้องสอดคล้องกับผู้โพสต์ในขอบเขตนั้น คำว่า “จองครับ” ที่ผู้ซื้อเขียนสร้างได้เพียงกิจกรรมสนใจ หากยืนยันผู้พูดหรือชิ้นที่กล่าวถึงไม่ได้ให้พักการตีความ

## 4. ราคาและสถานะรายชิ้น

| ตาราง | คอลัมน์และความสัมพันธ์ | กฎ |
|---|---|---|
| `price_facts` | `observation_id`, `post_id`, `item_id?`, `evidence_id`, `fact_key text`, `price_kind`, `unit`, `amount`, `amount_max?`, `currency` | UNIQUE(observation_id, fact_key) โดย fact_key รวมชิ้นหรือขอบเขตโพสต์ ชนิดราคา หน่วย และต้นทางของหลักฐาน `amount ≥ 0`, `amount_max ≥ amount` หากมี ค่าส่งทั้งโพสต์ที่ยังจัดสรรไม่ได้ให้ item_id เป็น NULL |
| `status_facts` | `observation_id`, `post_id`, `item_id`, `evidence_id`, `fact_key`, `sale_status`, `effective_at?`, `precision`, `rule_version` | UNIQUE(observation_id, item_id, fact_key) บันทึกสถานะที่ยืนยันได้ของชิ้นจากทุก observation แม้สถานะไม่เปลี่ยน เป็นข้อมูลดิบของประวัติสถานะ การอ่านโพสต์ไม่ได้สร้างผลด้าน access แยกใน observations |
| `sale_events` | `item_id`, `post_id`, `status_fact_id → status_facts`, `seq integer`, `sale_status`, `cause_key text`, `effective_at?`, `precision` | UNIQUE(item_id, seq) และ UNIQUE(item_id, cause_key) Projection ต่อชิ้นที่สร้างใหม่ทั้งชุดจาก status_facts หนึ่งแถวต่อการเปลี่ยนสถานะตามลำดับเวลา |
| `current_items` | `item_id → listing_items` เป็น PK, `last_observation_id`, `last_success_at?`, `status_event_id? → sale_events`, `ask_fact_id? → price_facts`, `shipping_fact_id? → price_facts`, `needs_review boolean` | Projection ที่ replay ได้ เชื่อมเฉพาะข้อเท็จจริงของชิ้นเดียวกัน ผู้ใช้เห็นเวลาของหลักฐานสถานะแยกจากเวลาตรวจหรือเวลานำเข้า |

**ลำดับสถานะเมื่อ observation มาไม่ตามลำดับ** เมื่อ commit status fact ของชิ้นใด ให้ lock แถว `current_items` ของชิ้นนั้น อ่าน status_facts ทั้งหมดของชิ้นที่ยังมีสิทธิ์ เรียงตาม `(effective_at, observed_at, observation_id)` โดยแทน effective_at ที่เป็น NULL ด้วย observed_at แล้วสร้าง `sale_events` ใหม่ทั้งชุด event เกิดเมื่อสถานะต่างจาก fact ก่อนหน้าในลำดับนี้ `cause_key` มาจาก evidence ของ fact ที่ทำให้เปลี่ยน จึงคงที่เมื่อสร้างซ้ำ ตัวอย่าง O1 open 09:00 กับ O3 open 10:00 ถูกบันทึกก่อน แล้ว O2 reserved 09:30 มาถึงทีหลัง ผลคือ open → reserved → open แม้ O3 เดิมไม่เคยเป็น event ปริมาณ fact ต่อชิ้นมีไม่มาก การสร้างใหม่จึงใช้กลไกเดียวกับการคำนวณใหม่หลัง purge ได้ ประเมินกฎและ Watch จากสถานะปัจจุบันก่อนและหลังการสร้างใหม่ ถ้าสถานะปัจจุบันไม่เปลี่ยน fact ที่มาช้าเติมได้เพียงประวัติโดยไม่สร้าง alert

ข้อมูลส่งเข้ารุ่น v1 มี `price` หนึ่งค่าต่อรายการ Backend แปลงค่านั้นเป็นหนึ่ง fact โดยคงชนิดและขอบเขตตามที่พิสูจน์ได้ สำหรับข้อความที่ให้ทั้งราคาสินค้าและค่าส่งให้สร้างสอง facts เมื่อสัญญารุ่นที่รองรับหรือขั้นยืนยันการนำเข้าบันทึกหลักฐานทั้งสองได้ ค่าส่งระดับโพสต์ที่ไม่ทราบว่าใช้กับชิ้นใดจะไม่นำไปบวกให้ทุกชิ้น

ยอดเทียบพร้อมส่งคำนวณจาก ask ต่อชิ้นที่ยืนยันได้บวกค่าส่งที่ใช้กับชิ้น วิธีรับของ และสกุลเงินเดียวกัน เมื่อระบุ “รวมส่ง” หรือ “ส่งฟรี” จึงนับค่าส่งเพิ่มเป็นศูนย์พร้อมหลักฐาน ขาดเงื่อนไขใดให้ค่ารวมเป็น NULL และแสดงยอดที่ทราบพร้อมสิ่งที่ยังขาด มัดจำ ค่าผ่อน หรือราคาเสนอซื้อแสดงตามชนิดของตน การคำนวณแนวโน้มใช้ ask แรกที่เข้าเกณฑ์ต่อข้อเสนอ ไม่รวมค่าส่งและไม่นับจำนวนรอบตรวจซ้ำ [วิธีคำนวณแนวโน้ม](../requirements/SRS.md)

## 5. เงื่อนไขตั้งรับ การติดตาม และกล่องแจ้งเตือน

| ตาราง | คอลัมน์และความสัมพันธ์ | ข้อบังคับ |
|---|---|---|
| `buy_rules` | `name text`, `revision integer`, `baseline_position bigint`, `enabled boolean`, `criteria jsonb`, `budget numeric(14,2)`, `currency`, `created_at`, `updated_at` | เกณฑ์มีรุ่น รุ่นย่อย VRAM สภาพ และเงื่อนไขตัดออกตามสัญญา ตรวจรูปแบบและชนิดก่อนบันทึก MVP ใช้งบบาทเทียบ ask ต่อชิ้น กำหนด CHECK(currency = 'THB') ทุกรุ่นของกฎมีจุดเริ่มประเมิน เก็บรุ่นและเกณฑ์ที่ใช้ ณ เวลาแจ้งไว้กับเหตุที่แจ้งก่อนแทนที่กฎ เพื่อเปิดอ่านเหตุผลเดิมหลังแก้กฎได้ |
| `rule_item_states` | PK `(rule_id, rule_revision, item_id)`, `rule_id → buy_rules`, `item_id → listing_items`, `last_observation_id`, `match_state`, `last_definite_match boolean?` | เก็บผลว่าเข้าเกณฑ์ หลุดเกณฑ์ หรือข้อมูลไม่พอ ค่า boolean จดจำผลที่ชัดล่าสุดตลอดช่วงที่ข้อมูลขาด |
| `watches` | `post_id → posts`, `item_id? → listing_items`, `saved_at`, `hot_until?`, `next_due_at?` | UNIQUE NULLS NOT DISTINCT(post_id, item_id) ใน PostgreSQL 17 แถว NULL คือทั้งโพสต์ แถวระบุชิ้นใช้ composite FK ตรวจว่าอยู่โพสต์นั้น บนแหล่งอัตโนมัติ `next_due_at` เป็นรอบช้าวันละครั้งหลังพ้น 24 ชั่วโมงแรกหรือเมื่อรอบเร่งหมดอายุ หลาย Watch ของ permalink เดียวใช้ revisit เดียวผ่าน logical_key งานติดตามจาก manual ไม่มีรอบนัด |
| `alerts` | `rule_id? → buy_rules`, `rule_revision?`, `watch_id? → watches`, `item_id → listing_items`, `observation_id`, `evidence_id`, `kind`, `cause_key text`, `reason jsonb`, `created_at`, `read_at?` | CHECK ให้มีต้นเหตุชนิดเดียว คือกฎหรือ watch ทุกการแจ้งชี้ชิ้นที่เปลี่ยน มี revision เฉพาะเมื่อมาจากกฎ และเปิดถึงหลักฐานใน scope เดิมได้ reason เก็บ snapshot เฉพาะเกณฑ์และค่าที่อธิบายการเข้าเงื่อนไข ณ เวลานั้น มีอายุข้อมูลตาม grant ของเหตุที่แจ้ง |

สร้าง แก้กฎ หรือเปิดกฎที่พักไว้แล้วเพิ่ม revision พร้อมบันทึกฐานเริ่มต้นโดยไม่สร้างข้อความย้อนหลัง เก็บ `baseline_position` ที่ตรงกับ observation ล่าสุดที่ commit แล้ว สร้างฐานจากข้อมูลถึงตำแหน่งนั้นภายใต้ snapshot เดียวกัน ประเมินทุก observation ที่ commit หลังตำแหน่งนั้นตามลำดับ รุ่นแรกใช้ transaction lock เดียวกันตอนจัดสรร commit_position ให้ observation และตอนตั้งฐานกฎ เมื่อได้ lock ให้อ่านข้อมูลที่ commit ล่าสุดและถือ lock จนจบ transaction ก่อนปล่อย writer ถัดไป วิธีนี้ปิดช่องที่งานนำเข้าระหว่างตั้งฐานอาจหลุดหาย การใช้เวลาบนนาฬิกาหรือเลข sequence ที่จองไว้ก่อน commit อย่างเดียวไม่รับประกันลำดับนี้ เมื่อระบบพบชิ้นใหม่เข้าเงื่อนไข หรือได้ราคาใหม่ที่ลงมาถึงเพดานหลังเคยเกินงบ ให้บันทึกหนึ่ง alert สำหรับการเปลี่ยนครั้งนั้น ถ้าข้อมูลเคยเข้าเกณฑ์แล้วหายไปชั่วคราว การกลับมาอ่านค่าเดิมได้ไม่สร้างการแจ้งซ้ำ เช่น “5,800 → อ่านราคาไม่ได้ → 5,800” ราคาที่พิสูจน์ได้ว่าออกนอกเงื่อนไขแล้วกลับเข้าใหม่เป็นคนละครั้ง

ล็อกแถวสถานะของคู่กฎกับชิ้น หรือใช้ transaction ที่ serialize การประมวลผลคู่นั้น ใช้ `cause_key` คงที่เมื่อ replay จากชนิดเหตุและ ID ของหลักฐานการเปลี่ยนต้นทางเดียวกัน บังคับ unique index แยกสองเงื่อนไข `(rule_id, rule_revision, item_id, cause_key) WHERE rule_id IS NOT NULL` และ `(watch_id, item_id, cause_key) WHERE watch_id IS NOT NULL` บันทึก alert กับการเลื่อน state ใน transaction เดียวกัน งานเก่าที่เสร็จช้าเติมประวัติย้อนหลังได้ตามข้อ 4 และแจ้งผู้ซื้อเฉพาะเมื่อสถานะปัจจุบันหลังสร้าง sale_events ใหม่ต่างจากก่อนหน้า

หน้า **เทียบดีล** รับ item_id ต่างกัน 2–3 ชิ้น ตรวจสิทธิ์ทุกชิ้นก่อนอ่าน แล้วประกอบผลจาก current_items, price_facts, tags, seller และหลักฐานที่อ่านได้ ใช้ค่าของรอบที่แสดงพร้อมเวลา แสดงคำกล่าวเรื่องประกันเฉพาะที่มีหลักฐาน หน้านี้อ่านผล ณ เวลาขอ จึงไม่มีตารางสำหรับชุดเปรียบเทียบใน MVP

## 6. เส้นทางอ่าน ดัชนี และการลบ

| เส้นทาง | ดัชนีหรือกฎที่ต้องใช้ |
|---|---|
| คิวงานถึงกำหนด | `jobs(due_at, id) WHERE state = 'queued'` พร้อมดัชนีตาม lease_until ของงานที่กำลังรัน ใช้ logical_key กันการตั้ง revisit ซ้ำเมื่อมีทั้ง watch ระดับโพสต์และระดับชิ้น |
| เปิดประวัติของโพสต์หรือผู้ขาย | `observations(post_id, observed_at DESC, id)`, `posts(seller_id, group_id)`, `evidence(observation_id, local_key)` และ lookup ที่ grant_id สำหรับปิดการอ่านและ purge |
| หารุ่นและกราฟราคา | `listing_items(model_key, variant, vram_gb, condition)`, `price_facts(item_id, observation_id) WHERE price_kind = 'ask'` ลดจำนวนรายการก่อนตรวจสิทธิ์และคำนวณกลุ่มราคา วัดแผน query กับปริมาณใน SRS ก่อนเพิ่มดัชนี |
| ประเมินกฎและเปิดกล่องแจ้งเตือน | ดัชนี `rule_item_states(item_id, rule_id)` สำหรับค้นกฎที่เคยประเมินชิ้นนั้น เมื่อพบชิ้นใหม่ให้ตรวจทุกกฎที่เปิดใช้ สำหรับกล่องข้อความใช้ `alerts(created_at DESC, id)` รองรับ cursor และ partial index สำหรับ `read_at IS NULL` |
| เพิกถอน ลบ และกู้คืน | `purge_jobs` เก็บ `id`, `scope_kind`, `scope_key`, `requested_at`, `state`, `completed_at?`, `result_counts jsonb` เก็บเฉพาะคีย์ที่จำเป็นต่อการสั่งลบซ้ำและผลรวม |

การอ่าน UI, API และ MCP เริ่มจาก grant ที่ยังอนุญาตให้ใช้ตามวัตถุประสงค์ ข้อมูลจากโพสต์เดียวกันอาจมาจากหลาย grant จึงตรวจถึง observation ของแต่ละ fact รวมถึงการอ่านข้อความใน alert เก่าด้วย หากถูกเพิกถอนให้ปิดการอ่านและหยุดงานใน scope ทันที จากนั้น purge หลักฐานและข้อความใน payload, facts, versions, tags, alerts และ state ที่อาศัยข้อมูลนั้น สร้าง sale_events ของชิ้นที่กระทบใหม่จาก status_facts ที่เหลือ คำนวณ current_items, ประวัติผู้ขาย และแนวโน้มใหม่จากหลักฐานที่ยังเข้าถึงได้ ถ้าโพสต์หรือชิ้นหมดหลักฐานให้นำ watch ของระเบียนนั้นออก กฎตั้งรับซึ่งผู้ซื้อสร้างเองคงอยู่ได้

ไฟล์ที่แชร์ผ่าน hash ลบเมื่อเอา reference ที่ถูกเพิกถอนออกแล้วไม่เหลือ reference ที่ยังต้องเก็บ ทุกการอ่านไฟล์ต้องผ่าน evidence และ grant ของคำขอ เมื่องานลบสำเร็จให้เก็บบันทึกคำสั่งลบที่ไม่มีข้อความประกาศไว้แยกจากชุด backup ที่อาจกู้คืน แล้ว replay คำสั่งดังกล่าวก่อนเปิดให้ค้นข้อมูลจากชุดที่กู้คืน [ข้อกำหนดการลบและคืนระบบ](../requirements/SRS.md)

## 7. ตัวอย่างสำหรับทดสอบ migration และ service

ใช้กลุ่มจำลอง G1 ผู้ขายที่ยืนยันได้ S1 โพสต์ P1 มี I1 และ I2 ตามตารางแรก และกฎ R1 ที่เปิดไว้ก่อนส่งข้อมูล ตามลำดับนี้ผู้ทดสอบต้องย้อนจาก alert ถึงข้อความต้นทางใน observation ที่ถูกต้องได้

| การกระทำ | ผลในฐานข้อมูลและผลที่ผู้ซื้อเห็น |
|---|---|
| ส่ง P1 พร้อมราคา I1 6,300 และ I2 7,900 ยืนยันการส่งเดิมซ้ำหลัง timeout | มีโพสต์ 1 แถว สินค้า 2 ชิ้น observation 1 แถว R1 ยังไม่มี alert |
| ส่ง P1 รอบใหม่พร้อมข้อความ “3060 5,800 ส่ง 150” | I1 เดิมมี ask 5,800 และ shipping 150 อ้าง O2 ยอดรวม 5,950 บาท มี 1 alert ว่า ask เข้าเกณฑ์ 6,000 ยืนยันคำขอเดิมซ้ำแล้วจำนวน alert คงที่ |
| แนบข้อความของผู้สนใจ “จองครับ” จากนั้นส่งคำยืนยันผู้ขายว่า “3070 ขายแล้ว” | ข้อความแรกคงเป็นกิจกรรมผู้สนใจ ข้อความหลังเปลี่ยนสถานะ I2 เท่านั้น การติดตาม I1 ไม่แจ้งว่าขายแล้ว |
| เปรียบเทียบ I1 กับชิ้นจากอีกโพสต์ซึ่งยังไม่ทราบค่าส่ง | I1 แสดง 5,950 บาทเมื่อเงื่อนไขส่งยังใช้ได้ อีกชิ้นแสดง ask พร้อม “ยังไม่ทราบยอดรวม” |
| ถอน grant ของ O2 ขณะที่ worker ยังประเมินกฎ | งานในสิทธิ์เดิม commit ไม่ได้ alert และรูปจาก O2 อ่านไม่ได้ ตรวจผลอีกครั้งหลัง purge และ restore ก่อนเปิดใช้ API |

ผลในตารางเป็นคำตอบคาดหวังสำหรับชุดข้อมูลสังเคราะห์ การตรวจรับต้องแนบ migration, รุ่น fixture และผลการรันจริงในงานพัฒนา [ตารางตรวจรับ](../quality/TRACEABILITY.md)
