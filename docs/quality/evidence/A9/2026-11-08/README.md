# A9 · แผนที่เกณฑ์รับมอบและรายงานผลระยะ A

วันที่ตรวจ: 2026-11-08  
ฐานแผนที่ A9a: `46408054f3b8f407096582365762c3a1614fad39`<br>
สาขาที่สร้างแผนที่ A9a: `test/a9a-acceptance-map`<br>
ชุดตรวจ: source tests ทั้งหมดใต้ `tests/`, Vitest ใต้ `web/src/*.test.tsx` และ Playwright E2E ใต้ `web/e2e/*.spec.ts`

เอกสารต้นทาง: [SRS](../../../../requirements/SRS.md), [TRACEABILITY](../../../TRACEABILITY.md), [buyer acceptance](../../../buyer-acceptance.md), [ROADMAP](../../../../delivery/ROADMAP.md) และรูปแบบรายงานเดิม [AT-05 evidence](../../AT-05/2026-11-07/README.md)

## ขอบเขตและวิธีจัดประเภท

ส่วนแผนที่ A9a ด้านล่างผูกกับ commit ที่ระบุข้างต้น ส่วนรายงานรับมอบ A9c อ้างผล fixture ที่รันใหม่บน commit `191c51ac41271c2d3d615ce8da9e13e0785d06c1` ผลจาก automated tests ทั้งหมดเป็น fixture สังเคราะห์ แยกจากผล pilot

- **F covered** — มี automated test ใช้ fixture สังเคราะห์และ assert พฤติกรรมที่ตรงกับข้อนั้น โดยผลรอบนี้ผ่าน ณ commit ที่รายงาน
- **F gap** — ตรวจด้วย fixture ได้ แต่ยังไม่มี assertion ตรงข้อ หรือมีเพียงบางส่วน
- **B1a** — ย้ายการวัดไปงาน B1a ตาม ROADMAP แถว A7b/B1a และคำตัดสิน G5 ใน Issue #38; ระยะ A ยังไม่มี parser ราคาใน adapter
- **P pilot** — ต้องวัดกับเครื่องหรือการทำงานจริงตาม A9c; ไม่ใช่เกณฑ์ fixture
- **N/A in A** — ความสามารถยังไม่มีในระยะ A; ต้องตรวจอีกครั้งเมื่อมีความสามารถนั้น

ผล A9b ใช้ labels SHA-256 `48d9659d41437c58a29fa283ce01ec14e66a6e4ec841eba86cc505ccbdf2a551` และ `label_status = agent-reviewed-owner-delegated`. ตาม Issue #72 ผู้ร่างคือ gpt-6-luna; reviewer เป็น agent อิสระ (Claude subagent) ที่แยกจากผู้ร่างและ lead อ่าน fixture กับ SRS/contract แบบ read-only และเห็นตรงกัน 109 จาก 112 แถว; lead บันทึกคำตัดสินและแก้แถวที่เหลือใน Issue #72. การทบทวนนี้เป็นการทบทวนโดย agent ที่เจ้าของมอบหมาย ไม่ใช่การทบทวนโดยมนุษย์หรือการยืนยันโดยเจ้าของเอง.

การตรวจ `tests/test_scenarios.py` พบว่าเป็น mutation test ของ validator สำหรับ schema และเฉลยใน fixture; ไม่นับเป็น assertion ของ runtime สถานะ ส่วนการตรวจรับที่อ้างด้านล่างอ่าน assertion ใน test body โดยตรง

<a id="at-01"></a>
## AT-01 · จัดการกลุ่ม

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-01.1 | เปิด 5 กลุ่มที่แต่ละกลุ่มมี grant | **F covered** — `tests/test_a3_queue.py::test_five_grant_backed_groups` ตรวจ 5 grant ที่ยังใช้ได้และมี read/capture พร้อมปฏิเสธกลุ่มที่ 6 |
| AT-01.2 | รองรับรอบ 30/60 นาที | **F covered** — `tests/test_a3_queue.py::test_global_lease_excludes_other_group_and_owner_can_complete` สร้างกลุ่มรอบ 30 และ 60 นาที แล้ว assert เวลางาน due ของทั้งสองกลุ่ม |
| AT-01.3 | เปลี่ยนรอบและ pause มีผลหลัง restart | **F covered** — `tests/test_a3_queue.py::test_interval_and_pause_survive_scheduler_restart` dispose Engine สร้าง Engine ใหม่และเรียก scheduler entry ใหม่ใน process เดิม ตรวจ pause, resume และ slot 60 นาที; ไม่ใช่การ restart process |
| AT-01.4 | งานค้างตรวจ grant ซ้ำก่อน claim | **F covered** — `tests/test_a3_queue.py::test_revoked_grant_cancels_claim` assert ว่า claim ไม่คืนงาน, job ถูก cancel และ run ระบุ `grant_unavailable` เมื่อ revoke grant ก่อน claim |
| AT-01.5 | เริ่มทดสอบจาก fixture โดยไม่ login จริง | **F covered** — `tests/test_a3_discovery.py::test_fixture_start_path_avoids_external_authentication` ตรวจ demo seed, serve entry ผ่าน ASGI และ discovery ภายใต้ Python socket และ environment/file spies ตามขอบเขตที่บันทึกด้านล่าง |

