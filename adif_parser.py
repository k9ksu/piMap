# adif_parser.py

def _normalize_eor(text: str) -> str:
    """
    Normalize various <EOR> capitalizations to <eor> so we can split reliably.
    """
    # This is a bit brute force but safe and clear.
    variants = ["<EOR>", "<Eor>", "<EoR>", "<eOR>", "<eoR>", "<eOr>"]
    for v in variants:
        text = text.replace(v, "<eor>")
    return text


def parse_adif(adif_text: str):
    """
    Parse an ADIF string into a list of dicts.
    Keys are uppercase ADIF field names.
    Simple but good enough for QRZ logbook output.
    """
    records = []
    if not adif_text:
        return records

    normalized = _normalize_eor(adif_text)
    for raw in normalized.split("<eor>"):
        raw = raw.strip()
        if not raw:
            continue

        rec = {}
        chunks = raw.split("<")
        for c in chunks:
            if ":" not in c or ">" not in c:
                continue
            try:
                field, rest = c.split(":", 1)
                length_str, value = rest.split(">", 1)

                field = field.strip().upper()
                # length may be like "8" or "8:S" etc – keep digits only
                digits = "".join(ch for ch in length_str if ch.isdigit())
                if not digits:
                    continue
                length = int(digits)

                rec[field] = value[:length]
            except Exception:
                # ignore malformed bits
                pass

        if rec:
            records.append(rec)

    return records
