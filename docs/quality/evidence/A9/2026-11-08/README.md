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
- **N/A in A** — ความสามารถยังไม่มีในระยะ A; ต้องตรวจอีกครั้งเมื่อมีความสามารถนั้น

การตรวจ `tests/test_scenarios.py` พบว่าเป็น mutation test ของ validator สำหรับ schema และเฉลยใน fixture; ไม่นับเป็น assertion ของ runtime สถานะ ส่วนการตรวจรับที่อ้างด้านล่างอ่าน assertion ใน test body โดยตรง

## AT-01 · จัดการกลุ่ม

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-01.1 | เปิด 5 กลุ่มที่แต่ละกลุ่มมี grant | **F covered** — `tests/test_a3_queue.py::test_five_grant_backed_groups` ตรวจ 5 grant ที่ยังใช้ได้และมี read/capture พร้อมปฏิเสธกลุ่มที่ 6 |
| AT-01.2 | รองรับรอบ 30/60 นาที | **F covered** — `tests/test_a3_queue.py::test_global_lease_excludes_other_group_and_owner_can_complete` สร้างกลุ่มรอบ 30 และ 60 นาที แล้ว assert เวลางาน due ของทั้งสองกลุ่ม |
| AT-01.3 | เปลี่ยนรอบและ pause มีผลหลัง restart | **F covered** — `tests/test_a3_queue.py::test_interval_and_pause_survive_scheduler_restart` dispose Engine สร้าง Engine ใหม่และเรียก scheduler entry ใหม่ใน process เดิม ตรวจ pause, resume และ slot 60 นาที; ไม่ใช่การ restart process |
| AT-01.4 | งานค้างตรวจ grant ซ้ำก่อน claim | **F covered** — `tests/test_a3_queue.py::test_revoked_grant_cancels_claim` assert ว่า claim ไม่คืนงาน, job ถูก cancel และ run ระบุ `grant_unavailable` เมื่อ revoke grant ก่อน claim |
| AT-01.5 | เริ่มทดสอบจาก fixture โดยไม่ login จริง | **F covered** — `tests/test_a3_discovery.py::test_fixture_start_path_avoids_external_authentication` ตรวจ demo seed, serve entry ผ่าน ASGI และ discovery ภายใต้ Python socket และ environment/file spies ตามขอบเขตที่บันทึกด้านล่าง |

## AT-02 · ค้นพบ

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-02.1 | พบ stable posts ที่คนติดป้ายว่าโหลดได้ไม่น้อยกว่า 95% | **L corpus** — `tests/test_a3_discovery.py::test_coverage_cases` และ `test_labelled_feed_replay` คำนวณ recall กับ label ชุดเล็กใน fixture; ยังไม่มี corpus ที่คนตรวจแล้ว ≥100 กรณี |
| AT-02.2 | รายงานตัวเศษและตัวหาร | **L corpus** — เป็นส่วนของ harness รายงาน A9b ซึ่งรอคำตอบ Q1 ใน Issue #72; ยังไม่ได้สร้าง harness หรืออ้างผล corpus |
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
| AT-03.6 | ขายหนึ่งชิ้นในโพสต์หลายชิ้นไม่ปิดอีกชิ้น | **F covered** — `tests/test_a4a_status.py::test_golden_replay[S09-sold-one-item-seller-sells-one-other-stays-open]` ตรวจ seller confirmation ว่า 3060 sold และ 3070 open พร้อม history และ evidence |
| AT-03.7 | คอมเมนต์ผู้ซื้อ “จองครับ” ที่ติดบทบาท seller ไม่สร้าง reserved/sold | **F covered** — `tests/test_a4a_status.py::test_golden_replay` ของ S03 buyer-only assert ว่าสถานะยัง open และ history ไม่มี event ใหม่; `test_rejected_comment_evidence` assert ว่า evidence ที่ role ไม่ตรงไม่สร้าง sale event |
| AT-03.8 | actor_key ที่ไม่ตรง seller ไม่สร้างสถานะยืนยัน | **F covered** — `tests/test_a4a_status.py::test_rejected_comment_evidence` กรณี actor assert ผล rejected, `actor_mismatch` และจำนวน sale events เป็นศูนย์ |
| AT-03.9 | ระบุสาเหตุของหลักฐานที่ถูกปฏิเสธ | **F covered** — `tests/test_a4a_status.py::test_rejected_comment_evidence` assert ค่า `reason` ที่เก็บไว้ เช่น `role_mismatch` และ `actor_mismatch`; ยังไม่ระบุใน AT ว่าต้องแสดง reason ที่ API หรือ UI |
| AT-03.10 | ลิงก์ขาดต้องไม่ทำให้ปิดขาย | **F covered** — `tests/test_a4a_status.py::test_sold_evidence_with_missing_source_link_is_rejected` ตรวจ permalink ที่หายและ evidence ที่อ้าง comment ซึ่งไม่มี; ทั้งสองถูกปฏิเสธพร้อมเหตุผลก่อนสร้าง observation หรือ sale event |

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
| AT-05.5 | ปุ่มหลักอ่านและกดได้ที่ 360 CSS px | **F covered** — `web/e2e/mobile.spec.ts::AT-05.5 primary mobile actions are visible and usable` ตรวจ focus, ขนาดแตะ, overflow และเปิด filter, post, evidence และ watch |

