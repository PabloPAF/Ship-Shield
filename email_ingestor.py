import email
from email import policy
from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import io

FREE_EMAIL_PROVIDERS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "yahoo.co.uk", "protonmail.com", "icloud.com", "live.com",
    "aol.com", "mail.com", "ymail.com",
}

URGENCY_KEYWORDS = {
    "urgent", "asap", "immediate", "action required", "payment due",
    "updated bank", "new account", "bank details changed", "wire transfer",
    "new banking", "revised payment",
}

SUPPORTED_ATTACHMENT_TYPES = {
    "application/pdf",
    "image/jpeg", "image/jpg", "image/png", "image/tiff", "image/webp",
}


def _get_domain(address: str) -> str:
    addr = address.lower()
    if "<" in addr:
        addr = addr.split("<")[-1].strip("> ")
    if "@" in addr:
        return addr.split("@")[-1].strip()
    return ""


def _get_display_name(address: str) -> str:
    if "<" in address:
        return address.split("<")[0].strip().strip('"')
    return ""


def _check_vec_headers(msg: Message) -> list:
    flags = []
    sender = msg.get("from", "")
    reply_to = msg.get("reply-to", "")
    subject = msg.get("subject", "").lower()

    sender_domain = _get_domain(sender)
    reply_domain = _get_domain(reply_to)

    # Check 1 (HIGH): reply-to domain differs from sender domain
    if reply_domain and reply_domain != sender_domain:
        flags.append({
            "severity": "HIGH",
            "code": "REPLY_TO_MISMATCH",
            "detail": (
                f"Reply-to domain ({reply_domain}) differs from sender domain "
                f"({sender_domain}) — classic VEC redirect: replies go to attacker."
            ),
        })

    # Check 2 (MEDIUM): sender domain is a free email provider
    if sender_domain in FREE_EMAIL_PROVIDERS:
        flags.append({
            "severity": "MEDIUM",
            "code": "FREE_PROVIDER_SENDER",
            "detail": (
                f"Sender domain '{sender_domain}' is a consumer email provider. "
                "Legitimate shipping/freight companies always use corporate domains."
            ),
        })
    elif reply_domain in FREE_EMAIL_PROVIDERS:
        flags.append({
            "severity": "MEDIUM",
            "code": "FREE_PROVIDER_REPLY_TO",
            "detail": (
                f"Reply-to is a consumer email address ({reply_domain}). "
                "Payments directed here would reach an attacker-controlled inbox."
            ),
        })

    # Check 3 (MEDIUM): display name mentions a company but email domain is free
    display_name = _get_display_name(sender)
    if display_name and sender_domain in FREE_EMAIL_PROVIDERS:
        flags.append({
            "severity": "MEDIUM",
            "code": "IMPERSONATION",
            "detail": (
                f"Display name claims corporate identity ('{display_name}') "
                f"but is sent from a free provider — likely impersonation."
            ),
        })

    # Check 4 (LOW): urgency / payment-redirect keywords in subject
    matched = [kw for kw in URGENCY_KEYWORDS if kw in subject]
    if matched:
        flags.append({
            "severity": "LOW",
            "code": "URGENCY_KEYWORDS",
            "detail": (
                f"Subject contains urgency/payment-redirect keywords: "
                f"{', '.join(repr(k) for k in matched[:4])}."
            ),
        })

    return flags


def _extract_attachments(msg: Message) -> list:
    attachments = []
    for part in msg.walk():
        content_type = part.get_content_type()
        filename = part.get_filename()
        if filename and content_type in SUPPORTED_ATTACHMENT_TYPES:
            payload = part.get_payload(decode=True)
            if payload:
                attachments.append({
                    "filename": filename,
                    "content_type": content_type,
                    "bytes": payload,
                    "is_image": content_type.startswith("image/"),
                })
    return attachments