<a id="at-02"></a>
## AT-02 · ค้นพบ

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-02.1 | พบ stable posts ที่ติดป้ายว่าโหลดได้ไม่น้อยกว่า 95% | **F covered** — `tests/test_a9b_corpus.py::test_a9b_thresholds` วัด visible recall ได้ **37/37 (100%)** จาก 112 label rows (เกณฑ์อย่างน้อย 100); ทุก run ที่มีตัวหารผ่าน ≥95%. [ผล A9b](../../../../../fixtures/acceptance/A9b/RESULTS.md), labels SHA-256 `48d9659d41437c58a29fa283ce01ec14e66a6e4ec841eba86cc505ccbdf2a551` |
| AT-02.2 | รายงานตัวเศษและตัวหาร | **F covered** — `tests/test_a9b_corpus.py::test_a9b_thresholds` และ [ผล A9b](../../../../../fixtures/acceptance/A9b/RESULTS.md) รายงาน 37/37 รวมและตัวเศษ/ตัวหารแยก run; labels SHA-256 `48d9659d41437c58a29fa283ce01ec14e66a6e4ec841eba86cc505ccbdf2a551` |
| AT-02.3 | จัดการ pinned ให้ coverage/overlap ตรง fixture | **F covered** — `tests/test_a3_discovery.py::test_coverage_cases` และ `test_excluded_cards_do_not_break_overlap_run` assert overlap กับ coverage เมื่อมี pinned card |
| AT-02.4 | การเรียงใหม่ยังพบ overlap ได้ | **F covered** — `tests/test_a3_discovery.py::test_coverage_cases` ใช้ `reordered_pinned` แล้ว assert `overlap_reached` และ coverage เป็น complete |
| AT-02.5 | sort ที่ยืนยันไม่ได้รายงาน partial และไม่เลื่อน checkpoint | **F covered** — `tests/test_a3_discovery.py::test_coverage_cases` assert `sort_unverified`, ไม่ advance checkpoint และ checkpoint คงเดิม |
| AT-02.6 | feed หยุดก่อน overlap รายงาน partial ตาม fixture | **F covered** — `tests/test_a3_discovery.py::test_feed_end_before_overlap_preserves_checkpoint` และ `test_coverage_cases` assert `feed_stopped_before_overlap` และรักษา checkpoint |

<a id="at-03"></a>
## AT-03 · สถานะ

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-03.1 | อัตราจับคู่ seller/post/item ผิดเป็นศูนย์ในชุดติดป้าย | **F covered** — `tests/test_a9b_corpus.py::test_a9b_thresholds` วัด wrong binding **0/57**; labels SHA-256 `48d9659d41437c58a29fa283ce01ec14e66a6e4ec841eba86cc505ccbdf2a551` |
| AT-03.2 | อัตรายืนยัน sold ผิดเป็นศูนย์ในชุดติดป้าย | **F covered** — `tests/test_a9b_corpus.py::test_a9b_thresholds` วัด wrong sold **0/60**; labels SHA-256 `48d9659d41437c58a29fa283ce01ec14e66a6e4ec841eba86cc505ccbdf2a551` |
| AT-03.3 | พบ sold ถูกต้อง ≥95% ในชุดย่อยที่มีหลักฐานปิดขายชัดเจนอย่างน้อย 20 กรณี | **F covered** — `tests/test_a9b_corpus.py::test_a9b_thresholds` วัด clear-sold recall **23/23 (100%)** จาก 23 กรณี (เกณฑ์อย่างน้อย 20); labels SHA-256 `48d9659d41437c58a29fa283ce01ec14e66a6e4ec841eba86cc505ccbdf2a551` |
| AT-03.4 | รายงาน coverage แยกสำหรับผู้ขายที่มี stable ID | **F covered** — `tests/test_a9b_corpus.py::test_a9b_thresholds` รายงาน stable-ID seller coverage **57/57**; labels SHA-256 `48d9659d41437c58a29fa283ce01ec14e66a6e4ec841eba86cc505ccbdf2a551` |
| AT-03.5 | reserved → open ต้องมีหลักฐานของแต่ละเหตุการณ์ | **F covered** — `tests/test_a4a_status.py::test_golden_replay` ใช้ `S01-reserve-reopen` แล้ว assert สถานะและประวัติพร้อม evidence ID ที่ตรงกับแต่ละรอบ |
| AT-03.6 | ขายหนึ่งชิ้นในโพสต์หลายชิ้นไม่ปิดอีกชิ้น | **F covered** — `tests/test_a4a_status.py::test_golden_replay[S09-sold-one-item-seller-sells-one-other-stays-open]` ตรวจ seller confirmation ว่า 3060 sold และ 3070 open พร้อม history และ evidence |
| AT-03.7 | คอมเมนต์ผู้ซื้อ “จองครับ” ที่ติดบทบาท seller ไม่สร้าง reserved/sold | **F covered** — `tests/test_a4a_status.py::test_golden_replay` ของ S03 buyer-only assert ว่าสถานะยัง open และ history ไม่มี event ใหม่; `test_rejected_comment_evidence` assert ว่า evidence ที่ role ไม่ตรงไม่สร้าง sale event |
| AT-03.8 | actor_key ที่ไม่ตรง seller ไม่สร้างสถานะยืนยัน | **F covered** — `tests/test_a4a_status.py::test_rejected_comment_evidence` กรณี actor assert ผล rejected, `actor_mismatch` และจำนวน sale events เป็นศูนย์ |
| AT-03.9 | ระบุสาเหตุของหลักฐานที่ถูกปฏิเสธ | **F covered** — `tests/test_a4a_status.py::test_rejected_comment_evidence` assert ค่า `reason` ที่เก็บไว้ เช่น `role_mismatch` และ `actor_mismatch`; ยังไม่ระบุใน AT ว่าต้องแสดง reason ที่ API หรือ UI |
| AT-03.10 | ลิงก์ขาดต้องไม่ทำให้ปิดขาย | **F covered** — `tests/test_a4a_status.py::test_sold_evidence_with_missing_source_link_is_rejected` ตรวจ permalink ที่หายและ evidence ที่อ้าง comment ซึ่งไม่มี; ทั้งสองถูกปฏิเสธพร้อมเหตุผลก่อนสร้าง observation หรือ sale event |