## AT-06 · ตัวตนและ tags

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-06.1 | ชื่อแสดงเหมือนกันแต่ stable keys ต่างกันยังเป็นคนละ seller | **F covered** — `tests/test_a4c_seller.py::test_distinct_keys_and_scopes_never_merge_sellers` ใช้ display name เดียวกันกับ actor keys ต่างกัน และ assert seller IDs ทั้งสามไม่ซ้ำ |
| AT-06.2 | ข้ามกลุ่มเมื่อยืนยัน identity ไม่ได้ต้องแยก scope | **F covered** — `tests/test_a4c_seller.py::test_unknown_identity_stays_unlinked_across_groups` ตรวจโพสต์สองกลุ่มชื่อผู้ขายซ้ำแต่ไม่มี stable key คง `seller_id = NULL` และแยก group ID ตามสัญญา Collector หัวข้อข้อมูลจาก Collector และการบันทึกและอ่านในระยะ A กับ data model ข้อ 3 |
| AT-06.3 | tags บอกที่มาของค่าได้ | **F covered** — `tests/test_a6a_extraction.py::test_first_matching_eligible_evidence_and_replay` assert tag ผูกกับ evidence key ที่ตรง quote; `test_contract_examples_through_ingest` assert ผล tags และ reason จาก fixture |
| AT-06.4 | ข้อความแทรกคำสั่งในโพสต์สั่งเรียก tool ไม่ได้ | **F covered** — `tests/test_a6c_mcp.py::test_injected_tool_call_is_data_at_every_mcp_boundary` ส่งข้อมูลจำลองผ่าน read services ของ MCP ทั้งห้า ตรวจ fence, read-only tool list, จำนวน calls และ SQL writes |

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
| AT-08.4 | O2 ที่มาทีหลังไม่สร้างแจ้งเตือน | **N/A in A** — `rg -n -i 'notification|notify|alert' src/paiya` ไม่พบผล; ระยะ A ไม่มี notification/alert sink และ alerts อยู่ใน AT-12 ระยะ M. `tests/test_a4a_status.py::test_golden_replay` ของ S02 ตรวจ history และ current แล้ว แต่ไม่ได้พิสูจน์การแจ้งเตือน; ตรวจข้อนี้อีกครั้งเมื่อมี alerts |
| AT-08.5 | model error ไม่บล็อก ingest และ API reads | **F covered** — `tests/test_a6b_enrichment.py::test_failing_model_does_not_block_ingest_or_reads` จำลอง HTTP 500, ingest fixture อีกโพสต์ และ assert posts/models API ยังคืนข้อมูลสองโพสต์ |
| AT-08.6 | collector ยัง discover ได้เมื่อ Ollama ใช้ไม่ได้ | **F covered** — `tests/test_a6b_enrichment.py::test_failed_enrichment_does_not_block_fixture_discovery` ทำ enrichment ล้มเหลวด้วย HTTP 500 จำลอง แล้วรัน FeedScriptAdapter และค้นคืนโพสต์ที่ ingest ใหม่ |
| AT-08.7 | UI ยังค้นข้อมูลเดิมได้เมื่อ Ollama ใช้ไม่ได้ | **F covered** — `web/e2e/mobile.spec.ts::AT-08.7 existing posts remain searchable when models fail` จำลอง models endpoint 503 และตรวจ filter, ผลโพสต์เดิมและเปิด detail |
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
| AT-10.2 | discovery poll ปกติของโพสต์ใหม่ที่ไม่ติดดาวยังทำงานใน 24 ชั่วโมงแรก | **F covered** — `tests/test_a3_discovery.py::test_regular_discovery_keeps_finding_unwatched_post_after_first_day` ตรวจ feed poll 0, 30 นาที และ 25 ชั่วโมง พร้อม observation และไม่มี watch |
| AT-10.3 | sample ต่ำกว่าเกณฑ์ขึ้น insufficient data | **F covered** — `tests/test_a7a_trends.py::test_thresholds` assert 9 offers หรือ 10 offers/2 sellers ได้ `insufficient_data` และค่ากลางเป็น null |
| AT-10.4 | hot watch ใช้ slot 10 นาทีภายใต้ cap | **F covered** — `tests/test_a4d_watches.py::test_hot_and_new_post_share_one_revisit_per_slot` assert slot 10 นาที; `test_watch_idempotency_hot_cap_and_expiry` assert hot-watch limit 10 รายและปฏิเสธรายที่ 11 |
| AT-10.5 | hot expiry หยุด revisit แต่ discovery ทำต่อ | **F covered** — `tests/test_a4d_watches.py::test_hot_slots_stop_after_expiry_while_discovery_continues` ทำ discovery ที่เวลา expiry แล้ว assert ไม่มี queued/leased revisit หลัง expiry |
| AT-10.6 | watch เกิน 24 ชั่วโมง revisit วันละครั้งและอยู่ใน budget | **F covered** — `tests/test_a4d_watches.py::test_revisit_executor_records_access_and_preserves_or_updates_status` assert revisit ถัดไป +1 วัน, ไม่มีงานซ้ำที่ +10 นาที และมี queued job เดียววันถัดไป; `test_budget_oldest_first_unique_index_and_discovery_priority` assert budget 10, oldest-first และ discovery priority |
| AT-10.7 | เมื่อล่าช้า UI บอก “ไม่ได้ตรวจตั้งแต่” เวลาสำเร็จล่าสุด | **F covered** — `tests/test_a4d_watches.py::test_watches_api_is_scoped_and_reports_overdue` assert overdue_since เท่ากับ next_due_at และ last_success_at ตรงเวลา; `web/src/cards.test.tsx::watch overdue_since forces stale indication even before poll interval` assert ข้อความใช้เวลาสำเร็จล่าสุด |
| AT-10.8 | slow revisit หยุดเมื่อทุกชิ้นที่ติดตามขายแล้ว | **F covered** — `tests/test_a4d_watches.py::test_slow_revisit_stops_when_both_items_are_seller_confirmed_sold` ตรวจ revisit เมื่อเหลือหนึ่งชิ้นเปิด และหยุดหลังทั้งสองชิ้นมี seller evidence ว่า sold |

