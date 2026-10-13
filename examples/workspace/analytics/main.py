from src.keys import clean_key

def handle(item):
    key = clean_key(item)
    if not key:
        return "missing"
    return key
