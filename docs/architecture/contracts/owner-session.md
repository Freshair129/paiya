# สัญญา session เจ้าของเครื่องและ CSRF ของ API ในเครื่อง

**สถานะ** ร่าง / เสนอเพื่ออนุมัติ · ยังไม่อนุมัติให้เริ่ม implementation ที่จัดการ session · **ผู้ดูแล** Product owner, Backend และ QA

เอกสารนี้เสนอรายละเอียดที่ [ADR 0004](../decisions/0004-local-access-and-mcp.md) กำหนดให้ตัดสินก่อนทำ session จริง ใช้คู่กับ [นโยบายการเข้าถึง](../SPEC.md), [สัญญาผู้ซื้อ](buyer-workflows.md), [สัญญาเส้นทางอ่านบนมือถือ](mobile-read-path.md), [SRS ข้อ 6](../../requirements/SRS.md), [ROADMAP O5](../../delivery/ROADMAP.md) และ [สัญญาสุขภาพระบบ](system-health.md) งานเอกสารนี้เป็นข้อเสนอเท่านั้น; implementation เป็นงานความเสี่ยงสูงและต้องมีคำอนุมัติเป็นลายลักษณ์อักษรใน Issue ก่อนเริ่ม

## 1. ขอบเขตและ threat model

| สถานะ | ขอบเขต |
|---|---|
| ครอบคลุม | API ผูกเฉพาะ loopback บนเครื่องเดียว มีเจ้าของหนึ่งคนต่อเครื่อง ป้องกันเว็บอื่นที่พยายามส่งคำขอผ่าน browser และปฏิเสธ DNS rebinding ด้วยการตรวจ `Host` ทุกคำขอและ `Origin` ของคำขอเขียน |
| ไม่ครอบคลุม | การเปิด API ผ่าน LAN หรือโทรศัพท์ ซึ่งเป็น O5 และต้องมี HTTPS กับการตัดสินแยก; ผู้ใช้หลายคนต่อเครื่อง; การยึดเครื่องหรือ process โดย malware/ผู้ใช้ใน Windows account เดียวกัน |

การจำกัด `Origin` และ `Host` ช่วยป้องกัน browser จากเว็บอื่นเรียก API บน loopback และอ่านข้อมูลด้วย DNS rebinding แต่ไม่ใช่ขอบเขตป้องกันโค้ดอันตรายที่ทำงานด้วยสิทธิ์ของเจ้าของเครื่อง

## 2. การตั้งค่าและปลดล็อกครั้งแรก

**ข้อเสนอหลัก: ใช้ passphrase ของเจ้าของเครื่องและเก็บเฉพาะตัวตรวจสอบที่ได้จาก `hashlib.scrypt`.** ใช้ Python standard library (`getpass`, `secrets`, `hashlib`, `hmac`, `pathlib`) โดยไม่เพิ่ม dependency

- เมื่อเริ่ม `serve` ครั้งแรกและยังไม่มีข้อมูลยืนยันตัวตน ให้ตั้ง passphrase ผ่าน prompt แบบไม่แสดงข้อความบน terminal และถามซ้ำเพื่อยืนยัน หากไม่มี interactive terminal ให้หยุดแบบ fail closed พร้อมบอกวิธีตั้งค่าจาก terminal ห้ามสร้างค่าเริ่มต้นหรือปลดล็อกอัตโนมัติ
- สร้าง salt ด้วย `secrets.token_bytes(16)` และตัวตรวจสอบด้วย `hashlib.scrypt`; เก็บรุ่น KDF, พารามิเตอร์, salt และผลลัพธ์เท่านั้น ไม่เก็บ passphrase ตรง ๆ ตัวเลือกตั้งต้นสำหรับทดลองคือ `N=2**15`, `r=8`, `p=1`, `dklen=32` และต้องวัดเวลา/หน่วยความจำบนเครื่องขั้นต่ำที่รองรับก่อนอนุมัติ implementation
- เก็บไฟล์ เช่น `%LOCALAPPDATA%\Paiya\security\owner-auth.json` นอก repository โดยปิดการสืบทอด ACL และอนุญาตเฉพาะ SID ของเจ้าของเครื่องกับ `SYSTEM`; ห้ามให้ `Users` หรือ `Everyone` อ่านได้ เขียนไฟล์ด้วยวิธี atomic และตั้ง ACL ให้ถูกต้องตั้งแต่สร้างไฟล์ หากรองรับ POSIX ให้ใช้ directory `0700` และไฟล์ `0600`
- ห้ามใส่ passphrase ใน argument, environment variable, repository, backup ที่ไม่เข้ารหัส หรือ log การตรวจ passphrase ใช้ `hmac.compare_digest`; เมื่อเปลี่ยน passphrase ต้องเพิกถอนทุก session
- `/v1/auth/challenge` ออก pre-auth synchronizer nonce แบบสุ่ม ใช้ครั้งเดียวและหมดอายุใน 5 นาที เก็บเฉพาะ hash ในหน่วยความจำ คำขอ `POST /v1/auth/unlock` ต้องส่ง nonce ใน `X-CSRF-Token` พร้อม passphrase และผ่าน `Origin`/`Host` check ก่อนสร้าง session

