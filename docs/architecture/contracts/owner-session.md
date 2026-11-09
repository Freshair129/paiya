# สัญญา session เจ้าของเครื่องและ CSRF ของ API ในเครื่อง

**สถานะ** เจ้าของอนุมัติให้ทำ S1 ใน [Issue #94](https://github.com/Freshair129/paiya/issues/94) แล้ว · **ผู้ดูแล** Product owner, Backend และ QA

เอกสารนี้กำหนดสัญญา S1 ตามการอนุมัติความเสี่ยงสูงของเจ้าของใน [Issue #94](https://github.com/Freshair129/paiya/issues/94#issuecomment-6731421313) ใช้คู่กับ [นโยบายการเข้าถึง](../SPEC.md), [สัญญาผู้ซื้อ](buyer-workflows.md), [สัญญาเส้นทางอ่านบนมือถือ](mobile-read-path.md), [SRS ข้อ 6](../../requirements/SRS.md), [ROADMAP O5](../../delivery/ROADMAP.md) และ [สัญญาสุขภาพระบบ](system-health.md) การอนุมัติครอบคลุม S1 และ DDL allowlist ในข้อ 10; งานนอกขอบเขตนั้นต้องขออนุมัติเพิ่มใน Issue ก่อนเริ่ม

## 1. ขอบเขตและ threat model

| สถานะ | ขอบเขต |
|---|---|
| ครอบคลุม | API ผูกเฉพาะ loopback บนเครื่องเดียว มีเจ้าของหนึ่งคนต่อเครื่อง ป้องกันเว็บอื่นที่พยายามส่งคำขอผ่าน browser และปฏิเสธ DNS rebinding ด้วยการตรวจ `Host` ทุกคำขอและ `Origin` ของคำขอเขียน |
| ไม่ครอบคลุม | การเปิด API ผ่าน LAN หรือโทรศัพท์ ซึ่งเป็น O5 และต้องมี HTTPS กับการตัดสินแยก; ผู้ใช้หลายคนต่อเครื่อง; การยึดเครื่องหรือ process โดย malware/ผู้ใช้ใน Windows account เดียวกัน |

การจำกัด `Origin` และ `Host` ช่วยป้องกัน browser จากเว็บอื่นเรียก API บน loopback และอ่านข้อมูลด้วย DNS rebinding แต่ไม่ใช่ขอบเขตป้องกันโค้ดอันตรายที่ทำงานด้วยสิทธิ์ของเจ้าของเครื่อง

## 2. การตั้งค่าและปลดล็อกครั้งแรก

**แนวทางที่อนุมัติ: ใช้ passphrase ของเจ้าของเครื่องและเก็บเฉพาะตัวตรวจสอบที่ได้จาก `hashlib.scrypt`.** ใช้ Python standard library (`getpass`, `secrets`, `hashlib`, `hmac`, `pathlib`) โดยไม่เพิ่ม dependency

- เมื่อเริ่ม `serve` ครั้งแรกและยังไม่มีข้อมูลยืนยันตัวตน ให้ตั้ง passphrase ผ่าน prompt แบบไม่แสดงข้อความบน terminal และถามซ้ำเพื่อยืนยัน หากไม่มี interactive terminal ให้หยุดแบบ fail closed พร้อมบอกวิธีตั้งค่าจาก terminal ห้ามสร้างค่าเริ่มต้นหรือปลดล็อกอัตโนมัติ
- สร้าง salt ด้วย `secrets.token_bytes(16)` และตัวตรวจสอบด้วย `hashlib.scrypt`; เก็บรุ่น KDF, พารามิเตอร์, salt และผลลัพธ์เท่านั้น ไม่เก็บ passphrase ตรง ๆ เจ้าของอนุมัติให้เริ่มวัดพารามิเตอร์จาก `N=2**15`, `r=8`, `p=1`, `dklen=32` ตาม Issue #94 และให้บันทึกค่าที่ใช้ไว้กับ verifier
- เก็บไฟล์ เช่น `%LOCALAPPDATA%\Paiya\security\owner-auth.json` นอก repository โดยปิดการสืบทอด ACL และอนุญาตเฉพาะ SID ของเจ้าของเครื่องกับ `SYSTEM`; ห้ามให้ `Users` หรือ `Everyone` อ่านได้ เขียนไฟล์ด้วยวิธี atomic และตั้ง ACL ให้ถูกต้องตั้งแต่สร้างไฟล์ หากรองรับ POSIX ให้ใช้ directory `0700` และไฟล์ `0600`
- ห้ามใส่ passphrase ใน argument, environment variable, repository, backup ที่ไม่เข้ารหัส หรือ log การตรวจ passphrase ใช้ `hmac.compare_digest`; เมื่อเปลี่ยน passphrase ต้องเพิกถอนทุก session
- `/v1/auth/challenge` ออก pre-auth synchronizer nonce แบบสุ่ม ใช้ครั้งเดียวและหมดอายุใน 5 นาที เก็บเฉพาะ hash ในหน่วยความจำ คำขอ `POST /v1/auth/unlock` ต้องส่ง nonce ใน `X-CSRF-Token` พร้อม passphrase และผ่าน `Origin`/`Host` check ก่อนสร้าง session

| ทางเลือก | ผลและข้อแลกเปลี่ยน |
|---|---|
| One-time setup token ที่ `serve` แสดงใน console | ติดตั้งครั้งแรกง่ายและมี entropy สูง แต่ต้องกำหนดการเก็บ/กู้ credential หลังใช้ token และต้องรับประกันว่า stdout ไม่ถูกส่งเข้า log; อาจใช้เป็นตัวเปิดหน้าตั้ง passphrase ได้ |
| OS user check | ไม่ต้องมี passphrase แต่การตรวจ process account ไม่ยืนยัน browser และไม่แยกเว็บภายนอกจาก browser ของเจ้าของเครื่อง จึงไม่พอเป็นกลไกปลดล็อกนี้ |

## 3. Cookie และ server-side session

สร้าง session token ด้วย `secrets.token_urlsafe(32)` (สุ่ม 32 bytes) และเก็บเพียง SHA-256 digest ของ token ใน PostgreSQL ตรวจ digest ด้วยการเปรียบเทียบแบบ constant-time เจ้าของอนุมัติตารางใหม่นี้เป็น DDL เพิ่มเพียงรายการเดียวใน Issue #94:

| ตาราง | คอลัมน์ที่อนุมัติ |
|---|---|
| `owner_sessions` | `id UUID PRIMARY KEY` (ID ภายในสำหรับอ้างอิง log), `token_hash BYTEA NOT NULL UNIQUE`, `csrf_token TEXT NOT NULL`, `created_at TIMESTAMPTZ NOT NULL`, `last_seen_at TIMESTAMPTZ NOT NULL`, `expires_at TIMESTAMPTZ NOT NULL`, `revoked_at TIMESTAMPTZ NULL` |

`csrf_token` เป็น synchronizer token สุ่มจาก `secrets.token_urlsafe(32)` ผูกกับ session; ส่งให้ client ในผล unlock และผ่าน `GET /v1/auth/csrf` ซึ่งต้องมี session และตั้ง `Cache-Control: no-store` เพื่อให้หน้าเว็บโหลดใหม่ได้ ห้าม log ค่านี้ ควรมีดัชนีตาม `expires_at` สำหรับค้น/เก็บกวาดแถวหมดอายุ และ constraint ให้ `expires_at > created_at`; ไม่เก็บ passphrase, cookie token แบบ plaintext, grant ID หรือเนื้อหาผู้ซื้อในตารางนี้

- อายุสูงสุดแบบตายตัวคือ 12 ชั่วโมงนับจาก unlock (`expires_at`); idle timeout คือ 30 นาทีจาก `last_seen_at` การอ่านหรือเขียนที่ยืนยัน session แล้วปรับ `last_seen_at` แต่ไม่เลื่อนอายุสูงสุด
- ตั้ง cookie `paiya_owner_session` เป็น host-only (ไม่มี `Domain`), `HttpOnly`, `SameSite=Strict`, `Path=/`, `Max-Age=43200`; ไม่ตั้ง `Secure` เพราะ listener ตาม ADR 0004 ใช้ plain loopback HTTP และ browser อาจไม่ส่ง Secure cookie บน HTTP ไม่ใช้ prefix `__Host-` ที่ต้องพึ่ง `Secure`
- อนุญาต origin ของ `127.0.0.1` และ `localhost` ตามข้อ 4 แต่ cookie ไม่มี `Domain`: browser จึงแยก cookie ตาม hostname ผู้ใช้ต้องใช้ hostname เดิมตลอด session
- เมื่อปลดล็อกสำเร็จ ให้สร้าง token และ CSRF token ใหม่ทุกครั้ง; หากคำขอมี session เดิม ให้เพิกถอน session เดิมก่อนออกค่าใหม่เพื่อกัน session fixation
- `POST /v1/auth/logout` ต้องผ่าน CSRF แล้วตั้ง `revoked_at` ให้ session ปัจจุบันและลบ cookie ด้วย `Max-Age=0`; คำขอถัดไปด้วย cookie เดิมต้องได้ 401
- `POST /v1/auth/revoke-all` ต้องผ่าน CSRF แล้วกำหนด `revoked_at` ให้ทุก session ที่ยังไม่เพิกถอน รวมถึง session ที่เรียก จากนั้นลบ cookie ของผู้เรียก คำขอที่ใช้ session เก่าทั้งหมดต้องได้ 401

เจ้าของเลือกตาราง PostgreSQL สำหรับ S1 เพื่อให้ตรวจและเพิกถอน session ได้จากทุก request และทุก process ของ API; ทางเลือก in-memory หรือ file store ไม่อยู่ในขอบเขตที่อนุมัติ

## 4. CSRF และการตรวจ `Origin`/`Host`

- ตรวจ `Host` ในทุก HTTP request รวม read, static app และ auth challenge เพื่อกัน DNS rebinding ค่า authority ที่รับได้มีเพียง `127.0.0.1:<PAIYA_PORT>` หรือ `localhost:<PAIYA_PORT>` โดย port ต้องตรงกับ listener จริง; ไม่รับ host อื่น, user-info, suffix/trailing dot, หลายค่า หรือ `X-Forwarded-Host`/`Forwarded` เป็นตัวแทน
- สำหรับทุกคำขอเขียน `POST`, `PUT`, `PATCH`, `DELETE` ต้องมี `Origin` ที่ parse ได้ตรงกับ `http://127.0.0.1:<PAIYA_PORT>` หรือ `http://localhost:<PAIYA_PORT>` และ hostname/port ต้องตรงกับ `Host` ของคำขอ ขาด `Origin`, เป็น `null`, ใช้ scheme/port/host อื่น หรือ `Host` ไม่ตรง ให้ตอบ 403 โดยไม่ใช้ `Referer` แทน
- คำขอเขียนที่มี session ต้องส่ง synchronizer token ใน `X-CSRF-Token` และตรงกับ token ของ session นั้น การปลดล็อกเป็นข้อยกเว้นด้านสถานะ session แต่ยังเป็นคำขอเขียน: ต้องใช้ pre-auth nonce แบบใช้ครั้งเดียวจาก `/v1/auth/challenge` พร้อมตรวจ `Origin` และ `Host`
- `GET`, `HEAD` และ `OPTIONS` ไม่เปลี่ยน state; method อื่นที่ไม่รู้จักตอบ 405 ห้ามอนุมานว่า method ที่ไม่รู้จักเป็น read
- ไม่เปิด CORS ให้ origin อื่น และทุก endpoint ที่คืน session/challenge/CSRF ต้องตั้ง `Cache-Control: no-store`

## 5. Read context และการเปลี่ยนจาก fixture

ในโหมดใช้งานปกติ session ยืนยันว่าเป็นเจ้าของเครื่องเท่านั้น API ต้อง resolve รายการ `source_grants` ที่มี `read`, ยังไม่ถูกเพิกถอน และอยู่ในช่วง `valid_from`/`valid_until` ณ เวลาของ request แล้วส่ง `list[ReadableGrant]` ให้ read service ตาม ADR 0004 ทุก read รวม endpoint ภาพต้องตรวจ grant ซ้ำใน service layer ห้ามรับ group/grant จาก cookie, header, query หรือ body เพื่อเพิ่มสิทธิ์

`PAIYA_READ_CONTEXT_GROUP_KEY` ใน `mobile-read-path.md` เป็น fixture context ไม่ใช่การยืนยันตัวตนสำหรับข้อมูลจริง. S1 กำหนดเส้นทางเปลี่ยนผ่านดังนี้:

1. โหมดปกติปิด fixture bypass และต้องมี owner session; ตั้ง `PAIYA_READ_CONTEXT_GROUP_KEY` อย่างเดียวไม่ให้สิทธิ์อ่าน
2. dev-only fixture mode ปิดโดยปริยายและเปิดได้ด้วยการตั้งค่าอย่างชัดเจน เช่น `PAIYA_DEV_FIXTURE_MODE=1` พร้อม `PAIYA_READ_CONTEXT_GROUP_KEY=synthetic-...`; mode นี้เลือกได้เฉพาะ `groups.source = fixture` และ grant `read` ที่ยังใช้ได้
3. e2e และ demo ต้องเปิด fixture mode นี้อย่างชัดเจน เพื่อให้ `paiya demo seed` และข้อมูลสาธิตทำงานต่อ; การทดสอบยืนยันว่า default ปิดอยู่และการตั้ง group key อย่างเดียวไม่ bypass session
4. dependency ใน `src/paiya/api/` ใช้ session และ `list[ReadableGrant]`; read service ตรวจขอบเขตซ้ำ ไม่ฝากการบังคับสิทธิ์ไว้กับ FastAPI dependency เพียงชั้นเดียว

fixture context ใช้ได้เฉพาะ dev opt-in สำหรับ fixture; ห้ามนำ dev fixture mode ไปใช้กับข้อมูลจริง

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

ข้อความที่ client แสดง: 401 `session_required` หมายถึงเจ้าของเครื่องต้องปลดล็อก; 403 หมายถึงไม่อนุญาตให้ดำเนินการนี้ โดยไม่แสดงรายละเอียดภายในของการตรวจ `Host`, `Origin`, CSRF หรือ grant

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

รายการข้างต้นเป็นเกณฑ์การยอมรับตามสัญญา ไม่ใช่บันทึกผลรันทดสอบ; ผลจริงของ implementation ต้องรายงานแยกใน Issue หรือ PR

## 10. การอนุมัติและขอบเขต

เจ้าของอนุมัติ S1 ความเสี่ยงสูงใน [Issue #94](https://github.com/Freshair129/paiya/issues/94#issuecomment-6731421313) โดยกำหนด:

- **Unlock:** terminal passphrase กับ `hashlib.scrypt` verifier นอก repository; เริ่มวัด KDF จาก `N=2**15`; ขั้นต่ำ 12 ตัวอักษร; จำกัด 5 ครั้งต่อ 15 นาที
- **Storage/DDL:** เพิ่มเฉพาะตาราง `owner_sessions` พร้อมคอลัมน์, constraint และ index ตามข้อ 3 ไม่มี DDL อื่น
- **Cookie/session:** `paiya_owner_session`, `HttpOnly`, `SameSite=Strict`, `Path=/`, host-only, `Max-Age=43200`, ไม่มี `Secure` บน loopback HTTP; อายุสูงสุด 12 ชั่วโมงและ idle timeout 30 นาที
- **Dev fixture mode:** ปิดโดยปริยายและเปิดด้วย opt-in เฉพาะ e2e และ demo

การอนุมัตินี้จำกัดอยู่ที่ขอบเขต S1 ใน Issue #94; การเพิ่มตารางหรือเปลี่ยนขอบเขตความเสี่ยงสูงต้องขออนุมัติใน Issue ก่อน

## 11. งานติดตามใน M-phase

`GET /v1/prices/trends` คืน 409 `read_scope_ambiguous` เมื่อ session มี readable grant มากกว่าหนึ่งรายการใน S1; การเลือกกลุ่มเป็นงานต่อเนื่องของ M-phase และต้องเพิ่ม group selector ก่อนเปลี่ยนพฤติกรรมนี้
