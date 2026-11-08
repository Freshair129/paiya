# A9a · แผนที่เกณฑ์รับมอบระยะ A — รอบ 1

วันที่ตรวจ: 2026-11-08  
Commit ที่ทำแผนที่: `46408054f3b8f407096582365762c3a1614fad39`  
สาขา: `test/a9a-acceptance-map`  
ชุดตรวจ: source tests ทั้งหมดใต้ `tests/`, Vitest ใต้ `web/src/*.test.tsx` และ Playwright E2E ใต้ `web/e2e/*.spec.ts`

เอกสารต้นทาง: [SRS](../../../../requirements/SRS.md), [TRACEABILITY](../../../TRACEABILITY.md), [buyer acceptance](../../../buyer-acceptance.md), [ROADMAP](../../../../delivery/ROADMAP.md) และรูปแบบรายงานเดิม [AT-05 evidence](../../AT-05/2026-11-07/README.md)

## ขอบเขตและวิธีจัดประเภท

เอกสารนี้เป็นแผนที่ข้อกำหนดกับ assertion ที่มีอยู่ ณ commit ข้างต้น ไม่ใช่ผลรับมอบ A9 และไม่มีการรันเทสต์เพื่อสร้างผลรับมอบรอบนี้ ผลจาก automated tests ทั้งหมดที่อ้างเป็น fixture สังเคราะห์

- **F covered** — มี automated test ใช้ fixture สังเคราะห์และ assert พฤติกรรมที่ตรงกับข้อนั้น
- **F gap** — ตรวจด้วย fixture ได้ แต่ยังไม่มี assertion ตรงข้อ หรือมีเพียงบางส่วน
- **L corpus** — ต้องวัดด้วย corpus ที่มนุษย์ติดป้ายอย่างน้อย 100 กรณีตาม A9b; Q1 ใน Issue #72 ยังรอผู้ดูแล
- **P pilot** — ต้องวัดกับเครื่องหรือการทำงานจริงตาม A9c; ไม่ใช่เกณฑ์ fixture ของ A9a

การตรวจ `tests/test_scenarios.py` พบว่าเป็น mutation test ของ validator สำหรับ schema และเฉลยใน fixture; ไม่นับเป็น assertion ของ runtime สถานะ ส่วนการตรวจรับที่อ้างด้านล่างอ่าน assertion ใน test body โดยตรง

## AT-01 · จัดการกลุ่ม

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-01.1 | เปิด 5 กลุ่มที่แต่ละกลุ่มมี grant | **F gap** — `tests/test_a3_queue.py::test_group_limit_concurrent_and_persistence` assert ว่ามี 5 กลุ่มและกลุ่มที่ 6 ถูกปฏิเสธ แต่ไม่ assert ว่าแต่ละกลุ่มมี grant ที่ใช้งานได้ |
| AT-01.2 | รองรับรอบ 30/60 นาที | **F covered** — `tests/test_a3_queue.py::test_global_lease_excludes_other_group_and_owner_can_complete` สร้างกลุ่มรอบ 30 และ 60 นาที แล้ว assert เวลางาน due ของทั้งสองกลุ่ม |
| AT-01.3 | เปลี่ยนรอบและ pause มีผลหลัง restart | **F gap** — `tests/test_a3_queue.py::test_group_limit_concurrent_and_persistence` เปิด Engine ใหม่และ assert ค่า poll 60 กับ enabled=false ที่อ่านจากฐานข้อมูล แต่ไม่ได้ restart scheduler/process แล้ว assert งานที่ถูก schedule หลัง restart |
| AT-01.4 | งานค้างตรวจ grant ซ้ำก่อน claim | **F covered** — `tests/test_a3_queue.py::test_revoked_grant_cancels_claim` assert ว่า claim ไม่คืนงาน, job ถูก cancel และ run ระบุ `grant_unavailable` เมื่อ revoke grant ก่อน claim |
| AT-01.5 | เริ่มทดสอบจาก fixture โดยไม่ login จริง | **F gap** — การทดสอบ discovery ใช้ `FeedScriptAdapter`/fixture แต่ไม่มี test ที่ assert ว่า login/authentication หรือคำขอไปต้นทางเกิดศูนย์ครั้ง |