<a id="at-04"></a>
## AT-04 · คอมเมนต์และ gap

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-04.1 | 5 ความเห็นจากผู้เขียนที่ยืนยันได้คนเดียวแสดง 5/1 | **F covered** — `tests/test_a4b_timeline.py::test_five_comments_one_known_author` assert activity เป็น 5 ความเห็น, 1 ผู้เขียนที่รู้จัก และ timeline มี comment_added 5 รายการ |
| AT-04.2 | โหลดได้ 2 จาก 5 ต้องระบุ partial และแยกยอดที่ต้นทางรายงาน | **F covered** — `tests/test_a4b_timeline.py::test_partial_comments_and_changes` และ `test_golden_replay` ของ S04 assert reported count 5, เห็นจริง 2, known commenters 1 และ coverage partial |
| AT-04.3 | สถานะที่หายระหว่างรอบไม่สร้าง event สมมติ | **F covered** — `tests/test_a4a_status.py::test_golden_replay` ของ S04 assert current status unknown และ status history ว่าง |

<a id="at-05"></a>
## AT-05 · หลักฐานและ UI

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-05.1 | จาก feed ไปถึง crop และ permalink ไม่เกิน 3 แตะ | **F covered** — `web/e2e/mobile.spec.ts::AT-05 captured image within three taps` นับแตะจาก feed, assert จำนวนไม่เกิน 3 และยืนยันว่ารูปกับลิงก์ต้นทางอยู่ใน viewport |
| AT-05.2 | ภาพ MCP ผูกกับ post/observation เดียวกับ read path ของ UI | **F covered** — `tests/test_a6c_mcp.py::test_media_integrity_and_scope` assert bytes ของ MCP image เท่ากับ API media และ evidence เชื่อมกับ post/observation เดียวกัน; `web/e2e/mobile.spec.ts::AT-05 captured image within three taps` เดินตาม evidence link ที่ UI แสดง |
| AT-05.3 | ภาพหายต้องมีเหตุผล | **F covered** — `tests/test_a6c_mcp.py::test_missing_image_reasons` assert reason ของ MCP metadata สำหรับ missing/capture_failed/not_requested; `web/e2e/mobile.spec.ts::capture failed preserves reason, quote and permalink` assert เหตุผลบนหน้า UI |
| AT-05.4 | ที่ 360 CSS px อ่านราคา/สถานะได้โดยไม่เลื่อนแนวนอน | **F covered** — `web/e2e/mobile.spec.ts::mobile pages have no horizontal overflow and accessible touch targets` assert ราคาและสถานะที่การ์ด พร้อม scrollWidth ไม่เกิน 360 บนหน้าที่ทดสอบ |
| AT-05.5 | ปุ่มหลักอ่านและกดได้ที่ 360 CSS px | **F covered** — `web/e2e/mobile.spec.ts::AT-05.5 primary mobile actions are visible and usable` ตรวจ focus, ขนาดแตะ, overflow และเปิด filter, post, evidence และ watch |