## สรุปจำนวน clause

| AT | F covered | F gap | L corpus | P pilot | N/A in A | รวม |
|---|---:|---:|---:|---:|---:|---:|
| AT-01 | 5 | 0 | 0 | 0 | 0 | 5 |
| AT-02 | 4 | 0 | 2 | 0 | 0 | 6 |
| AT-03 | 6 | 0 | 4 | 0 | 0 | 10 |
| AT-04 | 3 | 0 | 0 | 0 | 0 | 3 |
| AT-05 | 5 | 0 | 0 | 0 | 0 | 5 |
| AT-06 | 4 | 0 | 0 | 0 | 0 | 4 |
| AT-07 | 1 | 0 | 4 | 0 | 0 | 5 |
| AT-08 | 6 | 0 | 0 | 2 | 1 | 9 |
| AT-09 | 5 | 0 | 0 | 0 | 0 | 5 |
| AT-10 | 8 | 0 | 0 | 0 | 0 | 8 |
| **รวม** | **47** | **0** | **10** | **2** | **1** | **60** |

ยอดนี้เป็นจำนวน clause ในแผนที่ ไม่ใช่จำนวนเทสต์หรือผลผ่าน

## ช่องว่างสำหรับรอบ 2 · tests

| Clause | เทสต์ที่เสนอและ assertion | Fixture ที่ใช้ |
|---|---|---|
| AT-01.1 | เพิ่ม `tests/test_a3_queue.py::test_five_grant_backed_groups`; assert 5 กลุ่มมี grant active/read/capture ครบ และกลุ่มที่ 6 ถูกปฏิเสธ | ใช้ helper `add_group` และฐาน `pg_engine` เดิม |
| AT-01.3 | เพิ่ม restart test ใน `tests/test_a3_queue.py`; เปลี่ยน interval, pause, สร้าง scheduler/Engine ใหม่ แล้ว assert due time หลัง restart, ไม่มีงานช่วง pause และ resume สร้างงานตาม interval ใหม่ | ใช้ setup กลุ่มสังเคราะห์เดิม |
| AT-01.5 | เพิ่ม test ใน `tests/test_a3_discovery.py` ที่รัน `FeedScriptAdapter` พร้อม fail-fast network/auth spy; assert ingest ผ่านและไม่มีการเรียก login/HTTP ต้นทาง | ใช้ `fixtures/feeds` และ sample envelope เดิม |
| AT-02.2 | ย้ายไป A9b corpus harness; รอ Q1 ใน Issue #72 | ยังไม่ได้สร้าง harness |
| AT-03.6 | ปิดด้วย `test_golden_replay` ของ S09 | scenario ใหม่ `S09-sold-one-item`; ไม่แก้ S03 |
| AT-03.10 | ปิดด้วย `test_sold_evidence_with_missing_source_link_is_rejected`; เหตุผลอยู่ใน validation error ก่อนบันทึก | fixture ลบ permalink หรือ comment reference แยกใน `fixtures/a9a-missing-source-link/` |
| AT-05.5 | เพิ่ม E2E ใน `web/e2e/mobile.spec.ts` ที่ viewport 360×800 assert ปุ่มหลักที่กำหนดแล้วมองเห็น, focus/activate ได้ และไม่มี horizontal overflow | ใช้ synthetic demo และ local request guard เดิม |
| AT-06.2 | เพิ่ม `tests/test_a4c_seller.py::test_unknown_identity_stays_group_scoped`; สร้าง seller display name ซ้ำแต่ไม่มี stable actor identity คนละกลุ่ม แล้ว assert IDs/seller pages แยกกัน | ใช้ `envelope` และ grants เดิม ปรับ fixture ให้ actor identity unknown |
| AT-06.4 | เพิ่ม deterministic tool-boundary test ใน `tests/test_a6c_mcp.py`; ส่ง synthetic quote ที่สั่งเรียก tool ผ่าน boundary ของ agent/tool router จำลอง แล้ว assert ไม่มี tool invocation/DB write | ใช้ quote และ fixture ของ `test_untrusted_quote_is_fenced_and_tools_do_not_write`; ต้องมี mock boundary ที่ระบุชัด |
| AT-08.4 | N/A in A; ตรวจอีกครั้งเมื่อมี alerts ในระยะ M | S02 ตรวจ history/current แล้ว แต่ไม่มี sink ให้ตรวจ |
| AT-08.6 | เพิ่ม test รัน discover job ด้วย feed script พร้อม Ollama MockTransport error; assert discovery สำเร็จและ observation ใหม่ค้นคืนได้ | ใช้ `fixtures/feeds` และ mock pattern จาก `test_failing_model_does_not_block_ingest_or_reads` |
| AT-08.7 | เพิ่ม `web/e2e/mobile.spec.ts` test ให้ `/v1/models` fail ขณะมี synthetic posts; assert UI ยังแสดง/ค้นโพสต์เดิมและใช้ filter ที่ไม่ต้องใช้ model ได้ | ใช้ demo posts และ local request guard เดิม |
| AT-10.2 | เพิ่ม test ให้ unstarred post ถูก discover/ingest ด้วย FeedScriptAdapter ใน poll ปกติก่อนครบ 24h และยังถูกค้นพบหลัง quick revisit หมด | ใช้ `tests/test_a4d_watches.py` helper และ fixture feed เดิม |
| AT-10.8 | เพิ่ม multi-item case ใน `tests/test_a4d_watches.py` ที่ทุกชิ้นมี seller evidence เป็น sold; assert ไม่มี slow revisit หลังทุกชิ้น sold และมี revisit เมื่อยังเหลือชิ้นเปิด | ใช้ S03 multi-item และ helper `make_posts` แล้วเพิ่ม seller-confirmed rounds |