## AT-02 · ค้นพบ

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-02.1 | พบ stable posts ที่คนติดป้ายว่าโหลดได้ไม่น้อยกว่า 95% | **L corpus** — `tests/test_a3_discovery.py::test_coverage_cases` และ `test_labelled_feed_replay` คำนวณ recall กับ label ชุดเล็กใน fixture; ยังไม่มี corpus ที่คนตรวจแล้ว ≥100 กรณี |
| AT-02.2 | รายงานตัวเศษและตัวหาร | **F gap** — `test_coverage_cases` พิมพ์เศษ/ส่วนสำหรับกรณีเล็ก และ `test_labelled_feed_replay` คำนวณค่าในตัวแปร แต่ไม่มี assertion ของรายงานที่เก็บซ้ำได้หรือรูปแบบ output ที่ให้เศษ/ส่วน |
| AT-02.3 | จัดการ pinned ให้ coverage/overlap ตรง fixture | **F covered** — `tests/test_a3_discovery.py::test_coverage_cases` และ `test_excluded_cards_do_not_break_overlap_run` assert overlap กับ coverage เมื่อมี pinned card |
| AT-02.4 | การเรียงใหม่ยังพบ overlap ได้ | **F covered** — `tests/test_a3_discovery.py::test_coverage_cases` ใช้ `reordered_pinned` แล้ว assert `overlap_reached` และ coverage เป็น complete |
| AT-02.5 | sort ที่ยืนยันไม่ได้รายงาน partial และไม่เลื่อน checkpoint | **F covered** — `tests/test_a3_discovery.py::test_coverage_cases` assert `sort_unverified`, ไม่ advance checkpoint และ checkpoint คงเดิม |
| AT-02.6 | feed หยุดก่อน overlap รายงาน partial ตาม fixture | **F covered** — `tests/test_a3_discovery.py::test_feed_end_before_overlap_preserves_checkpoint` และ `test_coverage_cases` assert `feed_stopped_before_overlap` และรักษา checkpoint |

## AT-03 · สถานะ

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-03.1 | อัตราจับคู่ seller/post/item ผิดเป็นศูนย์ในชุดติดป้าย | **L corpus** — มี targeted fixtures สำหรับ identity และ item mapping แต่ไม่มีผลอัตราผิดบน corpus ที่คนติดป้าย ≥100 กรณี |
| AT-03.2 | อัตรายืนยัน sold ผิดเป็นศูนย์ในชุดติดป้าย | **L corpus** — ต้องวัดทั้ง corpus และชุดย่อยที่มีหลักฐานปิดขายชัดเจน ≥20 กรณี; Q1 ยังไม่อนุมัติเฉลยจากคน |
| AT-03.3 | พบ sold ถูกต้อง ≥95% ในชุดย่อยที่มีหลักฐานปิดขายชัดเจนอย่างน้อย 20 กรณี | **L corpus** — SRS ต้องมี positive control เพื่อกันระบบตอบ unknown ทั้งชุดแล้วผ่านเกณฑ์ผิดศูนย์; ยังไม่มีชุดติดป้ายผ่านคน |
| AT-03.4 | รายงาน coverage แยกสำหรับผู้ขายที่มี stable ID | **L corpus** — ต้องคำนวณ coverage จากป้ายของคนและระบุตัวหาร stable sellers; ไม่มีรายงานจาก corpus ปัจจุบัน |
| AT-03.5 | reserved → open ต้องมีหลักฐานของแต่ละเหตุการณ์ | **F covered** — `tests/test_a4a_status.py::test_golden_replay` ใช้ `S01-reserve-reopen` แล้ว assert สถานะและประวัติพร้อม evidence ID ที่ตรงกับแต่ละรอบ |
| AT-03.6 | ขายหนึ่งชิ้นในโพสต์หลายชิ้นไม่ปิดอีกชิ้น | **F gap** — `test_golden_replay` ของ `S03-multi-item` ทดสอบการ reserve 3060 ขณะที่ 3070 ยัง open แต่ไม่มีกรณีขายหนึ่งชิ้นและ assert ว่าอีกชิ้นไม่ถูกปิด |
| AT-03.7 | คอมเมนต์ผู้ซื้อ “จองครับ” ที่ติดบทบาท seller ไม่สร้าง reserved/sold | **F covered** — `tests/test_a4a_status.py::test_golden_replay` ของ S03 buyer-only assert ว่าสถานะยัง open และ history ไม่มี event ใหม่; `test_rejected_comment_evidence` assert ว่า evidence ที่ role ไม่ตรงไม่สร้าง sale event |
| AT-03.8 | actor_key ที่ไม่ตรง seller ไม่สร้างสถานะยืนยัน | **F covered** — `tests/test_a4a_status.py::test_rejected_comment_evidence` กรณี actor assert ผล rejected, `actor_mismatch` และจำนวน sale events เป็นศูนย์ |
| AT-03.9 | ระบุสาเหตุของหลักฐานที่ถูกปฏิเสธ | **F covered** — `tests/test_a4a_status.py::test_rejected_comment_evidence` assert ค่า `reason` ที่เก็บไว้ เช่น `role_mismatch` และ `actor_mismatch`; ยังไม่ระบุใน AT ว่าต้องแสดง reason ที่ API หรือ UI |
| AT-03.10 | ลิงก์ขาดต้องไม่ทำให้ปิดขาย | **F gap** — ไม่มี test ที่เอา fixture สถานะขายแล้วซึ่งขาดลิงก์ตามนิยามของ AT มา assert ว่าไม่เกิด sold event |

