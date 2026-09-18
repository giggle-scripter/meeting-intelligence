# V2.27: policy thích ứng theo lượng ứng viên trên DEV42

V2.27 dùng pool runtime `final_plus_bridge_plus_intermediate` đã khóa từ
V2.26 và một epoch của sparse ranker cho mỗi template được giữ lại. Policy
theo từng meeting điều chỉnh threshold và budget dựa trên số candidate,
tỷ lệ nguồn baseline/bridge/intermediate, phân phối score và mức đầy đủ
assignee/due/status. Template, family, case ID, nhãn expected, dữ liệu
diagnostic/final-dev/outer và đầu vào teacher/provider/Kaggle đều bị cấm
làm policy feature.

Package output là
`evaluation/runtime/experimental-distillation-v2/v227-dev42-volume-adaptive-policy/`.
Nó lưu metrics gọn, coverage, policy từng fold, context/lỗi runtime, test,
split access, status và report. Bảng ổn định theo fold/family ghi support rõ;
family/fold không có expected task không tính vào worst-support. Family có
ít nhất năm expected task phải đạt F1 0.40. Run đạt aggregate identity F1
0.57 và dung sai field regression 0.03 trên DEV42 development đã khóa.