รอบ 2b เพิ่มการตรวจและ mutation สำหรับทั้งเจ็ดข้อในรายการนี้ จึงไม่มี F gap เหลือในแผนที่; AT-02.2 อยู่ใน L corpus และ AT-08.4 เป็น N/A in A.

## การตีความที่ใช้

- AT-01.1: [สัญญาคิว ข้อ 2](../../../../architecture/contracts/discovery-queue.md) กำหนด grant ที่ใช้ได้และมี capture; เทสต์ตรวจ read ด้วยสำหรับการค้นคืนข้อมูล fixture
- AT-01.3: [สัญญาคิว ข้อ 3](../../../../architecture/contracts/discovery-queue.md) กำหนดสถานะคงอยู่หลัง restart; ใช้การ dispose Engine และสร้าง Engine กับ scheduler entry ใหม่ใน process เดิมเป็นขอบเขตที่ตรวจ
- AT-03.10: [สัญญา Collector หัวข้อข้อมูลจาก Collector](../../../../architecture/contracts/collector-and-query.md) และ [observation schema](../../../../architecture/contracts/observation.v1.schema.json) บังคับ permalink; [สัญญาสถานะ ข้อ 2](../../../../architecture/contracts/item-status-and-watches.md) ต้องอ้าง comment ของ seller เมื่อต้นทางเป็น comment. ตรวจทั้งสองกรณี; reason เป็น validation error ไม่ใช่ fact ที่บันทึก
- AT-06.2: [สัญญา Collector หัวข้อข้อมูลจาก Collector และการบันทึกและอ่านในระยะ A](../../../../architecture/contracts/collector-and-query.md) กับ [data model ข้อ 3](../../../../architecture/data-model.md) กำหนดว่าไม่มี stable actor key ให้คง seller ID เป็น NULL ไม่สร้าง seller จากชื่อ