<a id="at-06"></a>
## AT-06 · ตัวตนและ tags

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-06.1 | ชื่อแสดงเหมือนกันแต่ stable keys ต่างกันยังเป็นคนละ seller | **F covered** — `tests/test_a4c_seller.py::test_distinct_keys_and_scopes_never_merge_sellers` ใช้ display name เดียวกันกับ actor keys ต่างกัน และ assert seller IDs ทั้งสามไม่ซ้ำ |
| AT-06.2 | ข้ามกลุ่มเมื่อยืนยัน identity ไม่ได้ต้องแยก scope | **F covered** — `tests/test_a4c_seller.py::test_unknown_identity_stays_unlinked_across_groups` ตรวจโพสต์สองกลุ่มชื่อผู้ขายซ้ำแต่ไม่มี stable key คง `seller_id = NULL` และแยก group ID ตามสัญญา Collector หัวข้อข้อมูลจาก Collector และการบันทึกและอ่านในระยะ A กับ data model ข้อ 3 |
| AT-06.3 | tags บอกที่มาของค่าได้ | **F covered** — `tests/test_a6a_extraction.py::test_first_matching_eligible_evidence_and_replay` assert tag ผูกกับ evidence key ที่ตรง quote; `test_contract_examples_through_ingest` assert ผล tags และ reason จาก fixture |
| AT-06.4 | ข้อความแทรกคำสั่งในโพสต์สั่งเรียก tool ไม่ได้ | **F covered** — `tests/test_a6c_mcp.py::test_injected_tool_call_is_data_at_every_mcp_boundary` ส่งข้อมูลจำลองผ่าน read services ของ MCP ทั้งห้า ตรวจ fence, read-only tool list, จำนวน calls และ SQL writes |

<a id="at-07"></a>
## AT-07 · ข้อมูลราคา

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-07.1 | amount ตรง ≥95% ใน subset ที่ติดป้ายว่าระบุชัด | **B1a** — ROADMAP แถว A7b/B1a และคำตัดสิน G5 ใน Issue #38 ย้ายการวัด parser ราคาไป B1a; ระยะ A ไม่มี adapter price parser ให้ประเมิน |
| AT-07.2 | currency ตรง ≥95% ใน subset เดียวกัน | **B1a** — วัด amount/currency บน parser ของ adapter หลัง B1 ตาม ROADMAP แถว B1a; ระยะ A ไม่มี adapter price parser |
| AT-07.3 | จับคู่ราคากับ item ถูก ≥95% | **B1a** — วัดความแม่นยำการผูก item หลังมี adapter parser ตาม ROADMAP แถว A7b/B1a และคำตัดสิน G5 ใน Issue #38 |
| AT-07.4 | รายงานจำนวน subset ที่นำมาคิด | **B1a** — รายงานตัวหารพร้อมการวัด amount/currency/item ใน B1a ตาม ROADMAP; ไม่มี parser ที่จะสร้างตัวหารในระยะ A |
| AT-07.5 | shipping, deposit, offer และ bundle ไม่เข้า comparables ของการ์ดแยกชิ้น | **F covered** — `tests/test_a7a_trends.py::test_non_ask_price_is_excluded` assert deposit/shipping/offer ไม่เพิ่ม n; `test_window_edges_and_excluded_units_ranges` assert bundle และ range ถูกตัดออก |

<a id="at-08"></a>
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

<a id="at-09"></a>
## AT-09 · สิทธิ์และการลบ

| Clause | สาระย่อ | ประเภทและ assertion ที่มี |
|---|---|---|
| AT-09.1 | revoke ระหว่าง capture แล้ว commit ไม่ผ่าน | **F covered** — `tests/test_a8a_revocation.py::test_revoke_between_ingest_checks_rolls_back_commit` assert error เป็น `grant_unavailable` และจำนวน observation/evidence/facts/jobs คงเดิม |
| AT-09.2 | revoke แล้วงานคิวที่ยัง active หยุด | **F covered** — `tests/test_a8a_revocation.py::test_cli_revoke_cancels_only_active_jobs_and_is_idempotent` assert queued/leased jobs ถูก cancel; `test_revoked_read_endpoints_and_all_mcp_tools` ครอบคลุม read/MCP |
| AT-09.3 | API/MCP และ media ที่เคยเปิดอ่านซ้ำไม่ได้ | **F covered** — `tests/test_a8a_revocation.py::test_revoked_read_endpoints_and_all_mcp_tools` เปิด media ก่อน revoke แล้ว assert API ทุก endpoint และ MCP tools ปฏิเสธหลัง revoke |
| AT-09.4 | purge แล้วสถิติไม่รวมข้อมูลที่ลบ | **F covered** — `tests/test_a8b_purge.py::test_reads_after_purge_match_b_only_baseline` assert models, price trend และ seller read หลัง purge เท่ากับ baseline ของ grant B |
| AT-09.5 | restore ไม่ทำให้ข้อมูลที่ลบกลับมาค้นได้ | **F covered** — `tests/test_a8c_replay.py::test_restore_drill_replays_original_command` ยืนยัน startup ถูกกั้นจน replay purge log แล้ว assert post/evidence/media ที่ลบไม่พบผ่าน API/MCP และไม่โผล่ใน search/stats |

<a id="at-10"></a>
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