| ทางเลือก | ผลและข้อแลกเปลี่ยน |
|---|---|
| One-time setup token ที่ `serve` แสดงใน console | ติดตั้งครั้งแรกง่ายและมี entropy สูง แต่ต้องกำหนดการเก็บ/กู้ credential หลังใช้ token และต้องรับประกันว่า stdout ไม่ถูกส่งเข้า log; อาจใช้เป็นตัวเปิดหน้าตั้ง passphrase ได้ |
| OS user check | ไม่ต้องมี passphrase แต่การตรวจ process account ไม่ยืนยัน browser และไม่แยกเว็บภายนอกจาก browser ของเจ้าของเครื่อง จึงไม่พอเป็นกลไกปลดล็อกนี้ |

## 3. Cookie และ server-side session

สร้าง session token ด้วย `secrets.token_urlsafe(32)` (สุ่ม 32 bytes) และเก็บเพียง SHA-256 digest ของ token ใน PostgreSQL ตรวจ digest ด้วยการเปรียบเทียบแบบ constant-time ตารางใหม่นี้เป็น DDL แบบเพิ่มตารางและต้องอนุมัติแยกก่อน implementation:

| ตาราง | คอลัมน์ที่เสนอ |
|---|---|
| `owner_sessions` | `id UUID PRIMARY KEY` (ID ภายในสำหรับอ้างอิง log), `token_hash BYTEA NOT NULL UNIQUE`, `csrf_token TEXT NOT NULL`, `created_at TIMESTAMPTZ NOT NULL`, `last_seen_at TIMESTAMPTZ NOT NULL`, `expires_at TIMESTAMPTZ NOT NULL`, `revoked_at TIMESTAMPTZ NULL` |

`csrf_token` เป็น synchronizer token สุ่มจาก `secrets.token_urlsafe(32)` ผูกกับ session; ส่งให้ client ในผล unlock และผ่าน `GET /v1/auth/csrf` ซึ่งต้องมี session และตั้ง `Cache-Control: no-store` เพื่อให้หน้าเว็บโหลดใหม่ได้ ห้าม log ค่านี้ ควรมีดัชนีตาม `expires_at` สำหรับค้น/เก็บกวาดแถวหมดอายุ และ constraint ให้ `expires_at > created_at`; ไม่เก็บ passphrase, cookie token แบบ plaintext, grant ID หรือเนื้อหาผู้ซื้อในตารางนี้

- อายุสูงสุดแบบตายตัวคือ 12 ชั่วโมงนับจาก unlock (`expires_at`); idle timeout คือ 30 นาทีจาก `last_seen_at` การอ่านหรือเขียนที่ยืนยัน session แล้วปรับ `last_seen_at` แต่ไม่เลื่อนอายุสูงสุด
- ตั้ง cookie `paiya_owner_session` เป็น host-only (ไม่มี `Domain`), `HttpOnly`, `SameSite=Strict`, `Path=/`, `Max-Age=43200`; ไม่ตั้ง `Secure` เพราะ listener ตาม ADR 0004 ใช้ plain loopback HTTP และ browser อาจไม่ส่ง Secure cookie บน HTTP ไม่ใช้ prefix `__Host-` ที่ต้องพึ่ง `Secure`
- อนุญาต origin ของ `127.0.0.1` และ `localhost` ตามข้อ 4 แต่ cookie ไม่มี `Domain`: browser จึงแยก cookie ตาม hostname ผู้ใช้ต้องใช้ hostname เดิมตลอด session
- เมื่อปลดล็อกสำเร็จ ให้สร้าง token และ CSRF token ใหม่ทุกครั้ง; หากคำขอมี session เดิม ให้เพิกถอน session เดิมก่อนออกค่าใหม่เพื่อกัน session fixation
- `POST /v1/auth/logout` ต้องผ่าน CSRF แล้วตั้ง `revoked_at` ให้ session ปัจจุบันและลบ cookie ด้วย `Max-Age=0`; คำขอถัดไปด้วย cookie เดิมต้องได้ 401
- `POST /v1/auth/revoke-all` ต้องผ่าน CSRF แล้วกำหนด `revoked_at` ให้ทุก session ที่ยังไม่เพิกถอน รวมถึง session ที่เรียก จากนั้นลบ cookie ของผู้เรียก คำขอที่ใช้ session เก่าทั้งหมดต้องได้ 401

