# ERD ฐานข้อมูลป้ายยา

**สถานะ** แบบจำลองเชิงตรรกะสำหรับ PostgreSQL 17 · 7 พฤศจิกายน 2026

อ่านรูปจากคีย์ด้านพ่อไปหาคีย์ด้านลูก `||` หมายถึงหนึ่งพอดี `o|` หรือ `|o` หมายถึงศูนย์หรือหนึ่ง และ `o{` หมายถึงศูนย์ถึงหลายแถว ตารางที่ปรากฏซ้ำเป็นตารางเดียวกัน ภาพแสดงความสัมพันธ์หลัก ส่วน composite FK ที่ป้องกันข้อมูลข้ามโพสต์และข้ามสิทธิ์ระบุในพจนานุกรม [แบบจำลองข้อมูลและกฎตรวจ](../data-model.md)

## 1. ใครให้สิทธิ์ และได้ข้อมูลมาจากรอบใด

```mermaid
erDiagram
    groups ||--o{ source_grants : authorizes
    source_grants ||--o{ jobs : permits
    jobs ||--o| capture_runs : records
    capture_runs ||--o{ observations : captures
    posts ||--o{ observations : appears_in

    source_grants {
        uuid id PK
        uuid group_id FK
        text source_kind
        timestamptz revoked_at
    }
    observations {
        uuid id PK
        uuid run_id FK
        uuid post_id FK
        uuid grant_id FK
        text capture_id
        timestamptz observed_at
    }
```

งาน discovery หนึ่งรอบอาจเห็นหลายโพสต์ ส่วนการส่ง P1 ด้วยมือสร้างรอบที่ระบุวิธีนำเข้าและสิทธิ์ของผู้ส่ง การ retry รอบเดิมอ้าง run และ capture เดิม หนึ่งโพสต์จึงมีประวัติได้หลายรอบภายใต้สิทธิ์ต่างกัน งานคิวที่ใช้ตีความข้อมูลมี job โดยไม่มี capture_run

## 2. ชิ้นสินค้า ผู้ขาย และข้อเสนอข้ามกลุ่ม

```mermaid
erDiagram
    groups ||--o{ posts : contains
    sellers |o--o{ posts : publishes
    posts ||--o{ listing_items : offers
    listing_items ||--o| offer_cluster_members : participates
    offer_clusters ||--o{ offer_cluster_members : groups

    listing_items {
        uuid id PK
        uuid post_id FK
        text source_item_key
        text model_key
        text variant
        int vram_gb
    }
    offer_cluster_members {
        uuid item_id PK, FK
        uuid cluster_id FK
        uuid evidence_id FK
    }
```

ชื่อผู้ขายที่ยังยืนยันไม่ได้อนุญาตให้โพสต์ไม่มี seller_id หากมีหลักฐานว่าชิ้นเดียวกันถูกนำไปลงสองกลุ่ม ให้คงโพสต์และชิ้นของแต่ละกลุ่มไว้ สมาชิก cluster ช่วยตัดการนับข้อเสนอซ้ำเมื่อคำนวณราคากลาง

## 3. ตรวจว่าใครพูด และหลักฐานมาจากไหน

```mermaid
erDiagram
    posts ||--o{ comments : hosts
    comments ||--|{ comment_versions : has
    observations ||--o{ comment_versions : observes
    observations ||--o{ evidence : supplies
    comment_versions |o--o{ evidence : quoted_by
    media_assets |o--o{ evidence : illustrates

    evidence {
        uuid id PK
        uuid observation_id FK
        uuid grant_id FK
        uuid comment_version_id FK
        uuid media_asset_id FK
        text local_key
        text role
    }
```

หนึ่งหลักฐานอาจเป็นข้อความอย่างเดียว ข้อความจากคอมเมนต์ต้องชี้รุ่นข้อความที่พบในรอบนั้นและตรวจผู้เขียนก่อนตั้งสถานะ ภาพเดียวใช้กับหลายหลักฐานได้โดยแต่ละหลักฐานมีสิทธิ์ของตน เรียกดูผ่าน evidence ID

## 4. ข้อเท็จจริงที่ผู้ซื้อเห็น

```mermaid
erDiagram
    evidence ||--o{ price_facts : supports
    evidence ||--o{ sale_events : supports
    evidence ||--o{ tags : supports
    listing_items |o--o{ price_facts : priced_by
    listing_items ||--o{ sale_events : changes
    listing_items ||--o| current_items : projects
    sale_events |o--o| current_items : latest_status

    price_facts {
        uuid id PK
        uuid item_id FK
        uuid evidence_id FK
        uuid observation_id FK
        text price_kind
        numeric amount
        string currency
    }
    current_items {
        uuid item_id PK, FK
        uuid status_event_id FK
        uuid ask_fact_id FK
        uuid shipping_fact_id FK
    }
```

ข้อความเดียวรองรับหลายข้อเท็จจริงได้ เช่น ราคาขายกับค่าส่งต่อชิ้น เส้นไป price_facts ยอมให้ item_id ว่างสำหรับจำนวนเงินระดับโพสต์ที่ยังจัดสรรไม่ได้ ค่า current_items เป็นผลปัจจุบันของชิ้นนั้น ส่วนประวัติยังดูได้จาก sale_events และ observations

## 5. ตั้งรับ ติดตาม และอ่านการแจ้งเตือน

```mermaid
erDiagram
    buy_rules ||--o{ rule_item_states : evaluates
    listing_items ||--o{ rule_item_states : evaluated_in
    posts ||--o{ watches : watched_by
    listing_items |o--o{ watches : limits_to
    buy_rules |o--o{ alerts : matches
    watches |o--o{ alerts : notifies
    listing_items ||--o{ alerts : concerns
    evidence ||--o{ alerts : explains

    rule_item_states {
        uuid rule_id PK, FK
        int rule_revision PK
        uuid item_id PK, FK
        boolean last_definite_match
    }
    watches {
        uuid id PK
        uuid post_id FK
        uuid item_id FK
    }
    alerts {
        uuid id PK
        uuid rule_id FK
        uuid watch_id FK
        uuid item_id FK
        uuid evidence_id FK
        text cause_key
    }
```

แต่ละ alert เลือกต้นเหตุจาก buy_rules หรือ watches เพียงทางเดียว ทุกแถวระบุชิ้นที่ได้รับผล Watch ระดับโพสต์มี item_id เป็น NULL หากติดตามเฉพาะ 3060 ให้ชี้ไปที่ชิ้น 3060 โดยยังเปิด permalink เดียวกับ 3070 ได้ คีย์ผสมป้องกันการแจ้งเหตุเดิมซ้ำ และหน้าเทียบดีลดึงข้อมูลจากหลายชิ้นเป็นรายคำขอ

เมื่อเพิกถอนสิทธิ์ให้ปิดการอ่านตั้งแต่ต้นทางไปจนถึงกล่องแจ้งเตือน งานลบทำผ่าน purge_jobs ตามขอบเขตคำขอ ซึ่งอาจครอบคลุมหลายตาราง วิธีเก็บบันทึกลบ แยก reference รูป และทดสอบหลัง restore อยู่ใน [วงจรข้อมูลและดัชนี](../data-model.md) · [เส้นทางการเก็บและการอ่าน](observation-flow.md)