- AT-01.5: [สัญญาคิว ข้อ 1 และ 6](../../../../architecture/contracts/discovery-queue.md) จำกัด adapter เป็น fixture; [สัญญา mobile ข้อ 1](../../../../architecture/contracts/mobile-read-path.md) กำหนด read context. ตีความว่า `demo seed`, `serve.main` และ FeedScriptAdapter ไม่อ่านชื่อ environment หรือชื่อไฟล์ที่มี credential, cookie, session, password, passwd, token, secret หรือ `.env`, และ Python `socket.connect`/`connect_ex` ไม่เชื่อม non-loopback. ดัก `os.environ.__getitem__`, `builtins.open`, `io.open`, `os.open` และ socket; ยกเว้น `PGTARGETSESSIONATTRS` ซึ่งเป็นตัวเลือก routing ของ PostgreSQL ไม่ใช่ session ต้นทาง. `serve.main` ใช้ ASGI TestClient แทน uvicorn listener; spy นี้ไม่ครอบ native I/O หรือ credential ที่ใช้ชื่ออื่น และไม่ใช่หลักฐาน pilot.
- AT-05.5: [สัญญา mobile ข้อ 3–4](../../../../architecture/contracts/mobile-read-path.md) ใช้ filter, card, evidence, permalink และ navigation ติดตามเป็น primary actions; ทดสอบ viewport 360 × 800 และ focus/ขนาด/การใช้งานใน Playwright. ลิงก์ต้นทางตรวจการ focus โดยไม่ออกไปยังต้นทาง.
- AT-06.4: [สัญญา MCP ข้อ 3 และ 5](../../../../architecture/contracts/mcp-read.md) กำหนดเครื่องมืออ่านและ fence. ขอบเขตคือ MCP server ไม่มี agent runtime; ใช้ synthetic post แล้ว stub read service แต่ละตัวให้ส่ง quote เดียวกัน ตรวจ calls และ SQL DML ใน server ไม่อ้างพฤติกรรม agent ภายนอก.
- AT-08.6–08.7: [สัญญา local model ข้อ 1 และ 8](../../../../architecture/contracts/local-model-enrichment.md) กับ [mobile ข้อ 3](../../../../architecture/contracts/mobile-read-path.md) ใช้ HTTP 500 ของ enrichment และ 503 ของ models endpoint จำลอง แยกจากการปิด Ollama จริงที่ต้องทดสอบใน pilot.
- AT-10.2: [สัญญาคิว ข้อ 3–4](../../../../architecture/contracts/discovery-queue.md) และ [สัญญา watch ข้อ 6](../../../../architecture/contracts/item-status-and-watches.md) แยก discovery ปกติออกจาก quick revisit; ใช้ feed script ใน temp ที่ 0, 30 นาทีและ 25 ชั่วโมง.

## ผลรันเพิ่มเติมรอบ 2b (A fixture)

ผลต่อไปนี้รันบนฐาน `f09cc73` พร้อม diff รอบ 2b; ไม่ใช่ผล pilot. Commit ผลงานคือ commit ที่บรรจุเอกสารนี้ (`git rev-parse HEAD` เมื่อ checkout commit นั้น); การฝัง SHA ของ commit เดียวกันไว้ในเนื้อหาจะเปลี่ยน SHA นั้นเอง:

| การตรวจ | ผลจริง |
|---|---|
| `uv run pytest -p no:cacheprovider tests/test_a6b_enrichment.py::test_failed_enrichment_does_not_block_fixture_discovery tests/test_a4d_watches.py::test_slow_revisit_stops_when_both_items_are_seller_confirmed_sold -q` | `2 passed in 5.23s` |
| Playwright fresh `paiya_e2e_a9`, port 8137, `--reporter=line` | `11 passed (7.9s)`; server หยุดและฐานชั่วคราวถูก drop แล้ว |

### ชุดตรวจเต็มรอบ 2b (A fixture)

ตั้ง `UV_OFFLINE=1`, `UV_NO_SYNC=1`, `COREPACK_ENABLE_NETWORK=0`; cache และ output ที่เลือกได้อยู่ใน system temp. Build ใช้ `--outDir` ไป temp เพื่อไม่สร้าง `web/dist`; Playwright ใช้ `--output` และ reporter ใน temp. ผลไม่ใช่ pilot หรือการตรวจมือถือจริง.

| คำสั่ง | ผลจริง |
|---|---|
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `133 files already formatted` |
| `uv run mypy src` | `Success: no issues found in 62 source files` |
| `uv run pytest -p no:cacheprovider` | `473 passed, 144 warnings in 221.06s (0:03:41)` |
| `corepack.cmd pnpm lint` ใน `web/` | exit 0 |
| `corepack.cmd pnpm typecheck` ใน `web/` | exit 0 |
| `corepack.cmd pnpm test` ใน `web/` | `Test Files 5 passed (5); Tests 56 passed (56)` |
| `corepack.cmd pnpm build --outDir <system-temp>/paiya-a9b-web-dist` ใน `web/` | `31 modules transformed`; `built in 950ms` |
| `corepack.cmd pnpm exec playwright test --output <system-temp> --reporter=line` ใน `web/` | `11 passed (7.9s)` บนฐานสร้างใหม่ `paiya_e2e_a9`, migration และ demo seed; พอร์ต 8137, server หยุดและ drop ฐานแล้ว |
| `uv run python scripts/check_docs.py` | `Docs OK: 25 indexed documents, 44 requirement and test IDs.` |
| `uv run python scripts/check_contracts.py` | `Contracts OK: 15 fixtures and examples checked against observation.v1.schema.json.` |
| `uv run python scripts/check_scenarios.py` | `Scenarios OK: 5 A1 scenarios, 4 feed scenarios, and 0 revisit scripts; all fixture data matches its schema.` |

ก่อนเริ่ม C: เหลือ 113,762,648,064 bytes; วัดก่อน build C: 113,731,448,832 bytes. ใช้ write bound และ floor ตาม handoff; ไม่ติดตั้งแพ็กเกจ. Vitest/Vite สร้าง `web/node_modules/.vite` (มี `vitest/`) และ `.vite-temp` ระหว่างรัน แม้คำสั่งกำหนด output หลักไป temp; ทั้งคู่ถูก Git ignore และเก็บไว้เพราะอยู่ใน dependencies ที่ lead จัดเตรียม ยังไม่ได้ยืนยันสิทธิ์ลบ cache. ผลนี้ไม่ตรงข้อกำหนดไม่สร้างโฟลเดอร์ใน worktree.

pytest รอบแรกได้ `473 passed, 144 warnings in 226.24s (0:03:46)` แต่ mutation ของ MCP เกิดระหว่างที่ suite ยังทำงาน หลัง module นั้นถูกทดสอบแล้ว จึงรัน suite ซ้ำหลังคืน source ทั้งหมด; ใช้ผลรอบซ้ำในตาราง.

Mutation ใช้ Python ใน system temp คืนไฟล์เป็น byte เดิมใน finally แล้วตรวจ SHA-256 ก่อนและหลัง; web mutants แก้ HTML build ใน temp ซึ่งเป็น artifact ที่ server ส่งให้ browser แทนการแก้ source และ build ซ้ำ:

| Clause | Mutation | ผล | SHA-256 ก่อนและหลัง |
|---|---|---|---|
| AT-01.5 | อ่าน env `SYNTHETIC_SESSION_TOKEN` ใน demo seed | `1 failed in 2.83s`: env spy หยุดการอ่าน | `0178f66277311b7fc479a2567aeb3513f6fad0f39794602acb615993f0973d28` |
| AT-05.5 | ลดความกว้าง card เหลือ 10 px ใน HTML build ชั่วคราว | เทสต์ใหม่ fail: 10 < 44; ทั้ง run `4 failed, 7 passed` | `506f631ecf409a45e4f59fed93cc5610764989b06ca5e445725405b3c887b137` |
| AT-06.4 | ลบ fence รอบข้อมูลใน MCP result | `1 failed, 1 warning in 3.50s`: ไม่พบ data fence | `e03ccdff1435ca212ed64dead5906d5f2d657759a431750de42ea3364dc9ff99` |
| AT-08.7 | แทรก fetch override ใน HTML build ชั่วคราวให้ filtered posts ว่าง | เทสต์ใหม่ fail: 0 แทน 1; ทั้ง run `2 failed, 9 passed` | `506f631ecf409a45e4f59fed93cc5610764989b06ca5e445725405b3c887b137` |
| AT-10.2 | ไม่ schedule discovery หากไม่มี watch | `1 failed in 2.73s`: ไม่มี discover claim | `4380e3c2936327f1cec671291d268f1754a03f793949a515380b9261ffbdb2a9` |
| AT-08.6 | ข้าม `ingest_fixture` ใน discovery | `1 failed in 3.13s`: ค้นโพสต์ไม่พบ | `44acf2a3e83484c99e2ca4a6594850b4a0c11cf275f2416e9ceac87cd804504b` |
| AT-10.8 | ให้ sold ยังคงเข้าเกณฑ์ open สำหรับ scheduler | `1 failed in 3.27s`: มี revisit ที่ไม่ควรมี | `4380e3c2936327f1cec671291d268f1754a03f793949a515380b9261ffbdb2a9` |

AT-08.6 ใช้ HTTP 500 จำลองผ่าน MockTransport ไม่ใช่การหยุด Ollama จริง. AT-10.8 ใช้ [สัญญาสถานะ ข้อ 6](../../../../architecture/contracts/item-status-and-watches.md) ซึ่งหยุดรอบช้าเมื่อทุกชิ้นปิด; เทสต์นี้เลือกกรณีทุกชิ้นเป็น sold ตามข้อความ AT โดยตรง.

## ผลรันระหว่างดำเนินการ (A fixture)

ฐานก่อนแก้ `98fb528509d899025d0a7a343f904f9594cb8089`; ผลต่อไปนี้เป็น targeted tests ไม่ใช่ผล suite หรือผล pilot:

| คำสั่ง (`UV_OFFLINE=1`, `UV_NO_SYNC=1`) | ผลจริง |
|---|---|
| `uv run pytest -p no:cacheprovider tests/test_a3_queue.py::test_five_grant_backed_groups -q` | `1 passed in 11.74s` |
| `uv run pytest -p no:cacheprovider tests/test_a3_queue.py::test_interval_and_pause_survive_scheduler_restart -q` | `1 passed in 2.66s` |
| `uv run pytest -p no:cacheprovider tests/test_a4c_seller.py::test_unknown_identity_stays_unlinked_across_groups -q` | `1 passed in 2.97s` |
| `uv run pytest -p no:cacheprovider tests/test_a4a_status.py -k S09 -q` | `1 passed, 17 deselected in 3.20s` |
| `uv run pytest -p no:cacheprovider tests/test_a3_discovery.py::test_fixture_discovery_never_opens_network_or_login -q` | `1 passed in 2.93s`; ตรวจ Python socket และไม่มีเมธอด login บน adapter แต่ยังไม่ปิด AT-01.5 เพราะยังไม่ได้พิสูจน์ทุก authentication entry point |
| `uv run pytest -p no:cacheprovider tests/test_a4a_status.py::test_sold_evidence_with_missing_source_link_is_rejected -q` | `2 passed in 2.97s` |

Mutation แต่ละรายการรัน targeted test แล้วคืน byte เดิมด้วย Python ใน system temp และตรวจ SHA-256 ก่อน/หลัง:

| Clause | Mutation | ผล | SHA-256 ก่อนและหลังตรงกัน |
|---|---|---|---|
| AT-01.1 | ตัด read จาก fixture grant | `1 failed in 2.86s` | `556abb1a0c29aeb8a185d4136fcd3b81b77fe0ed6506b298b8a9292248dedc93` |
| AT-01.3 | บังคับ slot 30 นาทีแทน interval ที่ตั้ง | `1 failed in 2.90s` | `4380e3c2936327f1cec671291d268f1754a03f793949a515380b9261ffbdb2a9` |
| AT-06.2 | ใช้ display name แทน actor key ที่ขาด | `1 failed in 3.04s` | `eb9356bcbd4a2d9d8c8650cb95a78ad52cf165085e27a972e8d806d4f24c4486` |
| AT-03.6 | รวม status facts ทั้งโพสต์แทนกรอง item ID | `1 failed, 17 deselected in 2.85s` (constraint ปฏิเสธ fact ข้าม item) | `888dd882cc9878c485bd57778fa100c2ed9ca9f71f6440ac991593dfd90c8107` |
| AT-03.10 | ไม่ปฏิเสธ evidence ที่อ้าง comment ซึ่งไม่มี | `1 failed, 1 passed in 3.08s` | `624a6ab08c3058276e73f79f5ee01ef9cc427eb7abbfd23fee5da20b7da2b2d5` |
| AT-01.5 (บางส่วน) | สั่ง Python socket connect ใน fixture adapter | `1 failed in 2.70s` โดย guard ดักก่อนเชื่อมต่อ | `92e12e58ec6cfa11fe77f35ac3aab9622e3dea54b9678ecd5cc0a0c31ca34b4a` |