ทางเลือกแทนตารางคือ in-memory store ซึ่งไม่มี DDL แต่ restart แล้ว session ทั้งหมดหายและไม่เหมาะกับหลาย worker; file store อยู่นอก repository ได้แต่ต้องเพิ่ม atomic update, locking, recovery และการคุม ACL ตาราง PostgreSQL จึงเป็นข้อเสนอหลักเพื่อให้ตรวจ/เพิกถอน session ได้จากทุก request และทุก process ของ API

## 4. CSRF และการตรวจ `Origin`/`Host`

- ตรวจ `Host` ในทุก HTTP request รวม read, static app และ auth challenge เพื่อกัน DNS rebinding ค่า authority ที่รับได้มีเพียง `127.0.0.1:<PAIYA_PORT>` หรือ `localhost:<PAIYA_PORT>` โดย port ต้องตรงกับ listener จริง; ไม่รับ host อื่น, user-info, suffix/trailing dot, หลายค่า หรือ `X-Forwarded-Host`/`Forwarded` เป็นตัวแทน
- สำหรับทุกคำขอเขียน `POST`, `PUT`, `PATCH`, `DELETE` ต้องมี `Origin` ที่ parse ได้ตรงกับ `http://127.0.0.1:<PAIYA_PORT>` หรือ `http://localhost:<PAIYA_PORT>` และ hostname/port ต้องตรงกับ `Host` ของคำขอ ขาด `Origin`, เป็น `null`, ใช้ scheme/port/host อื่น หรือ `Host` ไม่ตรง ให้ตอบ 403 โดยไม่ใช้ `Referer` แทน
- คำขอเขียนที่มี session ต้องส่ง synchronizer token ใน `X-CSRF-Token` และตรงกับ token ของ session นั้น การปลดล็อกเป็นข้อยกเว้นด้านสถานะ session แต่ยังเป็นคำขอเขียน: ต้องใช้ pre-auth nonce แบบใช้ครั้งเดียวจาก `/v1/auth/challenge` พร้อมตรวจ `Origin` และ `Host`
- `GET`, `HEAD` และ `OPTIONS` ไม่เปลี่ยน state; method อื่นที่ไม่รู้จักตอบ 405 ห้ามอนุมานว่า method ที่ไม่รู้จักเป็น read
- ไม่เปิด CORS ให้ origin อื่น และทุก endpoint ที่คืน session/challenge/CSRF ต้องตั้ง `Cache-Control: no-store`

## 5. Read context และการเปลี่ยนจาก fixture

ในโหมดใช้งานปกติ session ยืนยันว่าเป็นเจ้าของเครื่องเท่านั้น API ต้อง resolve รายการ `source_grants` ที่มี `read`, ยังไม่ถูกเพิกถอน และอยู่ในช่วง `valid_from`/`valid_until` ณ เวลาของ request แล้วส่ง `list[ReadableGrant]` ให้ read service ตาม ADR 0004 ทุก read รวม endpoint ภาพต้องตรวจ grant ซ้ำใน service layer ห้ามรับ group/grant จาก cookie, header, query หรือ body เพื่อเพิ่มสิทธิ์

`PAIYA_READ_CONTEXT_GROUP_KEY` ใน `mobile-read-path.md` เป็น fixture context ปัจจุบัน ไม่ใช่การยืนยันตัวตนสำหรับข้อมูลจริง เส้นทางเปลี่ยนผ่านเมื่อทำ implementation ต้องเป็นดังนี้:

1. โหมดปกติปิด fixture bypass และต้องมี owner session; ตั้ง `PAIYA_READ_CONTEXT_GROUP_KEY` อย่างเดียวต้องไม่ให้สิทธิ์อ่าน
2. เพิ่ม dev-only fixture mode ที่ปิดโดยปริยายและเปิดโดยการตั้งค่าอย่างชัดเจน เช่น `PAIYA_DEV_FIXTURE_MODE=1` พร้อม `PAIYA_READ_CONTEXT_GROUP_KEY=synthetic-...`; mode นี้เลือกได้เฉพาะ `groups.source = fixture` และ grant `read` ที่ยังใช้ได้
3. ปรับ e2e และ demo ให้เริ่มด้วย fixture mode นี้อย่างชัดเจน เพื่อให้ `paiya demo seed` และข้อมูลสาธิตทำงานต่อ; การทดสอบต้องยืนยันว่า default ปิดอยู่และการตั้ง group key อย่างเดียวไม่ bypass session
4. ก่อนตัด implementation ให้ปรับ dependency ปัจจุบันใน `src/paiya/api/` จาก fixture-only context เป็น session + `list[ReadableGrant]`; คงการตรวจขอบเขตไว้ที่ read service ไม่ใช่ FastAPI dependency เพียงชั้นเดียว

ข้อเสนอนี้ไม่เปลี่ยนสัญญา fixture ปัจจุบันก่อน implementation และไม่อนุญาตให้นำ dev fixture mode ไปใช้กับข้อมูลจริง

## 6. MCP และ CLI

MCP ไม่เปลี่ยน: ใช้ stdio, เรียก read service ใน process และไม่มี HTTP session หรือ cookie ตาม ADR 0004 และสัญญา MCP เดิม เส้นทาง MCP ยังคงตรวจ grant และ `agent_export` ตามสิทธิ์เดิม CLI ไม่ต้อง login และไม่ได้รับผลจาก session ของ HTTP API

## 7. HTTP errors

คงรูปแบบ JSON ที่มี `detail.code` คงที่ตาม [สัญญาผู้ซื้อ](buyer-workflows.md):

| Status | `code` ที่เสนอ | ความหมาย |
|---|---|---|
| 401 | `session_required` | ไม่มี session, token ไม่รู้จัก, ถูกเพิกถอน, หมดอายุสูงสุด หรือ idle เกินกำหนด; ใช้ผลเดียวกันโดยไม่เปิดเผยว่า token ใดมีอยู่ |
| 403 | `origin_not_allowed` | `Origin` หรือ `Host` ไม่ผ่าน allowlist |
| 403 | `csrf_invalid` | ไม่มี/ผิด CSRF token หรือ pre-auth nonce |
| 403 | `write_forbidden` | session ใช้คำสั่งเขียนนี้ไม่ได้ ตามความหมาย 403 ในสัญญาผู้ซื้อ |
| 403 | `grant_unavailable` | ไม่มี readable grant ที่ยังใช้ได้ตามขอบเขตข้อมูลที่ขอ |

คำขอ read ใต้ `/v1` ที่ไม่มี session ตอบ 401; หน้า unlock และ `GET /v1/auth/challenge` เป็นข้อยกเว้นที่ไม่คืนข้อมูลผู้ซื้อ `healthz` ตอบเฉพาะสถานะ process และไม่แทนการตรวจ session ส่วน CLI ใช้ error model เดิมของ CLI

## 8. Logging

ตาม [สัญญาสุขภาพระบบ](system-health.md) ให้ log เป็น JSON โดยคง `ts`, `level`, `event` และเพิ่มเฉพาะ ID ภายใน, status กับ reason code เช่น `request_id`, `session_id`, `reason`; ฟิลด์อื่นให้เป็น enum หรือจำนวนที่ไม่เปิดเผยข้อมูล ห้าม log passphrase, ผล KDF, setup/pre-auth nonce, CSRF token, cookie หรือ header ที่มี token ห้ามใส่ `Cookie`, `Set-Cookie`, `Authorization` หรือ request body ใน log

## 9. Tests ที่ implementation ต้องมี

- Cross-origin `POST` ที่ Origin เป็นเว็บอื่นต้องได้ 403 `origin_not_allowed`
- คำขอเขียนที่ขาดหรือส่ง CSRF token ผิด รวมทั้ง unlock ที่ใช้ pre-auth nonce ผิด/หมดอายุ ต้องได้ 403 `csrf_invalid`
- คำขอที่ `Host` เป็นโดเมน attacker แม้ต่อเข้าถึง loopback ได้ ต้องถูกปฏิเสธ เพื่อยืนยันการกัน DNS rebinding
- ทดสอบแยกทั้ง absolute-expired และ idle-expired session ให้ได้ 401 `session_required` โดยใช้ clock ที่ควบคุมได้
- Logout แล้วเรียก endpoint ที่ต้องใช้ session ด้วย cookie เดิมต้องได้ 401
- ยืนยัน cookie flags: ชื่อ `paiya_owner_session`, `HttpOnly`, `SameSite=Strict`, `Path=/`, `Max-Age=43200`, ไม่มี `Domain` และไม่มี `Secure` บน HTTP loopback
- ใส่ canary ใน cookie, CSRF และ passphrase แล้วตรวจ log ทุกเหตุการณ์ว่าไม่มีค่า canary/token
- เรียก read endpoint โดยไม่มี session ต้องได้ 401 `session_required`
- ตรวจว่า default ปิด dev fixture mode; ตั้ง `PAIYA_READ_CONTEXT_GROUP_KEY` อย่างเดียวต้องยังได้ 401 และ demo/e2e ต้องเปิด fixture mode อย่างชัดเจน
- ตรวจว่า unlock ออก session ID และ CSRF token ใหม่, logout ทำให้ session ปัจจุบันใช้ต่อไม่ได้ และ `revoke-all` ทำให้ session ที่ยัง active ทุกตัวใช้ต่อไม่ได้