## AT-04 · คอมเมนต์และ gap

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-04.1 | 5 ความเห็นจากผู้เขียนที่ยืนยันได้คนเดียวแสดง 5/1 | **F covered** — `tests/test_a4b_timeline.py::test_five_comments_one_known_author` assert activity เป็น 5 ความเห็น, 1 ผู้เขียนที่รู้จัก และ timeline มี comment_added 5 รายการ |
| AT-04.2 | โหลดได้ 2 จาก 5 ต้องระบุ partial และแยกยอดที่ต้นทางรายงาน | **F covered** — `tests/test_a4b_timeline.py::test_partial_comments_and_changes` และ `test_golden_replay` ของ S04 assert reported count 5, เห็นจริง 2, known commenters 1 และ coverage partial |
| AT-04.3 | สถานะที่หายระหว่างรอบไม่สร้าง event สมมติ | **F covered** — `tests/test_a4a_status.py::test_golden_replay` ของ S04 assert current status unknown และ status history ว่าง |

## AT-05 · หลักฐานและ UI

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-05.1 | จาก feed ไปถึง crop และ permalink ไม่เกิน 3 แตะ | **F covered** — `web/e2e/mobile.spec.ts::AT-05 captured image within three taps` นับแตะจาก feed, assert จำนวนไม่เกิน 3 และยืนยันว่ารูปกับลิงก์ต้นทางอยู่ใน viewport |
| AT-05.2 | ภาพ MCP ผูกกับ post/observation เดียวกับ read path ของ UI | **F covered** — `tests/test_a6c_mcp.py::test_media_integrity_and_scope` assert bytes ของ MCP image เท่ากับ API media และ evidence เชื่อมกับ post/observation เดียวกัน; `web/e2e/mobile.spec.ts::AT-05 captured image within three taps` เดินตาม evidence link ที่ UI แสดง |
| AT-05.3 | ภาพหายต้องมีเหตุผล | **F covered** — `tests/test_a6c_mcp.py::test_missing_image_reasons` assert reason ของ MCP metadata สำหรับ missing/capture_failed/not_requested; `web/e2e/mobile.spec.ts::capture failed preserves reason, quote and permalink` assert เหตุผลบนหน้า UI |
| AT-05.4 | ที่ 360 CSS px อ่านราคา/สถานะได้โดยไม่เลื่อนแนวนอน | **F covered** — `web/e2e/mobile.spec.ts::mobile pages have no horizontal overflow and accessible touch targets` assert ราคาและสถานะที่การ์ด พร้อม scrollWidth ไม่เกิน 360 บนหน้าที่ทดสอบ |
| AT-05.5 | ปุ่มหลักอ่านและกดได้ที่ 360 CSS px | **F gap** — E2E ตรวจขนาด touch target ของ control ที่แสดง แต่ไม่มี assertion ระบุว่าปุ่มหลักใดต้องเห็นและทำงานได้จากหน้า feed |

