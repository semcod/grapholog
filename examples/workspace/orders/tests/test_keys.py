from src.keys import canonical_key

def test_blank():
    assert canonical_key("  ") == ""