การตรวจข้างต้นเป็นเกณฑ์สำหรับ implementation ในอนาคต ไม่ใช่ผลทดสอบของร่างเอกสารนี้

## 10. สิ่งที่ผู้ดูแลต้องระบุใน Issue เพื่ออนุมัติ

Issue ต้องระบุครบสี่รายการนี้ก่อนเริ่มงาน session ความเสี่ยงสูง:

1. **Design choice:** เลือกการปลดล็อกครั้งแรกและยืนยันว่าจะใช้ `hashlib.scrypt` passphrase ตามข้อเสนอ หรือเลือกทางเลือกใดพร้อมเหตุผล
2. **Storage:** ยืนยัน `owner_sessions` และ allowlist ของตาราง/คอลัมน์/constraint/index ตามข้อ 3 หรือระบุ store ทางเลือกและ trade-off; หากเลือกตารางให้ระบุชัดว่าอนุมัติ DDL เฉพาะรายการใด
3. **Cookie attributes:** ยืนยันชื่อ cookie, `HttpOnly`, `SameSite`, `Path`, host-only/`Domain`, อายุ `Max-Age` และเหตุผลที่ไม่มี `Secure` บน loopback HTTP
4. **Dev-mode rule:** ยืนยันวิธีเปิด fixture mode ที่ off by default, ข้อกำหนด `synthetic-*`, และการบังคับใช้กับ e2e/demo เท่านั้น

การอนุมัติเอกสารนี้หรือการ merge เอกสารไม่ใช่การอนุมัติให้เขียนโค้ด session, migration หรือ cookie

## 11. คำถามที่รอคำตัดสิน

1. **KDF:** ใช้ `N=2**14`, `2**15` หรือค่าที่วัดได้บนเครื่องขั้นต่ำ? **แนะนำ:** เริ่มวัดที่ `2**15`, เก็บพารามิเตอร์และรุ่นไว้กับ verifier แล้วใช้ค่าที่ผ่านเกณฑ์เวลา/หน่วยความจำของเจ้าของเครื่อง
2. **Passphrase policy และการลองซ้ำ:** บังคับขั้นต่ำ 12 ตัวอักษร, ใช้ passphrase ที่ผู้ใช้เลือกโดยไม่มี minimum, หรือสร้างให้? จำกัดความพยายามด้วย lockout หรือ backoff? **แนะนำ:** ยอมรับ passphrase ยาวอย่างน้อย 12 ตัวโดยไม่มี composition rule, จำกัด 5 ครั้งต่อ 15 นาทีด้วย backoff และเก็บตัวนับนอก repository
3. **Session store:** `owner_sessions` ใน PostgreSQL, in-memory, หรือ file store? **แนะนำ:** ตาราง PostgreSQL ตาม allowlist เพราะ revoke/expiry ใช้ได้ข้าม process; ยอมรับ DDL เพิ่มหนึ่งตารางในงาน implementation ที่ได้รับอนุมัติ
4. **อายุ session:** 8 ชั่วโมง/idle 15 นาที, 12 ชั่วโมง/idle 30 นาที, หรือ 24 ชั่วโมง/idle 1 ชั่วโมง? **แนะนำ:** 12 ชั่วโมง/30 นาทีตามข้อ 3 และคงอายุสูงสุดแบบไม่เลื่อน
5. **Fixture opt-in:** ใช้ `PAIYA_DEV_FIXTURE_MODE=1` คู่กับ group key หรือใช้ startup flag แยก? **แนะนำ:** ใช้ opt-in ที่เห็นชัดและ off by default; ไม่ว่าเลือกแบบใด key อย่างเดียวต้องไม่ bypass session และใช้ได้เฉพาะ fixture สำหรับ e2e/demo