## AT-06 · ตัวตนและ tags

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-06.1 | ชื่อแสดงเหมือนกันแต่ stable keys ต่างกันยังเป็นคนละ seller | **F covered** — `tests/test_a4c_seller.py::test_distinct_keys_and_scopes_never_merge_sellers` ใช้ display name เดียวกันกับ actor keys ต่างกัน และ assert seller IDs ทั้งสามไม่ซ้ำ |
| AT-06.2 | ข้ามกลุ่มเมื่อยืนยัน identity ไม่ได้ต้องแยก scope | **F gap** — `test_seller_api_context_and_errors` ทดสอบ seller ที่มี stable identity เดียวกันข้ามกลุ่ม ส่วน `test_distinct_keys_and_scopes_never_merge_sellers` ทดสอบ key/scope ต่างกันในกลุ่มเดียว; ไม่มี assertion สำหรับ unknown identity ข้ามกลุ่ม |
| AT-06.3 | tags บอกที่มาของค่าได้ | **F covered** — `tests/test_a6a_extraction.py::test_first_matching_eligible_evidence_and_replay` assert tag ผูกกับ evidence key ที่ตรง quote; `test_contract_examples_through_ingest` assert ผล tags และ reason จาก fixture |
| AT-06.4 | ข้อความแทรกคำสั่งในโพสต์สั่งเรียก tool ไม่ได้ | **F gap** — `tests/test_a6c_mcp.py::test_untrusted_quote_is_fenced_and_tools_do_not_write` assert quote ถูก fence และ tool ไม่เขียนข้อมูล แต่ไม่มี test ของ boundary ที่จะพิสูจน์ว่า agent/tool router ไม่เรียก tool จากข้อความนั้น |

## AT-07 · ข้อมูลราคา

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-07.1 | amount ตรง ≥95% ใน subset ที่คนติดป้ายว่าระบุชัด | **L corpus** — ต้องวัดบน corpus ที่คนตรวจแล้ว; trend tests ใช้ amount ที่เตรียมเป็น price fact แล้ว ไม่ได้วัดการแยก amount จากข้อความ |
| AT-07.2 | currency ตรง ≥95% ใน subset เดียวกัน | **L corpus** — ต้องวัด amount/currency พร้อมตัวหารบนชุดที่คนติดป้าย; A7b ด้าน parser ถูกย้ายไป B1a |
| AT-07.3 | จับคู่ราคากับ item ถูก ≥95% | **L corpus** — ไม่มีอัตราความแม่นยำ item ต่อ corpus ที่คนติดป้าย |
| AT-07.4 | รายงานจำนวน subset ที่นำมาคิด | **L corpus** — จำนวนและผลต้องออกจาก harness ของ A9b หลัง Q1 อนุมัติการติดป้าย |
| AT-07.5 | shipping, deposit, offer และ bundle ไม่เข้า comparables ของการ์ดแยกชิ้น | **F covered** — `tests/test_a7a_trends.py::test_non_ask_price_is_excluded` assert deposit/shipping/offer ไม่เพิ่ม n; `test_window_edges_and_excluded_units_ranges` assert bundle และ range ถูกตัดออก |

## AT-08 · Replay และโหลด

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-08.1 | replay งานเดิมไม่เพิ่ม event ซ้ำ | **F covered** — `tests/test_a4a_status.py::test_golden_replay` replay envelope เดิมแล้ว assert counts และ sale event rows ไม่เปลี่ยน |
| AT-08.2 | ประวัติ A → B → A อยู่ครบ | **F covered** — `test_golden_replay` ของ S01 assert ประวัติ open/reserved/open และ current เป็น open |
| AT-08.3 | งานเก่าที่มาช้ากว่าไม่ทับข่าวใหม่ | **F covered** — `test_golden_replay` ของ S02 ใช้ O1/O3/O2 arrival order แล้ว assert history เรียงตาม effective time และ current status เป็น open |
| AT-08.4 | O2 ที่มาทีหลังไม่สร้างแจ้งเตือน | **F gap** — S02 assert ประวัติย้อนหลังมี reserved จาก O2 และ current เป็น open แต่ไม่มี assertion ของ notification/alert ที่ถูกส่งหรือไม่ถูกส่ง |
| AT-08.5 | model error ไม่บล็อก ingest และ API reads | **F covered** — `tests/test_a6b_enrichment.py::test_failing_model_does_not_block_ingest_or_reads` จำลอง HTTP 500, ingest fixture อีกโพสต์ และ assert posts/models API ยังคืนข้อมูลสองโพสต์ |
| AT-08.6 | collector ยัง discover ได้เมื่อ Ollama ใช้ไม่ได้ | **F gap** — test ข้างต้น เรียก ingest โดยตรง ไม่ได้รัน discovery กับ feed fixture ขณะที่ enrichment ล้มเหลว |
| AT-08.7 | UI ยังค้นข้อมูลเดิมได้เมื่อ Ollama ใช้ไม่ได้ | **F gap** — มี API read หลัง model failure; `web/src/a6d-filters.test.tsx::model load failure leaves other filters usable` ตรวจเพียงว่าตัวกรองอื่นใช้ได้เมื่อ models endpoint fail โดยไม่มีผลค้นโพสต์เดิม |
| AT-08.8 | ยืนยันกับ Ollama จริงที่ปิดใช้งาน | **P pilot** — mock HTTP 500 เป็น fixture coverage; การปิด service Ollama จริงและยืนยันผล end-to-end เป็น pilot |
| AT-08.9 | เก็บ CPU/RAM/VRAM, backlog และ poll lateness | **P pilot** — ต้องวัดระหว่าง run จริงบนเครื่องเป้าหมาย ไม่ได้พิสูจน์ด้วย fixture |