def ingest_eml(eml_bytes: bytes) -> dict:
    """
    Parse a .eml file and return header metadata, VEC flags, and attachments.

    Returns dict with keys:
        sender, subject, reply_to, date: str
        vec_flags: list of {"severity": HIGH|MEDIUM|LOW, "code": str, "detail": str}
        vec_risk: "HIGH" | "MEDIUM" | "LOW" | "CLEAN"
        attachments: list of {"filename", "content_type", "bytes", "is_image"}
        error: str | None
    """
    try:
        msg = email.message_from_bytes(eml_bytes, policy=policy.default)
    except Exception as e:
        return {
            "error": str(e),
            "sender": "", "subject": "", "reply_to": "", "date": "",
            "vec_flags": [], "attachments": [], "vec_risk": "UNKNOWN",
        }

    vec_flags = _check_vec_headers(msg)

    if any(f["severity"] == "HIGH" for f in vec_flags):
        vec_risk = "HIGH"
    elif any(f["severity"] == "MEDIUM" for f in vec_flags):
        vec_risk = "MEDIUM"
    elif any(f["severity"] == "LOW" for f in vec_flags):
        vec_risk = "LOW"
    else:
        vec_risk = "CLEAN"

    return {
        "error": None,
        "sender": str(msg.get("from", "")),
        "subject": str(msg.get("subject", "")),
        "reply_to": str(msg.get("reply-to", "")),
        "date": str(msg.get("date", "")),
        "vec_flags": vec_flags,
        "vec_risk": vec_risk,
        "attachments": _extract_attachments(msg),
    }


