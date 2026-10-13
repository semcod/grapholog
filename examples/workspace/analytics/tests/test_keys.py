from src.keys import clean_key

def test_blank():
    assert clean_key("  ") == ""