## AT-09 · สิทธิ์และการลบ

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-09.1 | revoke ระหว่าง capture แล้ว commit ไม่ผ่าน | **F covered** — `tests/test_a8a_revocation.py::test_revoke_between_ingest_checks_rolls_back_commit` assert error เป็น `grant_unavailable` และจำนวน observation/evidence/facts/jobs คงเดิม |
| AT-09.2 | revoke แล้วงานคิวที่ยัง active หยุด | **F covered** — `tests/test_a8a_revocation.py::test_cli_revoke_cancels_only_active_jobs_and_is_idempotent` assert queued/leased jobs ถูก cancel; `test_revoked_read_endpoints_and_all_mcp_tools` ครอบคลุม read/MCP |
| AT-09.3 | API/MCP และ media ที่เคยเปิดอ่านซ้ำไม่ได้ | **F covered** — `tests/test_a8a_revocation.py::test_revoked_read_endpoints_and_all_mcp_tools` เปิด media ก่อน revoke แล้ว assert API ทุก endpoint และ MCP tools ปฏิเสธหลัง revoke |
| AT-09.4 | purge แล้วสถิติไม่รวมข้อมูลที่ลบ | **F covered** — `tests/test_a8b_purge.py::test_reads_after_purge_match_b_only_baseline` assert models, price trend และ seller read หลัง purge เท่ากับ baseline ของ grant B |
| AT-09.5 | restore ไม่ทำให้ข้อมูลที่ลบกลับมาค้นได้ | **F covered** — `tests/test_a8c_replay.py::test_restore_drill_replays_original_command` ยืนยัน startup ถูกกั้นจน replay purge log แล้ว assert post/evidence/media ที่ลบไม่พบผ่าน API/MCP และไม่โผล่ใน search/stats |

## AT-10 · ราคาและ Watch

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-10.1 | poll ซ้ำของรายการเดิมไม่เพิ่ม n | **F covered** — `tests/test_a7a_trends.py::test_first_ask_repoll_and_replay` assert first ask เป็นตัวอย่างเดียว; poll ราคาใหม่และ replay capture เดิมแล้ว current n ไม่เพิ่ม |
| AT-10.2 | discovery poll ปกติของโพสต์ใหม่ที่ไม่ติดดาวยังทำงานใน 24 ชั่วโมงแรก | **F gap** — `tests/test_a4d_watches.py::test_new_unwatched_post_revisits_only_in_first_day` ตรวจช่วง quick revisit แต่ไม่ assert ว่า FeedScriptAdapter discover และ ingest โพสต์ต่อใน poll ปกติภายใน 24 ชั่วโมง |
| AT-10.3 | sample ต่ำกว่าเกณฑ์ขึ้น insufficient data | **F covered** — `tests/test_a7a_trends.py::test_thresholds` assert 9 offers หรือ 10 offers/2 sellers ได้ `insufficient_data` และค่ากลางเป็น null |
| AT-10.4 | hot watch ใช้ slot 10 นาทีภายใต้ cap | **F covered** — `tests/test_a4d_watches.py::test_hot_and_new_post_share_one_revisit_per_slot` assert slot 10 นาที; `test_watch_idempotency_hot_cap_and_expiry` assert hot-watch limit 10 รายและปฏิเสธรายที่ 11 |
| AT-10.5 | hot expiry หยุด revisit แต่ discovery ทำต่อ | **F covered** — `tests/test_a4d_watches.py::test_hot_slots_stop_after_expiry_while_discovery_continues` ทำ discovery ที่เวลา expiry แล้ว assert ไม่มี queued/leased revisit หลัง expiry |
| AT-10.6 | watch เกิน 24 ชั่วโมง revisit วันละครั้งและอยู่ใน budget | **F covered** — `tests/test_a4d_watches.py::test_revisit_executor_records_access_and_preserves_or_updates_status` assert revisit ถัดไป +1 วัน, ไม่มีงานซ้ำที่ +10 นาที และมี queued job เดียววันถัดไป; `test_budget_oldest_first_unique_index_and_discovery_priority` assert budget 10, oldest-first และ discovery priority |
| AT-10.7 | เมื่อล่าช้า UI บอก “ไม่ได้ตรวจตั้งแต่” เวลาสำเร็จล่าสุด | **F covered** — `tests/test_a4d_watches.py::test_watches_api_is_scoped_and_reports_overdue` assert overdue_since เท่ากับ next_due_at และ last_success_at ตรงเวลา; `web/src/cards.test.tsx::watch overdue_since forces stale indication even before poll interval` assert ข้อความใช้เวลาสำเร็จล่าสุด |
| AT-10.8 | slow revisit หยุดเมื่อทุกชิ้นที่ติดตามขายแล้ว | **F gap** — `test_slow_revisit_stops_when_sold_and_resumes_after_reopen` ทดสอบหนึ่งชิ้น sold; `test_partially_sold_post_revisits_until_all_items_closed` ใช้หนึ่ง sold กับอีกหนึ่ง withdrawn ไม่ได้ assert กรณีทุกชิ้นในโพสต์หลายชิ้นยืนยัน sold |