| AT | F covered | F gap | L corpus | B1a | P pilot | N/A in A | รวม |
|---|---:|---:|---:|---:|---:|---:|---:|
| AT-01 | 5 | 0 | 0 | 0 | 0 | 0 | 5 |
| AT-02 | 6 | 0 | 0 | 0 | 0 | 0 | 6 |
| AT-03 | 10 | 0 | 0 | 0 | 0 | 0 | 10 |
| AT-04 | 3 | 0 | 0 | 0 | 0 | 0 | 3 |
| AT-05 | 5 | 0 | 0 | 0 | 0 | 0 | 5 |
| AT-06 | 4 | 0 | 0 | 0 | 0 | 0 | 4 |
| AT-07 | 1 | 0 | 0 | 4 | 0 | 0 | 5 |
| AT-08 | 6 | 0 | 0 | 0 | 2 | 1 | 9 |
| AT-09 | 5 | 0 | 0 | 0 | 0 | 0 | 5 |
| AT-10 | 8 | 0 | 0 | 0 | 0 | 0 | 8 |
| **รวม** | **53** | **0** | **0** | **4** | **2** | **1** | **60** |

ยอดนี้เป็นจำนวน clause ในแผนที่ ไม่ใช่จำนวนเทสต์หรือผลผ่าน

<a id="a9-report"></a>
## รายงานรับมอบระยะ A

คำว่า “ผ่าน” หมายถึงทุก clause ที่ทดสอบด้วย fixture ในระยะ A มี assertion ครอบคลุมและผ่านบน commit ที่รายงาน ส่วน pilot, B1a และ N/A แสดงแยกในคอลัมน์สุดท้าย

| AT | ผล fixture | clauses และหลักฐาน | ค้างสำหรับ pilot, B1a หรือระยะถัดไป |
|---|---|---|---|
| AT-01 | ผ่านบางส่วน | 01.1–01.5: `tests/test_a3_queue.py`, `tests/test_a3_discovery.py`; restart test สร้าง Engine ใหม่ใน process เดิม | ผ่านส่วน fixture; รอ pilot ระยะ C: restart ทั้ง process สำหรับ 01.3 และ native I/O สำหรับ 01.5 |
| AT-02 | ผ่าน | 02.1–02.2: `tests/test_a9b_corpus.py::test_a9b_thresholds`, visible recall 37/37; 02.3–02.6: `tests/test_a3_discovery.py`. Corpus 112 label rows และทุก run ที่มีตัวหารผ่าน ≥95% | ไม่มี clause ระยะ A ค้าง; labels ใช้สถานะ `agent-reviewed-owner-delegated` ตาม Issue #72 (ดูการตีความด้านล่าง) |
| AT-03 | ผ่าน | 03.1–03.4: `tests/test_a9b_corpus.py::test_a9b_thresholds` วัด wrong binding 0/57, wrong sold 0/60, clear-sold recall 23/23 และ stable-ID coverage 57/57; 03.5–03.10: `tests/test_a4a_status.py` | ไม่มี clause ระยะ A ค้าง; labels ใช้สถานะ `agent-reviewed-owner-delegated`; T17–T19 ใช้คำตัดสินลิงก์ขาดใน Issue #72 |
| AT-04 | ผ่าน | 04.1–04.3: `tests/test_a4b_timeline.py` และ `tests/test_a4a_status.py` | ไม่มี pilot/B1a/N/A ที่ระบุในแผนที่ |
| AT-05 | ผ่านบางส่วน | 05.1, 05.4–05.5: Playwright `web/e2e/mobile.spec.ts` 11 passed ที่ A9a commit `c9023ca9ea82dc5431f355338de281ba63c09c56`; 05.2–05.3: `tests/test_a6c_mcp.py`; E2E ไม่รันซ้ำรอบนี้ | ผ่านส่วน fixture; รอ pilot ระยะ C/O5 สำหรับโทรศัพท์จริงผ่าน HTTPS |
| AT-06 | ผ่าน | 06.1–06.2: `tests/test_a4c_seller.py`; 06.3: `tests/test_a6a_extraction.py`; 06.4: `tests/test_a6c_mcp.py` ภายใน MCP server boundary | ไม่มี pilot/B1a/N/A ที่ระบุในแผนที่; ขอบเขต agent runtime ไม่อยู่ในระยะ A |
| AT-07 | ผ่านบางส่วน | 07.5: `tests/test_a7a_trends.py::test_non_ask_price_is_excluded` และ `test_window_edges_and_excluded_units_ranges` | ผ่านส่วน fixture; 07.1–07.4 ย้ายไป B1a ตาม A7b/B1a และ G5 ใน Issue #38 |
| AT-08 | ผ่านบางส่วน | 08.1–08.3: `tests/test_a4a_status.py`; 08.5–08.6: `tests/test_a6b_enrichment.py`; 08.7: Playwright `web/e2e/mobile.spec.ts` ใน A9a commit `c9023ca9ea82dc5431f355338de281ba63c09c56` | ผ่านส่วน fixture; รอ pilot ระยะ B: 08.8 Ollama จริง และ 08.9 CPU/RAM/VRAM, backlog, poll lateness; 08.4 เป็น N/A in A จนมี alerts ใน M2 |
| AT-09 | ผ่าน | 09.1–09.3: `tests/test_a8a_revocation.py`; 09.4: `tests/test_a8b_purge.py`; 09.5: `tests/test_a8c_replay.py` | ไม่มี pilot/B1a/N/A ที่ระบุในแผนที่ |
| AT-10 | ผ่าน | 10.1, 10.3: `tests/test_a7a_trends.py`; 10.2: `tests/test_a3_discovery.py`; 10.4–10.8: `tests/test_a4d_watches.py` และ `web/src/cards.test.tsx` | ไม่มี pilot/B1a/N/A ที่ระบุในแผนที่ |

