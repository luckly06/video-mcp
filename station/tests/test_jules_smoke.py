def normalize_status(value: str) -> str:
    return value.strip().lower()


def test_normalize_status() -> None:
    assert normalize_status("  READY  ") == "ready"
