# D04-stop-before-overlap

รายการมีการ์ดหลัง load_limit ฟีดหยุดหลัง load_limit ก่อนการ์ดที่เหลือ

ข้อจำกัด: feed นี้เป็นรอบแรกของกลุ่มที่ยังไม่มีโพสต์เดิม ตาม discovery-queue.md §4 ข้อ 2 จึงไม่ให้ `feed_stopped_before_overlap`; กรณี “หน้าที่หยุดก่อนถึง overlap” ของ AT-02 ครอบคลุมโดย fixtures/feeds/S08-stop-before-overlap (เทสต์ F ใน A9a) ส่วนนี้ใช้วัด recall ของการ์ดที่โหลดก่อน load_limit เท่านั้น