## สรุปจำนวน clause

| AT | F covered | F gap | L corpus | P pilot | รวม |
|---|---:|---:|---:|---:|---:|
| AT-01 | 2 | 3 | 0 | 0 | 5 |
| AT-02 | 4 | 1 | 1 | 0 | 6 |
| AT-03 | 4 | 2 | 4 | 0 | 10 |
| AT-04 | 3 | 0 | 0 | 0 | 3 |
| AT-05 | 4 | 1 | 0 | 0 | 5 |
| AT-06 | 2 | 2 | 0 | 0 | 4 |
| AT-07 | 1 | 0 | 4 | 0 | 5 |
| AT-08 | 4 | 3 | 0 | 2 | 9 |
| AT-09 | 5 | 0 | 0 | 0 | 5 |
| AT-10 | 6 | 2 | 0 | 0 | 8 |
| **รวม** | **35** | **14** | **9** | **2** | **60** |

ยอดนี้เป็นจำนวน clause ในแผนที่ ไม่ใช่จำนวนเทสต์หรือผลผ่าน

## ช่องว่างสำหรับรอบ 2 · tests

| Clause | เทสต์ที่เสนอและ assertion | Fixture ที่ใช้ |
|---|---|---|
| AT-01.1 | เพิ่ม `tests/test_a3_queue.py::test_five_grant_backed_groups`; assert 5 กลุ่มมี grant active/read/capture ครบ และกลุ่มที่ 6 ถูกปฏิเสธ | ใช้ helper `add_group` และฐาน `pg_engine` เดิม |
| AT-01.3 | เพิ่ม restart test ใน `tests/test_a3_queue.py`; เปลี่ยน interval, pause, สร้าง scheduler/Engine ใหม่ แล้ว assert due time หลัง restart, ไม่มีงานช่วง pause และ resume สร้างงานตาม interval ใหม่ | ใช้ setup กลุ่มสังเคราะห์เดิม |
| AT-01.5 | เพิ่ม test ใน `tests/test_a3_discovery.py` ที่รัน `FeedScriptAdapter` พร้อม fail-fast network/auth spy; assert ingest ผ่านและไม่มีการเรียก login/HTTP ต้นทาง | ใช้ `fixtures/feeds` และ sample envelope เดิม |
| AT-02.2 | เพิ่ม test ของ A9b report/harness ให้ assert output มี numerator และ denominator ที่คำนวณจาก label set | ใช้ feed fixtures สังเคราะห์เดิมทดสอบ format; corpus จริงรอ Q1 |
| AT-03.6 | เพิ่มรอบใน `fixtures/scenarios/S03-multi-item` ที่ seller ยืนยัน sold ให้ 3060 ชิ้นเดียว; assert 3060 sold และ 3070 ยัง open | ใช้ S03 เป็นฐานและเพิ่มหนึ่ง synthetic round |
| AT-03.10 | เพิ่ม case ใน `tests/test_a4a_status.py` ที่ขายแล้วแต่ขาด “ลิงก์” ตามนิยามที่ตกลง; assert ไม่มี confirmed sold event และเก็บ reason | ใช้ S03 seller confirmation เป็นฐานแล้วตัดลิงก์ที่ระบุ |
| AT-05.5 | เพิ่ม E2E ใน `web/e2e/mobile.spec.ts` ที่ viewport 360×800 assert ปุ่มหลักที่กำหนดแล้วมองเห็น, focus/activate ได้ และไม่มี horizontal overflow | ใช้ synthetic demo และ local request guard เดิม |
| AT-06.2 | เพิ่ม `tests/test_a4c_seller.py::test_unknown_identity_stays_group_scoped`; สร้าง seller display name ซ้ำแต่ไม่มี stable actor identity คนละกลุ่ม แล้ว assert IDs/seller pages แยกกัน | ใช้ `envelope` และ grants เดิม ปรับ fixture ให้ actor identity unknown |
| AT-06.4 | เพิ่ม deterministic tool-boundary test ใน `tests/test_a6c_mcp.py`; ส่ง synthetic quote ที่สั่งเรียก tool ผ่าน boundary ของ agent/tool router จำลอง แล้ว assert ไม่มี tool invocation/DB write | ใช้ quote และ fixture ของ `test_untrusted_quote_is_fenced_and_tools_do_not_write`; ต้องมี mock boundary ที่ระบุชัด |
| AT-08.4 | เพิ่ม test ที่ขอบเขต event→notification โดยใช้ S02; assert O2 ยังอยู่ใน historical events แต่ไม่มี notification สำหรับ current=open | ใช้ S02 late-arrival เดิม; ต้องระบุ notification sink ที่จะตรวจ |
| AT-08.6 | เพิ่ม test รัน discover job ด้วย feed script พร้อม Ollama MockTransport error; assert discovery สำเร็จและ observation ใหม่ค้นคืนได้ | ใช้ `fixtures/feeds` และ mock pattern จาก `test_failing_model_does_not_block_ingest_or_reads` |
| AT-08.7 | เพิ่ม `web/e2e/mobile.spec.ts` test ให้ `/v1/models` fail ขณะมี synthetic posts; assert UI ยังแสดง/ค้นโพสต์เดิมและใช้ filter ที่ไม่ต้องใช้ model ได้ | ใช้ demo posts และ local request guard เดิม |
| AT-10.2 | เพิ่ม test ให้ unstarred post ถูก discover/ingest ด้วย FeedScriptAdapter ใน poll ปกติก่อนครบ 24h และยังถูกค้นพบหลัง quick revisit หมด | ใช้ `tests/test_a4d_watches.py` helper และ fixture feed เดิม |
| AT-10.8 | เพิ่ม multi-item case ใน `tests/test_a4d_watches.py` ที่ทุกชิ้นมี seller evidence เป็น sold; assert ไม่มี slow revisit หลังทุกชิ้น sold และมี revisit เมื่อยังเหลือชิ้นเปิด | ใช้ S03 multi-item และ helper `make_posts` แล้วเพิ่ม seller-confirmed rounds |

