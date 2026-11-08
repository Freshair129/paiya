# ผลวัด A9b — fixture (A)

ผลนี้ใช้ป้ายที่ reviewer เอเจนต์อิสระตรวจตามที่ผู้ดูแลมอบหมายใน #72 ไม่ใช่ป้ายที่คนตรวจ

labels.json SHA-256: `48d9659d41437c58a29fa283ce01ec14e66a6e4ec841eba86cc505ccbdf2a551` · label_status: `agent-reviewed-owner-delegated`
ผลนี้เป็น fixture สังเคราะห์ ไม่ใช่ผล pilot

## การค้นพบ AT-02

Corpus total: 112/100 minimum
Visible recall รวม: 37/37
- `discovery/D01-pinned-scope/feed.json`: 6/6
  - `r1`: 6/6
- `discovery/D02-reordered-batches/feed.json`: 7/7
  - `page-1`: 5/5
  - `page-2`: 5/5
- `discovery/D03-sort-unverified/feed.json`: 6/6
  - `r1`: 6/6
- `discovery/D04-stop-before-overlap/feed.json`: 3/3
  - `r1`: 3/3
- `discovery/D05-duplicate-cards/feed.json`: 5/5
  - `r1`: 5/5
- `discovery/D06-unreadable-cards/feed.json`: 4/4
  - `r1`: 4/4
- `discovery/D07-challenge-page/feed.json`: 0/0
  - `r1`: 0/0
- `discovery/D08-complete-feed/feed.json`: 6/6
  - `r1`: 6/6
- Unreadable: 11/52: D04-C04, D04-C05, D04-C06, D06-C02, D06-C04, D07-C01, D07-C02, D07-C03, D07-C04, D07-C05, D07-C06
- Visible misses: none

## สถานะ AT-03

- Wrong binding: 0/57: none
- Wrong sold: 0/60: none
- Clear-sold recall: 23/23; misses: none
- Stable-ID seller coverage: 57/57
- No stable ID: 3/60: T29-seller-no-stable-id-C-item-1, T30-seller-no-stable-id-C-item-1, T31-seller-no-stable-id-C-item-1

## ตารางสถานะและคำตอบที่ขาด

| Expected | open | reserved | sold | unknown | absent | unanswered / total |
|---|---:|---:|---:|---:|---:|---:|
| open | 25 | 0 | 0 | 0 | 0 | 0/25 |
| reserved | 0 | 3 | 0 | 0 | 0 | 0/3 |
| sold | 0 | 0 | 23 | 0 | 0 | 0/23 |
| unknown | 0 | 0 | 0 | 9 | 0 | 9/9 |

Unanswered cases: T29-seller-no-stable-id-C-item-1, T30-seller-no-stable-id-C-item-1, T31-seller-no-stable-id-C-item-1, T32-unknown-reserve-word-C-item-1, T33-unknown-closed-word-C-item-1, T34-unknown-shipped-word-C-item-1, T35-unknown-still-available-C-item-1, T36-unknown-sold-variant-C-item-1, T37-unknown-free-comment-C-item-1

## ผลกับดัก

- `buyer_comment_claimed_seller`: 3/3; T11-buyer-reserve-role-trap-C-item-1=open, T12-buyer-reserve-role-trap-C-item-1=open, T13-buyer-reserve-role-trap-C-item-1=open
- `reserved_to_open_with_seller_evidence`: 3/3; T26-reserved-to-open-seller-evidence-C-item-1=open, T27-reserved-to-open-seller-evidence-C-item-1=open, T28-reserved-to-open-seller-evidence-C-item-1=open
- `reserved_to_open_without_evidence`: 3/3; T23-reserved-to-open-no-evidence-C-item-1=reserved, T24-reserved-to-open-no-evidence-C-item-1=reserved, T25-reserved-to-open-no-evidence-C-item-1=reserved
- `seller_role_actor_mismatch`: 3/3; T14-seller-actor-mismatch-C-item-1=open, T15-seller-actor-mismatch-C-item-1=open, T16-seller-actor-mismatch-C-item-1=open
- `seller_without_stable_id`: 3/3; T29-seller-no-stable-id-C-item-1=unknown, T30-seller-no-stable-id-C-item-1=unknown, T31-seller-no-stable-id-C-item-1=unknown
- `two_items_one_sells`: 6/6; T20-two-items-one-sold-C-item-1=sold, T20-two-items-one-sold-C-item-2=open, T21-two-items-one-sold-C-item-1=sold, T21-two-items-one-sold-C-item-2=open, T22-two-items-one-sold-C-item-1=sold, T22-two-items-one-sold-C-item-2=open
- `unlinked_closing_evidence`: 3/3; T17-unlinked-closing-evidence-C-item-1=open, T18-unlinked-closing-evidence-C-item-1=open, T19-unlinked-closing-evidence-C-item-1=open

## ข้อแตกต่างและข้อจำกัด

- D05-C06: contract unclear — feed หยุดด้วย CaptureConflict ก่อนถึงการ์ดนี้ จึงยังสรุปไม่ได้ว่าป้ายหรือระบบผิด; [collector contract](../../../docs/architecture/contracts/collector-and-query.md) ระบุ “payload_hash ต่างกันเป็น conflict” ส่วน [discovery contract](../../../docs/architecture/contracts/discovery-queue.md) ระบุ “ส่ง envelope เข้า ingest ของ A2” แต่ไม่ได้ระบุผลรอบเมื่อ conflict ระหว่างการ์ด ต้องให้เจ้าของตัดสิน fixture/พฤติกรรมรอบ
- D01-C02: ระบบพบ d01-pin-out แม้ไม่ติด visible; [discovery contract](../../../docs/architecture/contracts/discovery-queue.md) ระบุ “จำนวนการ์ดที่โหลดเห็นทั้งหมดรวม pinned” และ fixture ไม่มีฟิลด์ scope แยกการ์ดนี้ จัดเป็น label looks wrong against the contract; label visible=false ใช้ตัด denominator ไม่ใช่ข้อห้ามพบโพสต์
- D05-C05: the original capture IDs differ; the worker derives the same capture ID for both cards from run and post key, while their seller and item payloads differ.
- Unknown ตามป้ายเป็น unanswered ตามนิยามรายงาน มิได้แปลว่าเป็นความผิดพลาดของระบบ