### ผล suite และสถานะ handoff

ผล A (fixture) ต่อไปนี้รันบนฐาน `98fb528509d899025d0a7a343f904f9594cb8089` พร้อมการเปลี่ยนแปลงรอบนี้; Python suite รันก่อนจัดรูปแบบ whitespace หนึ่งบรรทัดในชื่อฟังก์ชันเทสต์ ส่วน Ruff และตัวตรวจเอกสารรันหลังจัดรูปแบบแล้ว ตั้ง `UV_OFFLINE=1`, `UV_NO_SYNC=1`, ไม่ติดตั้งแพ็กเกจ และส่ง cache/temp ไป system temp.

| คำสั่ง | ผลจริง |
|---|---|
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `133 files already formatted` |
| `uv run mypy src` | `Success: no issues found in 62 source files` |
| `uv run pytest -p no:cacheprovider` | `468 passed, 138 warnings in 215.43s (0:03:35)` |
| `uv run python scripts/check_docs.py` | `Docs OK: 25 indexed documents, 44 requirement and test IDs.` |
| `uv run python scripts/check_contracts.py` | `Contracts OK: 15 fixtures and examples checked against observation.v1.schema.json.` |
| `uv run python scripts/check_scenarios.py` | `Scenarios OK: 5 A1 scenarios, 4 feed scenarios, and 0 revisit scripts; all fixture data matches its schema.` |
| `corepack.cmd pnpm lint` ใน `web/` | exit 1: `'eslint' is not recognized`; ไม่มี `node_modules` |
| `corepack.cmd pnpm typecheck` ใน `web/` | exit 1: `'tsc' is not recognized`; ไม่มี `node_modules` |
| `corepack.cmd pnpm test` ใน `web/` | exit 1: `'vitest' is not recognized`; ไม่มี `node_modules` |
| `corepack.cmd pnpm build` และ Playwright | ไม่ได้รัน: ไม่มี dependencies และยังไม่มีหลักฐาน storage preflight สำหรับ build ตาม policy J; ห้ามติดตั้งแพ็กเกจในงานนี้ |

**สถานะเมื่อจบรอบก่อน f09cc73 (ข้อมูลย้อนหลัง):** Corepack ตั้ง `COREPACK_ENABLE_NETWORK=0`. ไม่ได้สร้าง `paiya_e2e_a9` หรือเริ่ม server; ตรวจหลัง Python suite ว่า non-system schemas ของ `paiya_test` มีเฉพาะ `public`. รอบนี้ยังไม่เสร็จ: AT-01.5, AT-05.5, AT-06.4, AT-08.6, AT-08.7, AT-10.2 และ AT-10.8 ยังเป็น F gap; ทำ tests และ mutation ของแต่ละข้อก่อนปิดงาน. งาน web ต้องมี dependencies ที่มีเจ้าของและใช้ซ้ำได้โดยไม่ติดตั้ง พร้อม preflight ที่ครบก่อน build/e2e; จากนั้นใช้ port 8137 และฐานชั่วคราวตามคำสั่งงาน. ไม่มีการรับมอบ pilot จากผลนี้

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

แผนที่รอบแรกผูกกับ commit ข้างต้น; รอบ 2b เพิ่มเทสต์และหลักฐานตามผลรันข้างต้น โดยไม่ได้แก้ fixtures เดิม, source, AT criteria, goldens หรือ SRS. Disposition: รอบ 2b พร้อมให้ reviewer ตรวจ fixture evidence; ยังไม่ใช่ merge หรือ pilot acceptance. A9b ยังรอคำตอบ Q1 ก่อนสร้างเฉลยสำหรับ corpus และเปอร์เซ็นต์ของ AT-02/03/07; A9c ยังต้องทำ pilot บนเครื่องจริง.
