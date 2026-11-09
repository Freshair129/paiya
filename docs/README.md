# เอกสารและสถาปัตยกรรมของป้ายยา

เมื่อรับงาน ให้เลือกไฟล์หลักตามสิ่งที่จะเปลี่ยน ตารางนี้เป็นทั้งสารบัญและทางเข้าของคนพัฒนากับ Agent ตัวอย่างเช่น หากเปลี่ยนชื่อฟิลด์ JSON ที่แอปและ MCP ใช้ร่วมกัน ให้เริ่มจากสัญญาข้อมูล หากเปลี่ยนว่าใครมีสิทธิ์เห็นข้อมูลนั้น ให้เริ่มจากข้อกำหนดใน SRS

| ที่อยู่ | เนื้อหาที่ไฟล์นี้เป็นเจ้าของ | ผู้ดูแลตามบทบาท |
|---|---|---|
| [product/PRD.md](product/PRD.md) | ผลที่ผู้ซื้อควรได้รับ ขอบเขต MVP และรหัส PR | Product owner |
| [requirements/SRS.md](requirements/SRS.md) | พฤติกรรมที่ระบบต้องทำ ขอบเขตสิทธิ์ และเกณฑ์ FR / AT | Engineering + QA |
| [architecture/SPEC.md](architecture/SPEC.md) | ภาพรวมระบบ ขอบเขตความรับผิดชอบ เทคโนโลยีและตำแหน่งที่รัน | Tech lead |
| [architecture/views/observation-flow.md](architecture/views/observation-flow.md) | แผนภาพเส้นทางของโพสต์หนึ่งรายการ ตั้งแต่รอบตรวจถึงการเปิดหลักฐาน | เจ้าของ Collector, API และ Worker |
| [architecture/contracts/collector-and-query.md](architecture/contracts/collector-and-query.md) | ความหมายของฟิลด์ กฎ null และเงื่อนไขข้ามฟิลด์ HTTP endpoints และคำสั่ง MCP โดยมี [observation.v1.schema.json](architecture/contracts/observation.v1.schema.json) เป็นโครงสร้างที่ใช้ตรวจจริง | เจ้าของระบบที่ส่งและรับข้อมูล |
| [architecture/contracts/discovery-queue.md](architecture/contracts/discovery-queue.md) | กติกาวางรอบ จองงาน ความครอบคลุมของรอบ ช่วงที่ขาด feed script สังเคราะห์ และการอ่านผลรอบ | เจ้าของ Worker และฐานข้อมูล |
| [architecture/contracts/item-status-and-watches.md](architecture/contracts/item-status-and-watches.md) | ตารางป้ายสถานะ การยืนยันหลักฐาน การสร้าง sale_events/current_items, timeline, หน้าผู้ขาย และ Watch/revisit ระยะ A | เจ้าของ Worker, API และฐานข้อมูล |
| [architecture/contracts/mobile-read-path.md](architecture/contracts/mobile-read-path.md) | read context เมื่อรัน server ระยะ A, endpoint ของหน้าจอมือถือ ข้อความไทย การนับแตะ AT-05 และ fixture สาธิต | เจ้าของ PWA + API |
| [architecture/contracts/owner-session.md](architecture/contracts/owner-session.md) | **ร่าง / เสนอเพื่ออนุมัติ** การปลดล็อกเจ้าของเครื่อง, cookie, CSRF, read grants และโหมด fixture สำหรับ HTTP API | Product owner + Backend + QA |
| [architecture/contracts/price-trends.md](architecture/contracts/price-trends.md) | ask ที่ยืนยันได้ ชุดตัวอย่างที่เทียบกันได้ ช่วงเวลา ค่ากลาง เกณฑ์ข้อมูลไม่พอ และ `GET /v1/prices/trends` | เจ้าของ API และฐานข้อมูล |
| [architecture/contracts/extraction-and-tags.md](architecture/contracts/extraction-and-tags.md) | กฎสกัดรุ่น หน่วยความจำ สภาพ และประกัน `extract-v1.0` ตาราง tags ค่าใน listing_items และตัวกรองรุ่นของ `GET /v1/posts` | เจ้าของ Worker, API และฐานข้อมูล |
| [architecture/contracts/local-model-enrichment.md](architecture/contracts/local-model-enrichment.md) | คิว enrich การเรียก Ollama ผ่าน loopback การตรวจข้อเสนอของโมเดล และผลต่อ listing_items | เจ้าของ Worker และฐานข้อมูล |
| [architecture/contracts/mcp-read.md](architecture/contracts/mcp-read.md) | MCP stdio แบบอ่านอย่างเดียว เครื่องมือห้ารายการ การบังคับ agent_export ภาพหลักฐาน และข้อผิดพลาด | เจ้าของ API และ MCP |
| [architecture/contracts/revocation-and-purge.md](architecture/contracts/revocation-and-purge.md) | คำสั่งเพิกถอน grant งานลบตามขอบเขต การสร้างข้อมูลอนุพันธ์ใหม่ และ replay คำสั่งลบหลังกู้คืน | เจ้าของ Worker และฐานข้อมูล |
| [architecture/contracts/backup-and-restore.md](architecture/contracts/backup-and-restore.md) | ชุดสำรอง คำสั่ง create/verify/restore/status ตาราง `backup_runs` และขอบเขต O1a กับ O1b | เจ้าของ Worker และฐานข้อมูล |
| [architecture/contracts/system-health.md](architecture/contracts/system-health.md) | structured log, เกณฑ์ดิสก์ใกล้เต็ม, `GET /v1/admin/health`, หน้าจัดการ และเกณฑ์ตรวจ O2 | เจ้าของ Worker, API และ PWA |
| [architecture/contracts/continuity-drill.md](architecture/contracts/continuity-drill.md) | worker สำหรับ fixture จุดจำลองการหยุด และสคริปต์ซ้อมความต่อเนื่อง O4 | เจ้าของ Worker และ QA |
| [operations/windows-install.md](operations/windows-install.md) | ติดตั้ง เริ่มระบบ สำรอง ตรวจสุขภาพ อัปเกรด กู้คืน และซ้อมความต่อเนื่องบน Windows สำหรับ O3 | เจ้าของระบบบนเครื่อง Windows |
| [architecture/contracts/collector-inbox.md](architecture/contracts/collector-inbox.md) | **ร่าง** รูปแบบ inbox ที่ Collector ภายนอกใช้ส่งผล การตรวจ `paiya inbox validate` การรับเข้าฐานที่รออนุมัติ และ prompt สำหรับ Collector | เจ้าของ Worker และฐานข้อมูล |
| [architecture/views/erd.md](architecture/views/erd.md) | ERD ของต้นทาง การสังเกต หลักฐาน ราคา และการติดตาม อ่านคู่กับข้อบังคับของแต่ละความสัมพันธ์ | Tech lead + เจ้าของฐานข้อมูล |
| [architecture/data-model.md](architecture/data-model.md) | พจนานุกรมข้อมูล PostgreSQL ระดับตรรกะ หน่วยข้อมูล คีย์ ข้อบังคับ ดัชนี และวงจรลบ/สร้างใหม่ | เจ้าของฐานข้อมูล + API |
| [architecture/contracts/buyer-workflows.md](architecture/contracts/buyer-workflows.md) | แบบสัญญาใหม่สำหรับนำเข้าด้วยมือ ประเมินกฎ แจ้งเตือน ติดตามรายชิ้น และเทียบราคาพร้อมหลักฐาน | เจ้าของ PWA + API + Worker |
| [quality/buyer-acceptance.md](quality/buyer-acceptance.md) | ชุดสถานการณ์สังเคราะห์ร่วม การทดสอบขอบเขตสิทธิ์ และการวัดการใช้งานกับผู้ซื้อ | QA + Product owner |
| [quality/evidence/AT-05/2026-11-07/README.md](quality/evidence/AT-05/2026-11-07/README.md) | ภาพและผลตรวจเส้นทางมือถือ AT-05 ด้วยข้อมูลสังเคราะห์ | QA + เจ้าของ PWA |
| [quality/evidence/A9/2026-11-08/README.md](quality/evidence/A9/2026-11-08/README.md) | แผนที่ AT-01–AT-10 กับเทสต์อัตโนมัติ ช่องว่าง และรายการวัดใน pilot สำหรับรับมอบ A9 | QA + lead |
| [architecture/decisions/0001-observation-store.md](architecture/decisions/0001-observation-store.md) | บันทึกการเลือก PostgreSQL และคลังภาพในเครื่อง รวมถึงเงื่อนไขที่ควรทบทวน | Tech lead |
| [architecture/decisions/0002-source-strategy.md](architecture/decisions/0002-source-strategy.md) | **รับรองแล้ว** ขอบเขตนำเข้าเองสำหรับทดลอง M และสิทธิ์แยกสำหรับ Collector ระยะ B | Product owner + Tech lead |
| [architecture/decisions/0003-dev-tooling.md](architecture/decisions/0003-dev-tooling.md) | **เสนอเพื่อรับรอง** เครื่องมือพัฒนา โครงสร้างรีโป และวิธีรันเทสต์บน Linux กับ Windows | Tech lead |
| [architecture/decisions/0004-local-access-and-mcp.md](architecture/decisions/0004-local-access-and-mcp.md) | **รับรองแล้ว** API เฉพาะในเครื่องกับ session เจ้าของเครื่อง, MCP เรียก service ใน process และจุดบังคับ `agent_export` | Product owner + Tech lead |
| [architecture/decisions/0005-owner-directed-collection.md](architecture/decisions/0005-owner-directed-collection.md) | **รับรองแล้ว** Collector อัตโนมัติตามคำตัดสินของเจ้าของ แทนเงื่อนไขสิทธิ์จากแพลตฟอร์มใน ADR 0002 กติกาที่คงไว้ | Product owner + Tech lead |
| [quality/TRACEABILITY.md](quality/TRACEABILITY.md) | ตารางโยง PR → FR → AT และลิงก์ผลตรวจของแต่ละข้อ | QA |
| [delivery/ROADMAP.md](delivery/ROADMAP.md) | ลำดับงาน สิ่งที่ต้องมีก่อน และหลักฐานจบแต่ละระยะ | Delivery lead |
| [delivery/m1-approval-package.md](delivery/m1-approval-package.md) | ข้อเสนอขอคำตัดสินและอนุมัติขอบเขต M1, schema v2 และการแปลงจาก v1 | Product owner + Tech lead |

