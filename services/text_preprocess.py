"""
Vendor / narration text for ML: strip UPI VPAs, phones, txn refs, and long numbers.
"""
import re

_MULTI_SPACE = re.compile(r"\s+")
_LONG_NUM = re.compile(r"\b\d{8,}\b")
_UTR_RRN = re.compile(r"\b(?:utr|rrn|ref|txn|txnid)\b[\s:.-]*[a-z0-9-]{6,}\b", re.I)
_VPA = re.compile(r"\b[a-z0-9][a-z0-9._-]{0,63}@[a-z][a-z0-9.-]+\b", re.I)
_PHONE = re.compile(r"\b(?:\+91|0)?[6-9]\d{9}\b")
_CARD_TAIL = re.compile(r"\b(?:xx+|x{2,}\d{4}|\*{4,}\d{4})\b", re.I)


def ml_input_text(vendor: str) -> str:
    if not vendor:
        return ""
    s = str(vendor).strip()
    s = _UTR_RRN.sub(" ", s)
    s = _VPA.sub(" ", s)
    s = _PHONE.sub(" ", s)
    s = _CARD_TAIL.sub(" ", s)
    s = _LONG_NUM.sub(" ", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    return s
