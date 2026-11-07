"""Storage names for uploaded files: readable, confined to one directory, and never hidden."""

import pytest

from da_agent.storage.uploads import UploadRejectedError, storage_name


@pytest.mark.parametrize(
    ("original", "stored"),
    [
        ("Đơn hàng tháng 6.xlsx", "Don_hang_thang_6.xlsx"),
        ("sales.CSV", "sales.csv"),
        ("README.md", "README.md"),
        ("../../etc/passwd.csv", "passwd.csv"),
        ("..\\..\\windows\\evil.csv", "evil.csv"),
        (".hidden.csv", "hidden.csv"),
        ("***.csv", "file.csv"),
        ("a" * 300 + ".csv", "a" * 100 + ".csv"),
    ],
)
def test_names_are_made_safe(original: str, stored: str) -> None:
    assert storage_name(original, set()) == stored


def test_a_repeated_name_gets_a_number() -> None:
    assert storage_name("orders.csv", {"orders.csv", "orders_2.csv"}) == "orders_3.csv"


@pytest.mark.parametrize("original", ["run.sh", "model.pkl", "noextension", "data.csv.exe", ""])
def test_files_the_ingestion_cannot_read_are_rejected(original: str) -> None:
    with pytest.raises(UploadRejectedError, match="type"):
        storage_name(original, set())
