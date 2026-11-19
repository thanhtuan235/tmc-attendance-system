"""Smoke test cho T1-01: đảm bảo các package import được và luôn có ít nhất một test
(pytest không tìm thấy test nào sẽ trả mã lỗi 5)."""

import importlib

import pytest

PACKAGES = [
    "edge.cmp01_kiosk_control.entry",
    "edge.cmp01_kiosk_control.orchestration",
    "edge.cmp01_kiosk_control.ports",
    "edge.cmp01_kiosk_control.adapters",
    "edge.cmp01_kiosk_control.config",
    "cloud.cmp03_attendance_mini",
    "packages.contracts",
    "tools",
]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_importable(name):
    assert importlib.import_module(name) is not None