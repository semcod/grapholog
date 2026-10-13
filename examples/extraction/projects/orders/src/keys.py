def normalize_key(value):
    if value is None:
        return ""
    return str(value).strip().lower()