รวม F gap 14 ข้อ: 10 ข้อใช้ fixture/setup เดิมได้ และ 4 ข้อต้องเพิ่มหรือปรับ variant ของ fixture (AT-03.6, AT-03.10, AT-06.2, AT-10.8) ทั้งหมดเป็นข้อเสนอ ยังไม่มี test หรือ fixture ใหม่ในรอบนี้

## รายการวัด pilot สำหรับ A9c

ข้อมูลนี้แยกจาก fixture acceptance และไม่ควรนำค่า mock มาอ้างแทนผลจริง

1. **เครื่องเป้าหมาย:** บันทึก baseline และระบุ concurrency บน i7-8700K, RAM 32 GB, RTX 3060 12 GB ตาม SRS ก่อนขยาย concurrency
2. **ทรัพยากร:** เก็บ CPU, RAM peak/working set และ VRAM ใช้จริงตลอดการ run พร้อมเวลาเริ่ม/จบ และภาระที่กำลังรัน
3. **คิวและรอบตรวจ:** วัด queue depth/age, claim-to-complete, poll lateness เทียบเวลาที่ตั้งไว้, missed slots, machine-sleep gaps, retry และข้อผิดพลาด แยกตามกลุ่ม
4. **Ollama จริง:** ปิด service จริงระหว่าง discovery/enrichment; ยืนยัน collector ยัง discover ได้และ API/UI ยังอ่านข้อมูลเดิมได้ เก็บ model/service state, timeout/error และ backlog recovery หลังเปิดกลับ
5. **Read path:** วัด p95 ≤1 วินาทีสำหรับ query 50 รายการบนข้อมูลจำลอง 10,000 โพสต์เมื่อ DB อุ่น; ไม่นับการส่งรูป และรายงานขนาดรูปกับ time-to-first-image แยกตาม SRS
6. **มือถือจริง:** บันทึกรุ่นเครื่อง, OS/browser, viewport, การอ่านราคา/สถานะ/ปุ่ม, การแตะถึง crop/permalink และ horizontal overflow บนเครื่องจริง การทดสอบ viewport 360 CSS px ใน Playwright เป็น fixture evidence; การเปิดผ่านโทรศัพท์/HTTPS เป็น gate แยกตาม ROADMAP O5

