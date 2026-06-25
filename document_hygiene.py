"""
Layer 0 — document hygiene scan.

Before any attachment is parsed or sent to the extraction model, scan it for
*active / hidden content* that could make the document itself an attack:

  PDF   — embedded JavaScript (/JS, /JavaScript), auto-run actions (/OpenAction,
          /AA), launch-external-program actions (/Launch), embedded files
          (/EmbeddedFile), submit-to-URL forms (/SubmitForm), embedded media.
  Office— VBA macros (vbaProject.bin in OOXML; VBA streams in legacy OLE),
          and remote-template injection (external relationship targets).

Design notes
------------
* Pure standard library — no native deps required — so it runs anywhere.
* PDF name objects can be hex-obfuscated (e.g. /J#61vaScript). We de-obfuscate
  #xx escapes before token matching, the same trick pdfid uses.
* This is a *triage* scanner: it flags documents that warrant quarantine, it is
  not a full sandbox. Anything FAIL should be quarantined and never opened or
  fed to the extractor.
"""
from __future__ import annotations

import io
import re
import zipfile

# token (bytes)        -> (human label, severity)
_PDF_TOKENS = {
    b"/JavaScript":  ("Embedded JavaScript", "FAIL"),
    b"/JS":          ("Embedded JavaScript (/JS)", "FAIL"),
    b"/OpenAction":  ("Auto-run action when the file is opened", "FAIL"),
    b"/AA":          ("Additional (automatic) actions", "FAIL"),
    b"/Launch":      ("Launch-external-program action", "FAIL"),
    b"/EmbeddedFile": ("Embedded file payload", "FAIL"),
    b"/SubmitForm":  ("Form that submits data to a URL", "WARN"),
    b"/RichMedia":   ("Embedded rich media / Flash", "WARN"),
}

_HEX_ESC = re.compile(rb"#([0-9A-Fa-f]{2})")


def _deobfuscate(raw: bytes) -> bytes:
    """Resolve PDF name #xx hex escapes so obfuscated tokens still match."""
    return _HEX_ESC.sub(lambda m: bytes([int(m.group(1), 16)]), raw)


def _scan_pdf(data: bytes) -> list:
    findings = []
    hay = _deobfuscate(data)
    for token, (label, sev) in _PDF_TOKENS.items():
        if token in hay:
            findings.append({"token": token.decode(), "detail": label, "severity": sev})
    return findings


def _scan_office(data: bytes, filename: str) -> list:
    findings = []
    # OOXML (.docx/.xlsx/.pptx) are ZIP archives
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if any(n.lower().endswith("vbaproject.bin") for n in names):
                    findings.append({"token": "vbaProject.bin", "detail": "VBA macro project embedded", "severity": "FAIL"})
                # remote-template injection: external relationship targets
                for n in names:
                    if n.endswith(".rels"):
                        rel = z.read(n)
                        if b'TargetMode="External"' in rel and (b"dotm" in rel or b"template" in rel.lower() or b"http" in rel):
                            findings.append({"token": "external-template", "detail": "References an external template/URL (possible remote-template injection)", "severity": "WARN"})
                            break
        except zipfile.BadZipFile:
            pass
    # Legacy OLE compound file (.doc/.xls/.ppt)
    elif data[:8] == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1":
        up = data.upper()
        if b"VBA" in up or b"MACROS" in up:
            findings.append({"token": "ole-vba", "detail": "Legacy OLE document appears to contain VBA macros", "severity": "FAIL"})
        else:
            findings.append({"token": "ole-legacy", "detail": "Legacy OLE document — may contain macros; treat with caution", "severity": "WARN"})
    return findings


def scan_attachment(filename: str, content_type: str, data: bytes) -> dict:
    """Scan a single attachment. Returns {filename, status, findings:[...]}.

    status: CLEAN (no active content) | WARN (review) | FAIL (quarantine)."""
    fn = (filename or "").lower()
    ct = (content_type or "").lower()
    findings = []

    if data[:5] == b"%PDF-" or fn.endswith(".pdf") or "pdf" in ct:
        findings += _scan_pdf(data)
    if (data[:4] == b"PK\x03\x04" and any(fn.endswith(e) for e in (".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"))) \
       or data[:8] == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" \
       or any(fn.endswith(e) for e in (".doc", ".xls", ".ppt")):
        findings += _scan_office(data, fn)

    if any(f["severity"] == "FAIL" for f in findings):
        status = "FAIL"
    elif any(f["severity"] == "WARN" for f in findings):
        status = "WARN"
    else:
        status = "CLEAN"

    return {"filename": filename, "status": status, "findings": findings}


def scan_attachments(attachments: list) -> dict:
    """
    Scan a list of attachments (as returned by email_ingestor.ingest_eml):
    each item has keys 'filename', 'content_type', 'bytes'.

    Returns {status, detail, attachments:[...]} or None if there is nothing to scan.
    """
    if not attachments:
        return None

    results = [scan_attachment(a.get("filename", ""), a.get("content_type", ""), a.get("bytes", b"")) for a in attachments]
    worst = "CLEAN"
    for r in results:
        if r["status"] == "FAIL":
            worst = "FAIL"
            break
        if r["status"] == "WARN":
            worst = "WARN"

    if worst == "FAIL":
        bad = next(r for r in results if r["status"] == "FAIL")
        labels = ", ".join(f["detail"] for f in bad["findings"] if f["severity"] == "FAIL")
        detail = (f"Attachment '{bad['filename']}' contains active content ({labels}). "
                  f"Quarantined — do NOT open; the document itself may be the attack.")
    elif worst == "WARN":
        warn = next(r for r in results if r["status"] == "WARN")
        labels = ", ".join(f["detail"] for f in warn["findings"])
        detail = f"Attachment '{warn['filename']}' has features that warrant review ({labels})."
    else:
        detail = f"{len(results)} attachment(s) scanned — no active or hidden content detected."

    return {"status": worst, "detail": detail, "attachments": results}
