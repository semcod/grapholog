from src.keys import canonical_key

def handle(item):
    key = canonical_key(item)
    if not key:
        return "missing"
    return key