def imap_fetch_invoices(
    host: str,
    port: int,
    username: str,
    password: str,
    folder: str = "INBOX",
    subject_filter: str = "invoice",
    max_messages: int = 20,
    mark_read: bool = False,
) -> list:
    """
    Connect to an IMAP mailbox over SSL, search for unread messages whose subject
    contains `subject_filter`, and return a list of ingest_eml() result dicts.

    Each result is augmented with a 'uid' key (IMAP message UID as a string).
    On connection or authentication failure, returns a single-element list with
    an 'error' key so callers can display the error without special-casing.
    """
    import imaplib

    results = []
    try:
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(username, password)
            imap.select(folder, readonly=not mark_read)

            status, data = imap.search(None, f'(UNSEEN SUBJECT "{subject_filter}")')
            if status != "OK":
                return [{"error": f"IMAP SEARCH failed: {status}", "uid": None,
                         "sender": "", "subject": "", "reply_to": "", "date": "",
                         "vec_flags": [], "attachments": [], "vec_risk": "UNKNOWN"}]

            uids = data[0].split()
            if not uids:
                return []

            for uid in reversed(uids[-max_messages:]):
                try:
                    status, msg_data = imap.fetch(uid, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue
                    raw_bytes = msg_data[0][1]
                    result = ingest_eml(raw_bytes)
                    result["uid"] = uid.decode()
                    if mark_read and not result.get("error"):
                        imap.store(uid, "+FLAGS", "\\Seen")
                    results.append(result)
                except Exception as e:
                    results.append({
                        "error": str(e), "uid": uid.decode() if uid else None,
                        "sender": "", "subject": "", "reply_to": "", "date": "",
                        "vec_flags": [], "attachments": [], "vec_risk": "UNKNOWN",
                    })

    except imaplib.IMAP4.error as e:
        return [{"error": f"IMAP authentication/connection error: {e}", "uid": None,
                 "sender": "", "subject": "", "reply_to": "", "date": "",
                 "vec_flags": [], "attachments": [], "vec_risk": "UNKNOWN"}]
    except Exception as e:
        return [{"error": str(e), "uid": None,
                 "sender": "", "subject": "", "reply_to": "", "date": "",
                 "vec_flags": [], "attachments": [], "vec_risk": "UNKNOWN"}]

    return results


def make_demo_eml(scenario: str) -> bytes:
    """
    Generate an in-memory .eml for UI demo purposes.

    scenario: "clean" | "vec_attack"
    """
    if scenario == "clean":
        msg = MIMEMultipart()
        msg["From"] = "billing@atlantic-shipping.com"
        msg["To"] = "ap@acme-logistics.com"
        msg["Subject"] = "Invoice INV-2026-999 — Atlantic Explorer — Port of Rotterdam"
        msg["Date"] = "Fri, 20 Jun 2026 10:30:00 +0100"
        body = (
            "Dear Accounts Payable,\n\n"
            "Please find attached invoice INV-2026-999 for cargo discharge at\n"
            "Port of Rotterdam on 2026-06-20.\n\n"
            "Vessel: Atlantic Explorer (MMSI: 244170218)\n"
            "Voyage: VOY-2026-999\n"
            "Amount: EUR 42,500.00\n"
            "IBAN: NL91ABNA0417164300\n\n"
            "Regards,\n"
            "Atlantic Shipping BV — Billing Department\n"
        )
        msg.attach(MIMEText(body, "plain"))
        return msg.as_bytes()

    elif scenario == "free_sender":
        # MEDIUM risk: corporate display name but sent from a Gmail address — impersonation signal.
        msg = MIMEMultipart()
        msg["From"] = '"Hamburg Port Services GmbH" <hps.invoicing@gmail.com>'
        msg["To"] = "ap@acme-logistics.com"
        msg["Subject"] = "Invoice INV-2026-888 — Baltic Carrier — Port of Hamburg"
        msg["Date"] = "Mon, 23 Jun 2026 09:15:00 +0200"
        body = (
            "Dear Finance Team,\n\n"
            "Please find our invoice INV-2026-888 for port handling services\n"
            "rendered to Baltic Carrier at Port of Hamburg on 2026-06-19.\n\n"
            "Vessel: Baltic Carrier (MMSI: 211456200)\n"
            "Voyage: VOY-2026-212\n"
            "Amount: EUR 18,750.00\n"
            "IBAN: DE89370400440532013000\n\n"
            "Kind regards,\n"
            "Hamburg Port Services GmbH — Accounts\n"
        )
        msg.attach(MIMEText(body, "plain"))
        return msg.as_bytes()

    else:  # vec_attack
        msg = MIMEMultipart()
        # Typo domain: atlantlc (l→l swap) mimics atlantic
        msg["From"] = '"Atlantic Shipping BV" <billing@atlantlc-shipping.com>'
        msg["To"] = "ap@acme-logistics.com"
        msg["Reply-To"] = "urgent.payments@gmail.com"
        msg["Subject"] = "URGENT: Updated Banking Details — Invoice INV-2026-999 ACTION REQUIRED"
        msg["Date"] = "Fri, 20 Jun 2026 11:45:00 +0100"
        body = (
            "Dear Finance Team,\n\n"
            "URGENT: Please be advised that our banking details have changed effective immediately.\n\n"
            "Please update your payment records for Invoice INV-2026-999 with our new account:\n\n"
            "  Bank: Quickpay Solutions\n"
            "  IBAN: GB29NWBK60161331926819\n\n"
            "This is time sensitive — please ACTION IMMEDIATELY to avoid payment delays.\n\n"
            "Atlantic Shipping BV\n"
        )
        msg.attach(MIMEText(body, "plain"))
        return msg.as_bytes()


def make_demo_imap_results() -> list:
    """
    Return a simulated IMAP inbox — three messages at different risk levels.

    Identical shape to imap_fetch_invoices() output so the UI can render them
    without any special-casing.

      uid 1001 — CLEAN   : legitimate corporate email, no flags
      uid 1002 — MEDIUM  : corporate display name sent from Gmail (impersonation signal)
      uid 1003 — HIGH    : reply-to hijack + urgency keywords (VEC attack)
    """
    scenarios = [
        ("1001", "clean"),
        ("1002", "free_sender"),
        ("1003", "vec_attack"),
    ]
    results = []
    for uid, scenario in scenarios:
        result = ingest_eml(make_demo_eml(scenario))
        result["uid"] = uid
        results.append(result)
    return results