## เลือกโฟลเดอร์เมื่อเพิ่มเอกสาร

- ใน `architecture/views/` ให้ใช้หนึ่งไฟล์ต่อคำถาม เช่น เมื่อบันทึกโพสต์ได้แต่เขียนภาพล้มเหลว ผู้ซื้อจะเห็นอะไร เก็บ Mermaid พร้อมคำอธิบายไว้ในหน้าเดียวกัน แล้วให้ SPEC ลิงก์มาที่หน้านั้น
- ใน `architecture/contracts/` ให้ใช้หนึ่งไฟล์ต่อขอบเขตการส่งข้อมูลที่ต้องเปลี่ยนพร้อมกัน ผู้สร้างข้อมูลและผู้ใช้ข้อมูลร่วมกันตรวจชื่อฟิลด์ ความหมาย ค่า null และข้อผิดพลาด หากภายหลังสร้าง OpenAPI จากโค้ดได้ ให้ระบุแหล่งที่สร้างและวิธีตรวจ diff ในหน้า contract
- ใน `architecture/decisions/` ให้ใช้ชื่อ `NNNN-topic.md` เมื่อมีการเลือกแนวทางที่เปลี่ยนย้อนกลับยาก บันทึกทางเลือก เหตุผลและผลกระทบ หากเปลี่ยนคำตัดสินให้ออก ADR ใหม่ เชื่อมทั้งสองฉบับ และระบุว่าฉบับใดแทนฉบับเดิม
- ใน `quality/` ให้ตารางตรวจย้อนกลับชี้ไปที่ผลทดสอบของ commit ที่ตรวจจริง เมื่อมีหลักฐานสังเคราะห์ที่ต้องเปิดเทียบข้ามรุ่น จึงเพิ่ม `quality/evidence/<AT-ID>/` พร้อมวันที่ ชุดข้อมูลและ commit ส่วนหลักฐานกลุ่มจริงให้อยู่ในที่ที่บังคับสิทธิ์ได้