ผล fixture ใหม่ทั้งหมดด้านล่างอ้าง HEAD ก่อน commit A9c: `191c51ac41271c2d3d615ce8da9e13e0785d06c1`. ต้นไม้ commit นี้รวมเนื้อหา A9b `28dd4285e93bea8b9cf9806604fcc09def4815c5` และ A9a `1bb2c840819a4361147eded7fc118e79887cf6cb`; local `origin/main` ยังชี้ที่ A9a เพราะรอบนี้ห้ามใช้เครือข่าย.

### ผลตรวจใหม่บน HEAD ข้างต้น

ใช้ `UV_OFFLINE=1`, `UV_NO_SYNC=1`, `UV_NO_CACHE=1`, `PYTHONDONTWRITEBYTECODE=1`; pytest เพิ่ม `PYTEST_ADDOPTS=-p no:cacheprovider`. ไม่ติดตั้งแพ็กเกจ. ใน `web/` ใช้ `COREPACK_ENABLE_NETWORK=0` และ `npm_config_offline=true`.

| คำสั่ง | ผลจริง |
|---|---|
| `uv run python scripts/check_docs.py` | `Docs OK: 25 indexed documents, 44 requirement and test IDs.` |
| `uv run python scripts/check_contracts.py` | `Contracts OK: 15 fixtures and examples checked against observation.v1.schema.json.` |
| `uv run python scripts/check_scenarios.py` | `Scenarios OK: 5 A1 scenarios, 4 feed scenarios, and 0 revisit scripts; all fixture data matches its schema.` |
| `uv run python scripts/check_corpus.py` | `A9b corpus OK; label_status=agent-reviewed-owner-delegated.` 112 label rows; ambiguous rows 0. |
| `uv run pytest -q` | `475 passed, 144 warnings in 379.50s (0:06:19)`; warnings are SQLAlchemy `ARRAY.Comparator.any()` deprecations from `src/paiya/read/context.py:40`. |
| `corepack.cmd pnpm test` in `web/` | `Test Files 5 passed (5); Tests 56 passed (56)`; duration 24.82s. |

**E2E ที่ไม่ได้รันซ้ำตามขอบเขตงานนี้:** A9a รายงาน Playwright `11 passed (7.9s)` ที่ commit `c9023ca9ea82dc5431f355338de281ba63c09c56`; เป็นผล fixture เก่า ไม่ใช่ pilot.

## รายการวัดใน pilot

ข้อมูลนี้แยกจาก fixture acceptance; ค่า mock ใช้แทนผลจริงไม่ได้ เป้าหมายที่ SRS ไม่ได้กำหนดตัวเลขให้ระบุว่าเป็นการเก็บ/รายงานผล ไม่สร้างเกณฑ์ใหม่.

