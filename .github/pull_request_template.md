## Mã công việc
<!-- Vd: T2-02 -->

## Tóm tắt thay đổi
-

## Cách kiểm thử
<!-- Lệnh đã chạy, ca test mới thêm -->
- [ ] `pytest -q` pass trên máy

## Checklist
- [ ] Phạm vi: một PR = một công việc, khoảng ≤ 400 dòng thay đổi
- [ ] Test: có unit test cho code mới; CI xanh
- [ ] Ranh giới: không sửa thư mục của người khác; không đổi `ports/` khi T1 chưa đồng ý
- [ ] SOLID: lớp mới chỉ phụ thuộc interface trong `ports/`; lắp ráp chỉ ở `main.py` (composition root)
- [ ] Không commit file thừa: `.venv/`, `*.db`, khóa riêng `*.pem`, `__pycache__/`

## Liên kết
Closes #