## ถ้อยคำที่ยังตีความได้หลายแบบ

- AT-01: “`เปิดได้ 5 กลุ่มที่มี grant`” ไม่บอกว่า grant ต้อง active/unexpired และมี scope ขั้นต่ำใด; “`หลัง restart`” หมายถึง restart เฉพาะ process หรือเพียงเปิด DB connection ใหม่
- AT-02: “`stable posts ที่ผู้ทดสอบติดป้ายว่าโหลดให้เห็นในขอบเขตรอบนั้น`” ไม่กำหนด stable key, ขอบเขตรอบ และการนับ pinned/duplicate ที่แน่นอน; “`แสดงตัวเศษ/ตัวหาร`” ไม่กำหนดไฟล์หรือรูปแบบรายงาน
- AT-03: “`จับคู่ผู้ขาย/โพสต์/ชิ้นสินค้าผิด 0 กรณี`” ไม่ระบุ denominator; ชุดควบคุม sold กำหนด ≥20 กรณีแต่ไม่บอกวิธีสุ่มหรือเกณฑ์ “หลักฐานปิดขายชัดเจน”; “`ลิงก์ขาด`” ไม่ชี้ว่าหมายถึง permalink, source link หรือ evidence link
- AT-03: “`ต้องบอกสาเหตุ`” ไม่ระบุว่าจะต้องอยู่ใน DB, API หรือ UI; test ปัจจุบันยืนยันเฉพาะ reason ที่บันทึกใน status fact
- AT-04: “`แยกยอดที่หน้าเว็บรายงาน`” ไม่ระบุว่า UI ต้องแยก source-reported total, distinct comments seen หรือทั้งสองค่าอย่างไร
- AT-05: “`รูปที่ส่งผ่าน MCP ตรงกับ post/observation เดียวกับ UI`” ไม่บอกว่าต้องเทียบ bytes, evidence ID หรือเฉพาะความสัมพันธ์ของ post/observation
- AT-05: “`ปุ่มหลัก`” ไม่ระบุชื่อหรือ action ที่ต้องเห็น/กดได้ใน 360 CSS px
- AT-06: “`ข้อความแทรกคำสั่งในโพสต์เรียก tool ไม่ได้`” ไม่ระบุ agent/tool router ที่อยู่ใน acceptance boundary; test ปัจจุบันตรวจการ fence quote แต่ไม่มี agent runtime
- AT-07: “`subset ที่มนุษย์ติดป้ายว่าระบุชัด`” ไม่กำหนดเกณฑ์ clear, ชุดที่ตัดออกก่อนคำนวณ หรือวิธีรายงานกรณีระบบ unknown; Q1 ยังรอคำตอบ
- AT-08: “`ไม่เกิดแจ้งเตือนจาก O2`” ไม่ชี้ notification sink/event ใด และ “`ปิด Ollama`” ไม่แยกจำลอง service error จากปิด service จริง
- AT-10: “`ภายใต้ cap`” และ “`ภายในงบ`” ไม่ระบุตัวเลขเดียวกันหรือไม่; test ปัจจุบันมี cap hot watch 10 และ scheduler budget 10 แต่ AT ไม่ชี้ว่าหมายถึงค่าใด
- AT-10: “`รอบช้าหยุดเมื่อชิ้นที่ติดตามทุกชิ้นขายแล้ว`” ไม่ระบุว่า withdrawn ถือเป็นปิดแล้วหรือจำเป็นต้องเป็น sold เท่านั้น; test ปัจจุบันมีทั้งกรณี sold และ withdrawn

## สถานะรอบนี้

แผนที่ผูกกับ commit ข้างต้น; ไม่มีการแก้ tests, fixtures, source, AT criteria, goldens หรือ SRS. A9b ยังรอคำตอบ Q1 ก่อนสร้างเฉลยสำหรับ corpus และเปอร์เซ็นต์ของ AT-02/03/07.