| รายการ | วัดอะไร | เป้าหมายจาก SRS | ระยะ | เจ้าของบทบาท |
|---|---|---|---|---|
| AT-08 ทรัพยากรเครื่อง | baseline, concurrency, CPU, RAM/working set peak และ VRAM ตลอด run บน i7-8700K / RAM 32 GB / RTX 3060 12 GB | บันทึกค่าจริงบนเครื่องนี้ก่อนเพิ่ม concurrency; SRS ไม่กำหนดเพดานตัวเลข | B3 | QA + ผู้ปฏิบัติการ Collector |
| Poll lateness | เวลาที่ poll เสร็จเทียบเวลาที่ตั้ง, queue depth/age, missed slots, machine-sleep gaps, retry และ error แยกกลุ่ม | AT-08 ให้บันทึก queue backlog และ poll lateness; SRS ไม่มีตัวเลข lateness ผ่าน/ไม่ผ่าน | B3 | QA + ผู้ดูแลคิว Collector |
| Ollama จริง | ปิด service จริงระหว่างงาน; ตรวจ discovery, การอ่านข้อมูลเดิมใน UI/API, error/backlog และการฟื้นหลังเปิด service | AT-08: discovery และการอ่านข้อมูลเดิมยังทำงานเมื่อ Ollama ใช้ไม่ได้; เก็บ error/backlog จริง | B2 (หนึ่งกลุ่ม), ยืนยันซ้ำ B3 | QA + ผู้ปฏิบัติการ Collector |
| Read path | ใช้ข้อมูลสังเคราะห์ 10,000 โพสต์และฐานอุ่น; วัด p95 ของ feed query 50 รายการ, ไม่นับการส่งรูป; รายงานขนาดรูปและ time-to-first-image แยก | p95 ≤1 วินาที; SRS §7 | C/O4 | QA + Tech lead |
| โทรศัพท์จริง | รุ่นเครื่อง, OS/browser, viewport, อ่านราคา/สถานะ/ปุ่ม, feed ถึง crop/permalink, จำนวนแตะและ horizontal overflow ผ่าน HTTPS | AT-05: 360 CSS px อ่านราคา/สถานะ/ปุ่มได้โดยไม่เลื่อนแนวนอน และเปิด crop/permalink ไม่เกิน 3 แตะ; O5 ใช้เครื่องจริงผ่าน HTTPS | C/O5 | QA + เจ้าของ PWA |
| Restart ทั้ง process (AT-01.3) | ปรับ interval/pause, ปิดและเปิด process ใหม่, ตรวจค่าที่คงอยู่ งานที่ resume และ revalidation grant ก่อน claim | AT-01: เปลี่ยนรอบและ pause มีผลหลัง restart; งานค้างตรวจ grant ใหม่ก่อน claim | C/O4 | QA + ผู้ดูแล runtime |
| Native I/O (AT-01.5) | รัน fixture path ใน process ที่ติดตั้งจริง; บันทึก OS-level file/env/socket calls และยืนยันไม่มี login/credential access หรือ source request | AT-01: เริ่มจาก fixture ได้โดยไม่ login จริง; SRS ไม่กำหนดวิธีสังเกต native I/O | C/O3–O4 | QA + Tech lead |
| AT-08.4 หลังมี alerts | ป้อน O1/O3/O2 ตามลำดับเวลาที่กำหนด แล้วตรวจ notification sink/event และจำนวน alerts จาก O2 | AT-08: late O2 ต้องไม่สร้าง alert; ไม่มี sink ใน A, จึงทดสอบหลัง M2/AT-12 มี alerts | M2 (แล้วทวนใน M5) | QA + เจ้าของฟีเจอร์ notification |
| D05: post เดิมซ้ำใน run เดียวแต่ content เปลี่ยน | ปัจจุบัน CaptureConflict หยุด run; ทำซ้ำ post/capture identity ด้วย payload ที่เปลี่ยน แล้ววัดการหยุด/ต่อ run, cards ที่นับได้ และตัวหารที่รายงาน | AT-02: ≥95% stable posts ที่โหลดให้เห็น พร้อมตัวเศษ/ตัวหารต่อ run; SRS ไม่กำหนดผลเมื่อ CaptureConflict หยุด run จึงต้องตัดสินพฤติกรรมก่อนเริ่ม B2 | B2 (ต้องมี decision ก่อน) | Product owner + Tech lead ตัดสิน; QA วัดผล |

## จุดที่คำตัดสินขึ้นกับการตีความ

