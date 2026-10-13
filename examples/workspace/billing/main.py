from src.keys import normalize_key

def handle(item):
    key = normalize_key(item)
    if not key:
        return "missing"
    return key
