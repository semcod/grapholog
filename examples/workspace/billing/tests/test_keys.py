from src.keys import normalize_key

def test_blank():
    assert normalize_key("  ") == ""
