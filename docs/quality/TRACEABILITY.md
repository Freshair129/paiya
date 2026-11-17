# ป้ายยา (paiya) · ข้อกำหนดและการตรวจรับ

ใช้ตารางนี้หาว่าเมื่อเปลี่ยนพฤติกรรมที่ผู้ซื้อเห็น ต้องทวนข้อกำหนดระบบและชุดทดสอบใดบ้าง ขอบเขตผลิตภัณฑ์อยู่ใน [PRD](../product/PRD.md) รายละเอียดพฤติกรรม ตัวหาร และเกณฑ์ผ่านอยู่ใน [SRS](../requirements/SRS.md) ส่วนตำแหน่งที่ระบบรับผิดชอบอยู่ใน [สเปกสถาปัตยกรรม](../architecture/SPEC.md)

**สถานะ** ผล fixture ระยะ A สำหรับ PR-01–PR-09 บันทึกแล้วใน [รายงาน A9](evidence/A9/2026-11-08/README.md#a9-report); ผล pilot ยัง pending. PR-10–PR-13 อยู่ระยะ M และยังไม่มีผล A.

| ข้อผลิตภัณฑ์ | สิ่งที่ต้องพิสูจน์ | ข้อกำหนดระบบ | ชุดทดสอบรับมอบ | หลักฐาน |
|---|---|---|---|---|
| PR-01 | ตั้งรอบแต่ละกลุ่ม พักแล้วเริ่มต่อได้ | FR-01 | AT-01 | [A9 map §AT-01](evidence/A9/2026-11-08/README.md#at-01) · A (fixture) · `191c51ac41271c2d3d615ce8da9e13e0785d06c1` · ผ่านบางส่วน: AT-01 |
| PR-02 | ค้นโพสต์ที่โหลดเห็นจริง แสดงผลอ่านบางส่วนและช่วงที่ขาด | FR-02, FR-03, FR-05 | AT-02, AT-04, AT-08 | [A9 map §AT-02](evidence/A9/2026-11-08/README.md#at-02), [§AT-04](evidence/A9/2026-11-08/README.md#at-04), [§AT-08](evidence/A9/2026-11-08/README.md#at-08) · A (fixture) · `191c51ac41271c2d3d615ce8da9e13e0785d06c1` · A3 duplicate regression: [test_a3_duplicate_sightings.py](../../tests/test_a3_duplicate_sightings.py) · ผ่านบางส่วน: AT-08 |
| PR-03 | ติดตามสถานะรายชิ้น การกลับมาเปิดขาย และกันคำพูดผู้ซื้อที่ถูกติดบทบาทผู้ขายผิด | FR-04, FR-05, FR-06 | AT-03, AT-04, AT-08, AT-10 | [A9 map §AT-03](evidence/A9/2026-11-08/README.md#at-03), [§AT-04](evidence/A9/2026-11-08/README.md#at-04), [§AT-08](evidence/A9/2026-11-08/README.md#at-08), [§AT-10](evidence/A9/2026-11-08/README.md#at-10) · A (fixture) · `191c51ac41271c2d3d615ce8da9e13e0785d06c1` · ผ่านบางส่วน: AT-08 |
| PR-04 | เปิดข้อความ รูป และลิงก์ของโพสต์เดียวกับหลักฐานที่ใช้อ้าง | FR-07, FR-11 | AT-05, AT-09 | [A9 map §AT-05](evidence/A9/2026-11-08/README.md#at-05), [§AT-09](evidence/A9/2026-11-08/README.md#at-09) · A (fixture) · `191c51ac41271c2d3d615ce8da9e13e0785d06c1` · ผ่านบางส่วน: AT-05 (มือถือจริง/O5); AT-09 ผ่าน |
| PR-05 | ประวัติผู้ขายย้อนกลับไปดูประกาศได้และแยกคนชื่อซ้ำ | FR-08 | AT-03, AT-06 | [A9 map §AT-03](evidence/A9/2026-11-08/README.md#at-03), [§AT-06](evidence/A9/2026-11-08/README.md#at-06) · A (fixture) · `191c51ac41271c2d3d615ce8da9e13e0785d06c1` · ผ่าน: AT-03, AT-06 |
| PR-06 | ค้น กรอง ติดตาม และเปิดหลักฐานบนจอมือถือ | FR-04, FR-09 | AT-05, AT-10 | [A9 map §AT-05](evidence/A9/2026-11-08/README.md#at-05), [§AT-10](evidence/A9/2026-11-08/README.md#at-10) · A (fixture) · `191c51ac41271c2d3d615ce8da9e13e0785d06c1` · ผ่านบางส่วน: AT-05 (มือถือจริง/O5); AT-10 ผ่าน |
| PR-07 | แท็กมีแหล่งที่มา Agent ค้นคืนข้อมูลได้ตามสิทธิ์ และค้นต่อได้เมื่อโมเดลหยุด | FR-10, FR-11 | AT-06, AT-07, AT-08 | [A9 map §AT-06](evidence/A9/2026-11-08/README.md#at-06), [§AT-07](evidence/A9/2026-11-08/README.md#at-07), [§AT-08](evidence/A9/2026-11-08/README.md#at-08) · A (fixture) · `191c51ac41271c2d3d615ce8da9e13e0785d06c1` · ผ่านบางส่วน: AT-07, AT-08 |
| PR-08 | ราคาเสนอขายย้อนหลังมาจากข้อเสนอที่เปรียบเทียบกันได้ | FR-12 | AT-07, AT-10 | [A9 map §AT-07](evidence/A9/2026-11-08/README.md#at-07), [§AT-10](evidence/A9/2026-11-08/README.md#at-10) · A (fixture) · `191c51ac41271c2d3d615ce8da9e13e0785d06c1` · ผ่านบางส่วน: AT-07 |
| PR-09 | หยุดเก็บ ปิดการอ่านรวมถึงแจ้งเตือนและผลเทียบ และลบข้อมูลตามขอบเขตสิทธิ์ | FR-01, FR-08, FR-13 | AT-01, AT-09, AT-11, AT-14 | [A9 map §AT-01](evidence/A9/2026-11-08/README.md#at-01), [§AT-09](evidence/A9/2026-11-08/README.md#at-09) · A (fixture) · `191c51ac41271c2d3d615ce8da9e13e0785d06c1` · ผ่านบางส่วน: AT-01; AT-11/AT-14 ยังอยู่ระยะ M |
| PR-10 | บันทึกประกาศจากข้อความบนมือถือ ตรวจแหล่ง/สิทธิ์ก่อนยืนยัน retry ไม่ซ้ำ ส่งข้อมูลใหม่เข้าประวัติเดิมโดยไม่เปิดต้นทาง | FR-13, FR-14 | AT-11 | — |
| PR-11 | ตั้งรับก่อนเจอชิ้นสินค้า เห็นผลเดิมและได้เหตุผลเมื่อเข้าเกณฑ์ใหม่ ตัดรายการคลุมเครือและกันแจ้งซ้ำ | FR-15 | AT-12 | — |
| PR-12 | เลือกติดตามเฉพาะ 3060 ในโพสต์ 3060/3070 ได้ การเปลี่ยนของอีกชิ้นไม่กระทบรายการที่เลือก | FR-04, FR-06, FR-16 | AT-13 | — |
| PR-13 | เปิดเทียบ 2–3 ชิ้น เห็นราคาสุทธิเมื่อทราบค่าส่งและเปิดหลักฐานตรงชิ้นได้ ข้อมูลที่ถอนสิทธิ์ต้องอ่านไม่ได้ | FR-13, FR-17 | AT-14 | — |

[แบบจำลองข้อมูล](../architecture/data-model.md) และ [ERD](../architecture/views/erd.md) ใช้ตรวจคีย์และเส้นทางจากชิ้นสินค้าถึงหลักฐาน ส่วน [สัญญาสำหรับผู้ซื้อ](../architecture/contracts/buyer-workflows.md) ระบุเงื่อนไขรับคำขอและข้อผิดพลาดที่ผู้ทดสอบต้องครอบคลุม

## I2 Collector inbox

[Issue #91](https://github.com/Freshair129/paiya/issues/91) เชื่อม FR-02/03 กับการรับผลรอบและหลักฐานตาม AT-02 และ FR-07/13 กับการปฏิเสธสิทธิ์และการลบตาม AT-09 ชุด [test_i2_inbox_ingest.py](../../tests/test_i2_inbox_ingest.py) ใช้ข้อมูลจำลองบน PostgreSQL 17 ผลจากกลุ่มจริงต้องตรวจใน pilot แยกต่างหาก

## B1 และ B1a

- **B1** · FR-01/02/13 · AT-01/09 · [เทสต์สอง origin](../../collector/tests/b1_browser_origin_integration.py) และ [เทสต์ก่อนอ่าน DOM](../../collector/tests/test_browser_runtime.py) ใช้หน้าและ credential สังเคราะห์บน loopback
- **B1a** · FR-10/12 · AT-07/10 · เจ้าของยืนยันเฉลย [ชุดราคา 100 กรณี](../../collector/fixtures/b1a-price-cases.jsonl) ตาม [Roadmap](../delivery/ROADMAP.md) [การวัดซ้ำ](../../collector/scripts/score_b1a.py) ได้จำนวนเงิน สกุลเงิน และรายการตรงพร้อมกัน 64/64 กรณี และกันราคาที่เทียบไม่ได้ออก 36/36 กรณี ดูหลักฐานใน [PR #111](https://github.com/Freshair129/paiya/pull/111) ชุดผลคำนวณราคาและการนับซ้ำของ Paiya ตรวจผ่านชุด A7a ตามแถว PR-08

## บันทึกหลักฐานเมื่อส่งงาน

1. ผู้ทำงานใส่รหัส PR, FR และ AT ที่เกี่ยวข้องใน GitHub issue และ pull request เมื่อแก้ขอบเขต ให้อัปเดตแถวที่ได้รับผลพร้อมเอกสารต้นทางใน pull request เดียวกัน
2. ผู้ทดสอบแนบ commit SHA, รหัส AT, ชุดข้อมูลและรุ่น, ขั้นตอนทดสอบ, ผลจริงเทียบเกณฑ์ และลิงก์ไปยัง run หรือรายงานที่ย้อนตรวจได้ ระบุรายการที่ยังไม่ได้รันและกรณีที่ล้มเหลว
3. ผู้ตรวจเติมลิงก์หลักฐานในแถวที่เกี่ยวข้อง พร้อมระบุว่าเป็นผลจาก fixture (A), ผู้ซื้อส่งข้อมูลเอง (M) หรือ Collector ที่ได้รับสิทธิ์ (B) ให้รายงานจำนวนผู้ซื้อ ระยะสังเกตและเวลาตัดสินใจใน M ส่วน B ให้รายงานจำนวนกลุ่ม รอบตรวจและจำนวนโพสต์ที่โหลดให้เห็น หากหนึ่งข้อผ่านเพียงบางชุด ให้ระบุรหัสชุดที่ผ่านจริง
4. รีโปนี้เปิดสาธารณะ ใช้ข้อมูลสังเคราะห์สำหรับตัวอย่างและภาพในรีโป บันทึกผล pilot ที่เผยแพร่ได้เป็นตัวเลขรวม พร้อมรหัสอ้างอิงไปยังหลักฐานที่ควบคุมสิทธิ์

[แผนส่งมอบ](../delivery/ROADMAP.md) · [สารบัญเอกสาร](../README.md)

## Collector restricted MCP candidate

Issue #91 approval [6937280955](https://github.com/Freshair129/paiya/issues/91#issuecomment-6937280955) covers implementation and synthetic verification. [ADR 0007](../architecture/decisions/0007-restricted-collector-mcp.md) maps FR-01/02/03/07/13 to synthetic subsets of AT-01/02/04/09. [Policy tests](../../collector/tests/test_mcp_policy.py), [asset relay tests](../../collector/tests/test_mcp_assets.py) and the [native MCP/CLI harness](../../collector/scripts/test-mcp-browser.py) cover exact-origin redirect rejection, receipt-based 429, challenge-before-cards, profile ownership, evidence node identity, credential-free asset hops and READY publication. Run receipts belong to the immutable reviewed candidate/PR. No Facebook pilot or live-profile acceptance is claimed by this row.

MCP startup containment also maps FR-01/13 to AT-01/09 through the [gate test](../../collector/tests/test_mcp_gate.py) and native harness cases for pre-arm navigation, service EOF/forced kill, suspended-child assignment failure and profile reopening. These are Windows synthetic containment checks, not live pilot evidence.

The [compatibility harness](../../collector/scripts/test-mcp-compat.py) extends the same FR-01/02/03/07/13 and AT-01/02/04/09 subsets to existing service-worker profiles, active startup, persistent restart guards, loader/worker-produced 429, same-origin child challenge scanning, gzip/deflate and CORS/CORP delivery. Credential-dependent CDN refusal remains explicit; live profile/CDN/source behavior remains unqualified. Use the latest immutable candidate manifest and receipts rather than superseded r6 results.

AT-04/09 includes open/closed shadow-frame insertion and child navigation races with zero internal card reads and zero image captures, source-JavaScript suspension and rejection of confirmed pending child responses, and pause timeout, unexpected resume and transport-loss refusal. Post-read discard alone does not satisfy challenge-before-cards; debugger pause alone does not freeze parser transitions.