| AT/กรณี | ถ้อยคำที่ใช้ | ผลต่อคำตัดสิน |
|---|---|---|
| AT-01.1 | “grant ที่ใช้ได้และมี capture” (สัญญาคิวข้อ 2); เทสต์ตรวจ read เพิ่มสำหรับการค้นคืน fixture | ผล fixture ครอบคลุม grant ที่ active/read/capture; ไม่ได้ตั้งเกณฑ์ scope อื่นเพิ่ม |
| AT-01.3 | “ใช้การ dispose Engine และสร้าง Engine กับ scheduler entry ใหม่ใน process เดิมเป็นขอบเขตที่ตรวจ” | จึงรายงานผ่านบางส่วน; การ restart process จริงยังอยู่ใน C/O4 |
| AT-01.5 | “`serve.main` ใช้ ASGI TestClient แทน uvicorn listener” | fixture spy ไม่พิสูจน์ native I/O; จึงค้าง C/O3–O4 |
| AT-02/AT-03 labels | Issue #72: “labels are recorded as `agent-reviewed-owner-delegated`, not as human-reviewed”; reviewer แยกจากผู้ร่าง อ่าน fixture/SRS/contract แบบ read-only และเห็นตรงกัน 109/112 แถว | ตัวเลขใน corpus ผ่าน แต่รายงานสถานะ review ตามจริง; ไม่อ้างว่าเป็น human-reviewed หรือ owner-confirmed |
| AT-02 denominator | Issue #72: “AT-02 counts ‘per round and overall’”; D02-C10 “The contract does not drop loaded cards after overlap” | 37/37 เป็นยอดรวมตาม label; รายงาน per-run ใน RESULTS.md และนับการ์ดที่โหลดใน step เดียวกันตามคำตัดสินนี้ |
| AT-03.1–03.3, T17–T19 | Issue #72: “Broken link covers two cases: the closing evidence cannot be tied back to its source, or it cannot be tied to the item. Neither may produce `sold`.” | T17–T19 เป็น item link missing จึงคาด `open`; source link ที่หาย/อ้าง comment ที่ไม่มีถูกปฏิเสธโดย AT-03.10 |
| AT-03.9 | SRS บอก “ต้องบอกสาเหตุ” แต่ไม่ระบุว่าจะอยู่ใน DB, API หรือ UI | fixture พิสูจน์ reason ที่เก็บกับ status fact (`role_mismatch`, `actor_mismatch`); ยังไม่อ้างการแสดงผล UI/API เพิ่ม |
| AT-04.2 | SRS บอก “แยกยอดที่หน้าเว็บรายงาน” โดยไม่ระบุว่าหมายถึง source-reported total, จำนวนที่เห็นจริง หรือทั้งสอง | test ตีความเป็น reported count 5, visible 2, known commenters 1 และ coverage partial; verdict จำกัดอยู่ที่ assertion เหล่านี้ |
| AT-05.2 | SRS บอก “รูปที่ส่งผ่าน MCP ตรงกับ post/observation เดียวกับ UI” โดยไม่ระบุ bytes หรือความสัมพันธ์ | test เทียบ bytes กับ API media และยืนยัน post/observation/evidence relationship; ไม่อ้างเกณฑ์อื่นเกินนี้ |
| AT-05.5 | “primary actions” ใช้ filter, card, evidence, permalink และ navigation ติดตามตาม mobile contract | Playwright pass อ้างขอบเขต action เหล่านี้ที่ 360 × 800; E2E รอบนี้อ้างผล A9a ที่ commit แยกไว้ข้างต้น |
| AT-06.4 | “ขอบเขตคือ MCP server ไม่มี agent runtime” | verdict ผ่านเฉพาะ read-only MCP/tool boundary ใน A; ไม่ขยายไป agent runtime ภายนอก |
| AT-08.4 | “ไม่มี notification/alert sink” ใน A; alerts อยู่ใน AT-12/M2 | จัดเป็น N/A in A และรอ M2 ก่อนวัด ไม่ใช้ผล history/current แทนผล alert |
| AT-10.4 | SRS ระบุ “ภายใต้ cap”; ไม่ชี้ว่าเป็น cap 10 ของ hot watches หรือ budget 10 ของ scheduler | fixture ผ่านค่าที่ทดสอบทั้งสองส่วน; ไม่อ้างว่าได้ข้อสรุปเชิงความหมายเกินสอง assertion นั้น |
| AT-10.8 | SRS ระบุ “เมื่อชิ้นที่ติดตามทุกชิ้นขายแล้ว”; test เลือกกรณีทุกชิ้นมี seller evidence ว่า sold | verdict ครอบคลุม sold ตามถ้อยคำนี้; ไม่ใช้ withdrawn แทน sold |
| D05-C06 | collector contract: “payload_hash ต่างกันเป็น conflict”; discovery contract: “ส่ง envelope เข้า ingest ของ A2” | เอกสารไม่กำหนดผลของ run เมื่อ conflict เกิดระหว่างการ์ด; ไม่ตัดสินว่า fixture หรือระบบผิด และต้องได้คำตัดสินก่อน B2 |

## ประวัติแผนการปิดช่องว่าง A9a

ตารางนี้เป็นข้อเสนอ ณ A9a ก่อนผล A9b และไม่ใช่รายการ gap ปัจจุบัน; สถานะปัจจุบันอยู่ในแผนที่และรายงานด้านบน.

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

งานในแผน A9a นี้เสร็จใน PR #73. สถานะปัจจุบันดูตารางสรุปและรายงาน A9c ด้านบน: ไม่มี F gap; AT-07.1–07.4 อยู่ B1a; AT-08.4 เป็น N/A in A; AT-08.8–08.9 รอ pilot.

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

## ผลรันเพิ่มเติมรอบ 2b (A fixture, ประวัติ A9a)

ผลต่อไปนี้รันบนฐาน `f09cc73` พร้อม diff รอบ 2b และอยู่ใน A9a commit `c9023ca9ea82dc5431f355338de281ba63c09c56`; ไม่ใช่ผล pilot.

| การตรวจ | ผลจริง |
|---|---|
| `uv run pytest -p no:cacheprovider tests/test_a6b_enrichment.py::test_failed_enrichment_does_not_block_fixture_discovery tests/test_a4d_watches.py::test_slow_revisit_stops_when_both_items_are_seller_confirmed_sold -q` | `2 passed in 5.23s` |
| Playwright fresh `paiya_e2e_a9`, port 8137, `--reporter=line` | `11 passed (7.9s)`; server หยุดและฐานชั่วคราวถูก drop แล้ว |

### ชุดตรวจเต็มรอบ 2b (A fixture, ประวัติ A9a)

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

### ผล suite และสถานะ handoff (A9a, ประวัติ)

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

**สถานะ A9c:** ผล fixture ระยะ A ถูกบันทึกในรายงานด้านบนแล้ว. ยังไม่มีผล pilot รอบนี้; ดำเนินการวัดตามรายการ B/C/M ด้านบนเมื่อเงื่อนไขและการตัดสิน D05 ครบ. AT-07.1–07.4 รอ B1a.
