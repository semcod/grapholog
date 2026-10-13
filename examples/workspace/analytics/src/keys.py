def clean_key(text):
    if text is None:
        return ""
    return str(text).strip().lower()
