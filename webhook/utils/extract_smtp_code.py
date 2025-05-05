import re
from typing import Optional

# ------------------------------------------------------------------
# 1) 550 5.4.310      2) 550‑5.1.1      3) Diagnostic‑Code: smtp; 550 5.1.1
PAT_BOTH = re.compile(
    r"""\b([245]\d{2})            # 3‑digit basic code
       [\s\-]{0,4}              # space or dash (optional)
       ([245]\.\d+\.\d{1,3})    # enhanced code x.x.x
    """, re.X)

# Stand‑alone enhanced code (Status: 5.1.1, or "… said: 5.2.2 …")
PAT_ENH = re.compile(r"\b([245]\.\d+\.\d{1,3})\b")

# Stand‑alone basic code (rare but still legal)
PAT_BASIC = re.compile(r"\b([245]\d{2})\b")


def extract_smtp_code(body: str) -> Optional[str]:
    """
    Parse the *body text* of a bounce message and return:
        "basic enhanced"   (one space)   – if both are found
        "basic"                           – if only 3‑digit code exists
        None                              – if no status code present
    """
    body = body or ""

    # 1️⃣  Try to grab both codes on the same line
    m = PAT_BOTH.search(body)
    if m:
        basic, enhanced = m.groups()
        return f"{basic} {enhanced}"

    # 2️⃣  Look for an enhanced code anywhere, then derive a generic basic code
    m = PAT_ENH.search(body)
    if m:
        enhanced = m.group(1)
        basic = {"2": "250", "4": "450", "5": "550"}[enhanced[0]]
        return f"{basic} {enhanced}"

    # 3️⃣  Fall back to any 3‑digit 2xx / 4xx / 5xx we can find
    m = PAT_BASIC.search(body)
    if m:
        return m.group(1)

    return None


# ------------------------- demo -------------------------
if __name__ == "__main__":
    samples = [
        "Remote server returned '550 5.4.310 DNS domain …'",
        "Diagnostic-Code: smtp; 550 5.1.1 User unknown",
        "Status: 5.2.2  Mailbox full",
        "host said: 451 Temporary local problem – please try later",
    ]
    for s in samples:
        print(f"» {extract_smtp_code(s)}") 