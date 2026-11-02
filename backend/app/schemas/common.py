from enum import StrEnum


class BuildingType(StrEnum):
    nha_pho = "nha_pho"
    biet_thu = "biet_thu"
    can_ho = "can_ho"
    van_phong = "van_phong"
    khach_san = "khach_san"
    khac = "khac"


class WorkScope(StrEnum):
    xay_tho = "xay_tho"
    hoan_thien = "hoan_thien"
    tron_goi = "tron_goi"
    noi_that = "noi_that"


class QualityTier(StrEnum):
    tiet_kiem = "tiet_kiem"
    trung_binh = "trung_binh"
    kha = "kha"
    cao_cap = "cao_cap"


BUILDING_TYPE_VI: dict[str, str] = {
    "nha_pho": "Nhà phố", "biet_thu": "Biệt thự", "can_ho": "Căn hộ",
    "van_phong": "Văn phòng", "khach_san": "Khách sạn", "khac": "Khác",
}
WORK_SCOPE_VI: dict[str, str] = {
    "xay_tho": "Xây thô", "hoan_thien": "Hoàn thiện", "tron_goi": "Trọn gói", "noi_that": "Nội thất",
}
QUALITY_TIER_VI: dict[str, str] = {
    "tiet_kiem": "Tiết kiệm", "trung_binh": "Trung bình", "kha": "Khá", "cao_cap": "Cao cấp",
}