## เมื่อโค้ดเปลี่ยน เอกสารเปลี่ยนอย่างไร

1. ผู้ทำงานระบุไฟล์ที่เป็นเจ้าของเรื่องและรหัสข้อกำหนดใน Issue หากกระทบทั้งสิ่งที่ผู้ใช้เห็นและข้อมูลข้ามส่วน ให้แก้ทั้ง SRS และ contract ใน PR เดียวกับโค้ด
2. หน้าที่เป็นข้อเสนอระบุว่า **ร่าง** พร้อมคำถามที่รอคำตอบ เมื่อทีมเลือกเป็นแนวทางพัฒนา ให้ระบุว่า **แบบที่ใช้อ้างอิง** ส่วนการระบุว่า **ตรวจยืนยันแล้ว** ต้องเจาะจงข้อที่ผ่าน แนบ commit และผลทดสอบในตารางตรวจย้อนกลับ รวมถึงแยกผลจาก fixture กับผลจากแหล่งจริง
3. Reviewer ตรวจ diff และหลักฐานใน PR เดียวกัน ตัวอย่างเช่น เมื่อเปลี่ยนให้หน้าประกาศแสดงจำนวนบัญชีผู้คอมเมนต์ที่ยืนยันได้ ให้ตรวจ FR-06 และ AT-04 ใน SRS, กติกา `distinct_known_commenters_seen` ใน contract และผล fixture ที่คนเดียวเขียนหลายคอมเมนต์ ก่อนบันทึกว่าข้อนี้ผ่าน
4. เมื่อเพิ่ม ย้าย หรือเปลี่ยนชื่อไฟล์ Markdown ใต้ `docs/` ให้อัปเดตลิงก์ในสารบัญนี้ รัน `python scripts/check_docs.py` จากรากรีโป เมื่อแก้ schema หรือ fixture ใน `fixtures/contract/` ให้รัน `python scripts/check_contracts.py` ด้วย CI ตรวจความครบของสารบัญ ลิงก์ภายใน และรหัสอ้างอิง ส่วนความตรงกันระหว่างคำอธิบายกับพฤติกรรมแอปให้ reviewer ตรวจจากผลทดสอบ

ดูขั้นตอนเปิด Issue, branch และ PR ที่ [คู่มือผู้ร่วมพัฒนา](../CONTRIBUTING.md)
