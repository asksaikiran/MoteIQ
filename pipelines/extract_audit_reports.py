"""
MoteIQ – Audit Report Extractor  (v4)
======================================
Extracts structured data from Baymont/Wyndham night-audit PDFs into 4 CSVs.
Supports three distinct report formats detected automatically from PDF content:

    FORMAT_2022  – "Balances" guest section + "Detail Transaction Totals List"
    FORMAT_2023  – Shift-level "Transaction Totals Detail For Today" only;
                   no guest list exists in these files → guests always empty
    FORMAT_2024  – "Standard Guest List Report" + "Statistics Report for: Yesterday"
                   + consolidated "Transaction Totals Detail For Today"
    FORMAT_2025  – Same as 2024 but transaction report is "For Yesterday"

Format is detected by scanning section headers found in the PDF text, so
year-in-filename is not required (and misfiled PDFs still parse correctly).

Schema overview (unchanged from v3)
-------------------------------------
    data/processed/
        guest_stays.csv        – one row per in-house guest
        transactions.csv       – one row per posted charge/payment
        daily_revenue.csv      – one row per audit PDF / report date
        occupancy_stats.csv    – operational metrics per audit date

Usage
-----
    python pipelines/extract_audit_reports.py
    python pipelines/extract_audit_reports.py --workers 8
    python pipelines/extract_audit_reports.py --dir data/raw/audit_reports --out data/processed
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pdfplumber

# ─────────────────────────────────────────────────────────────────────────────
# Format constants
# ─────────────────────────────────────────────────────────────────────────────

FORMAT_2022 = "2022"
FORMAT_2023 = "2023"
FORMAT_2024 = "2024"   # "For Today" consolidated txn report
FORMAT_2025 = "2025"   # "For Yesterday" consolidated txn report


# ─────────────────────────────────────────────────────────────────────────────
# Compiled regex patterns  –  2022 format
# ─────────────────────────────────────────────────────────────────────────────

# Balances page guest row
RE_BALANCE = re.compile(
    r"^(\d{3})\s+(.+?)\s+"
    r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(\d+)/(\d+)\s+(\S+)\s+"
    r"\$([\d.]+)\s+(\(?\$?[\d.,]+\)?)\s+(\S+)$"
)

# Old transaction row
RE_TXN_2022 = re.compile(
    r"^([A-Z0-9]+)\s+"
    r"(\d{3}|\d{3}-\d{6})\s+"
    r"(\d{3}-\d{6}|\*\S+)\s+"
    r"(.+?)\s+"
    r"([A-Z]{0,2}\d{5})\s+"
    r"(\d{1,2}:\d{2}\s+[AP]M)\s+"
    r"(\(?\$?[\d,]+\.\d{2}\)?)$"
)

# Monthly Summary row
RE_MONTHLY = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{4})\s+\(\w+\)\s+"
    r"(\d+)\s+(\d+)\s+([\d.]+)%\s+"
    r"\$?([\d,.]+)\s+\$?([\d,.]+)\s+\$?([\d,.]+)\s+\$?([\d,.]+)\s+"
    r"(\(?\$?[\d,.]+\)?)\s+(\(?\$?[\d,.]+\)?)"
)

# ─────────────────────────────────────────────────────────────────────────────
# Compiled regex patterns  –  2023/2024/2025 (new) format
# ─────────────────────────────────────────────────────────────────────────────

# Transaction detail row in the new "Transaction Totals Detail For Today/Yesterday" report.
# Columns (from PDF): Date  BaseType  ChargeCode  Description  UserID  AccountID
#                     AccountType  Name  Time  Company  Room  Amount
#
# Key observations from the real PDFs:
#   - ChargeCode may be "MC", "VI", "EFT", "CA", "DR", "RM", "1000", "1001" …
#   - Description can be multi-word ("ROOM CHARGE", "State Tax 6.75%", "DIRECT BILL *CLC …")
#   - UserID always ends in digits (ma23295, ADAM23295, Mma23295 …)
#   - AccountID is always "81618EE" + digits  OR  a plain 9-digit AR number
#   - AccountType is "Guest" or "AR"
#   - Name may contain spaces (multi-word); for AR rows the Name column is absent
#   - Company is optional (e.g. "Wyndham Rewards Member Rate", "CLC - CLC GENERIC")
#   - Room is 2-3 digits, optional
#   - Amount ends the line: plain decimal, possibly negative

# We parse the new format line-by-line using a looser approach because the
# column layout is too variable for a single tight regex.  The strategy:
#   1. Identify lines that end with a signed/unsigned decimal amount.
#   2. Extract UserID (ends in digits), AccountID, AccountType, Name, Room, Amount.

RE_NEW_TXN = re.compile(
    r"^(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\s+)?"
    r"(\S+)\s+"                             # Base Type  (Cash/CreditCard/RoomCharge/Taxes/DirectBill)
    r"(\S+)\s+"                             # Charge Code (MC, VI, EFT, RM, DR, 1000, 1001 …)
    r"(.+?)\s+"                             # Description (greedy-lazy, stops at UserID)
    r"([A-Za-z]{0,5}\d{5})\s+"             # UserID
    r"(81618EE\d+|\d{9,12}H?)\s+"          # AccountID
    r"(Guest|AR)\s+"                        # AccountType
    r"(.+?)\s+"                             # Name  (last field before optional Company/Room/Amount)
    r"(\d{1,2}:\d{2}\s+[AP]M)\s+"          # Time
    r"(?:(.+?)\s+)?"                        # Company (optional, non-greedy)
    r"(\d{2,3})?\s*"                        # Room (optional)
    r"(-?[\d,]+\.\d{2})$"                  # Amount
)

# Simpler fallback: just grab AccountType=Guest rows and pull Name + Room + Amount
# from a less strict match when the full regex misses due to line-wrapping.
RE_NEW_TXN_SIMPLE = re.compile(
    r"([A-Za-z]{0,5}\d{5})\s+"     # UserID
    r"(81618EE\d+)\s+"              # AccountID
    r"(Guest|AR)\s+"                # AccountType
    r"(.+?)\s+"                     # Name
    r"(\d{1,2}:\d{2}\s+[AP]M)"     # Time
)

RE_AMOUNT_END = re.compile(r"(-?[\d,]+\.\d{2})$")
RE_ROOM_BEFORE_AMOUNT = re.compile(r"\b(\d{2,3})\s+(-?[\d,]+\.\d{2})$")

# Standard Guest List row (2024/2025)
# Header: Guest Name | Confirmation Number | Status | Secondary Status |
#         Arrival Date | Departure Date | GTD Type | Rate Plan | Rate |
#         Company | Room Type | Room Number | Adult/Child | Balance | …
#
# pdfplumber returns the page as a single text blob; columns are separated by
# varying whitespace.  We anchor on the Confirmation Number (81618EE + digits)
# which is unique and always present.
RE_GUEST_NEW = re.compile(
    r"^(.+?)\s+"                                   # Guest Name (everything up to conf#)
    r"(81618EE\d+)\s+"                             # Confirmation Number
    r"(Confirmed|Cancelled|Waitlisted)\s+"          # Status
    r"(In House|Checked Out|None|No Show)\s+"       # Secondary Status
    r"([A-Za-z]{3}\s+\d{1,2},\s+\d{4})\s+"        # Arrival Date  (Dec 3, 2024)
    r"([A-Za-z]{3}\s+\d{1,2},\s+\d{4})\s+"        # Departure Date
    r"(\S+)\s+"                                     # GTD Type
    r"(\S+)\s+"                                     # Rate Plan
    r"([\d.]+)\s+"                                  # Rate
    r"(?:(.+?)\s+)?"                                # Company (optional)
    r"([A-Z0-9]+\d?)\s+"                            # Room Type
    r"(\d{2,3})\s+"                                 # Room Number
    r"(\d+),(\d+)\s+"                               # Adult,Child
    r"(-?[\d.]+)?"                                  # Balance (optional)
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def clean_money(s: str) -> str:
    """($1,203.25) → -1203.25  |  $2,994.23 → 2994.23"""
    s = s.strip().replace("$", "").replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        return "-" + s[1:-1]
    return s


def date_from_filename(p: Path) -> str:
    """09-22-2022.pdf → 9/22/2022"""
    parts = p.stem.split("-")
    if len(parts) == 3:
        try:
            return f"{int(parts[0])}/{int(parts[1])}/{parts[2]}"
        except ValueError:
            pass
    return p.stem


def stay_length(arrival: str, departure: str) -> str:
    """Return integer nights between two date strings, or '' on error."""
    for fmt in ("%m/%d/%Y", "%b %d, %Y"):
        try:
            delta = (datetime.strptime(departure, fmt)
                     - datetime.strptime(arrival, fmt))
            return str(delta.days)
        except Exception:
            continue
    return ""


def make_guest_id(first: str, last: str) -> str:
    normalised = f"{last.upper().strip()},{first.upper().strip()}"
    digest = hashlib.sha256(normalised.encode()).hexdigest()
    return f"gst_{digest[:8]}"


def split_name(full: str) -> tuple[str, str]:
    """'SMITH, JOHN' or 'John Smith' → (first, last)"""
    full = full.strip()
    if "," in full:
        last, _, first = full.partition(",")
        return first.strip().title(), last.strip().title()
    parts = full.split()
    if len(parts) >= 2:
        return parts[0].strip().title(), parts[-1].strip().title()
    return "", full.title()


def detect_format(text: str) -> str:
    """
    Detect which report format this PDF uses by scanning for known section headers.

    The 2022 format also contains "Statistics Report for" so we must check for
    2022-unique markers FIRST before testing the new-format headers.

    Priority:
      FORMAT_2022 : contains "Detail Transaction Totals List" OR a "Balances"
                    section header — these only exist in the old Wyndham format.
      FORMAT_2025 : has "Transaction Totals Detail For Yesterday"
      FORMAT_2024 : has "Standard Guest List Report"
                    (we no longer use "Statistics Report for" alone — it appears
                    in both old and new formats)
      FORMAT_2023 : has "Transaction Totals Detail For Today" (shift-level only,
                    no guest list, no statistics report in new layout)
      FORMAT_2022 : final fallback
    """
    # 2022-specific anchors — these strings do NOT appear in new-format PDFs
    if "Detail Transaction Totals List" in text:
        return FORMAT_2022
    # "Balances\n" as a standalone section header (old format only)
    if re.search(r"^\s*Balances\s*$", text, re.MULTILINE):
        return FORMAT_2022

    # New formats
    if "Transaction Totals Detail For Yesterday" in text:
        return FORMAT_2025
    if "Standard Guest List Report" in text:
        return FORMAT_2024
    if "Transaction Totals Detail For Today" in text:
        return FORMAT_2023

    return FORMAT_2022


# ─────────────────────────────────────────────────────────────────────────────
# ── 2022 parsers (unchanged from v3) ─────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def parse_guest_stays_2022(lines: list[str], report_date: str, source: dict) -> list[dict]:
    rows, active = [], False
    for line in lines:
        if line.strip() == "Balances":
            active = True
            continue
        if not active:
            continue
        m = RE_BALANCE.match(line.strip())
        if m:
            room, name, arrival, dep, adults, children, _rp, _rate, _bal, gtd = m.groups()
            first, last = split_name(name.strip())
            gid = make_guest_id(first, last)
            rows.append({
                **source,
                "guest_id":       gid,
                "report_date":    report_date,
                "room":           room,
                "first_name":     first,
                "last_name":      last,
                "arrival":        arrival,
                "departure":      dep,
                "stay_length":    stay_length(arrival, dep),
                "adults":         adults,
                "children":       children,
                "payment_method": gtd,
            })
        if re.match(r"^23295", line):
            active = False
    return rows


def parse_transactions_2022(lines: list[str], report_date: str, source: dict) -> list[dict]:
    rows = []
    last_start = -1
    for i, line in enumerate(lines):
        if "Detail Transaction Totals List" in line and "Page 1 of" in line:
            last_start = i
    if last_start == -1:
        return rows

    for line in lines[last_start:]:
        m = RE_TXN_2022.match(line.strip())
        if m:
            code, room_or_acct, _acct, name_comment, _user, _time, amount = m.groups()
            room = room_or_acct if re.match(r"^\d{3}$", room_or_acct) else ""
            raw_name = name_comment.strip()
            m2 = re.match(r"^([A-Z]+(?:[A-Z]+)?,\s+[A-Z]+(?:[A-Z]+)?)", raw_name)
            clean_name = m2.group(1) if m2 else raw_name
            first, last = split_name(clean_name)
            gid = make_guest_id(first, last) if (first or last) else ""
            rows.append({
                **source,
                "guest_id":    gid,
                "report_date": report_date,
                "room":        room,
                "txn_code":    code,
                "amount":      clean_money(amount),
            })
    return rows


def parse_daily_revenue_2022(lines: list[str], report_date: str, source: dict) -> list[dict]:
    candidate_rows: list[dict] = []
    active = False
    for line in lines:
        if "Monthly Summary for" in line:
            active = True
            continue
        if not active:
            continue
        m = RE_MONTHLY.match(line.strip())
        if m:
            date, rooms, _avail, occ_pct, adr, _fb, room_chg, taxes, other, total = m.groups()
            candidate_rows.append({
                **source,
                "report_date":     report_date,
                "date_in_summary": date,
                "rooms_rented":    rooms,
                "occupancy_pct":   occ_pct,
                "adr":             adr,
                "room_charges":    clean_money(room_chg),
                "taxes":           clean_money(taxes),
                "other_revenue":   clean_money(other),
                "total_revenue":   clean_money(total),
            })
        if line.strip().startswith("Totals"):
            active = False

    if not candidate_rows:
        return []
    for row in candidate_rows:
        if row["date_in_summary"] == report_date:
            row.pop("date_in_summary")
            return [row]
    row = candidate_rows[-1]
    row.pop("date_in_summary")
    return [row]


_STAT_LABELS: dict[str, str] = {
    "Total Rooms Occupied":             "rooms_occupied",
    "Out of Order Rooms":               "ooo_rooms",
    "Total Rooms Left Vacant":          "vacant_rooms",
    "Total Number of Guests":           "total_guests",
    "Total Walk Ins":                   "walk_ins",
    "Total No Show":                    "no_shows",
    "Total Reservation Cancellations":  "cancellations",
}


def parse_occupancy_stats_2022(lines: list[str], report_date: str, source: dict) -> list[dict]:
    stat_buf, active = [], False
    for line in lines:
        if "Statistics Report for" in line:
            active = True
            continue
        if active:
            stat_buf.append(line)
            if "Total Reservation Cancellations" in line:
                active = False

    occ: dict[str, str] = {}
    for i, line in enumerate(stat_buf):
        for label, key in _STAT_LABELS.items():
            if label in line and key not in occ:
                nums = re.findall(r"[\d,]+\.?\d*", line)
                if not nums and i > 0:
                    nums = re.findall(r"[\d,]+\.?\d*", stat_buf[i - 1])
                if nums:
                    occ[key] = nums[0].replace(",", "")

    return [{
        **source,
        "report_date": report_date,
        **{k: occ.get(k, "0") for k in _STAT_LABELS.values()},
    }]


# ─────────────────────────────────────────────────────────────────────────────
# ── 2024 / 2025 parsers (new format) ─────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _parse_new_txn_block(lines: list[str], report_date: str, source: dict,
                         header_phrase: str) -> list[dict]:
    """
    Parse the consolidated transaction detail block identified by `header_phrase`.

    Strategy:
      1. Find the LAST "Page 1 of N" occurrence of the header to skip per-shift
         duplicates (2023 has multiple shift reports).
      2. Reconstruct "logical lines" by joining pdfplumber line-wraps: a new
         logical line starts when we see an AccountID (81618EE...).
      3. For each Guest row: extract charge code, name, room, amount.
         - Amount is always the last decimal on the line.
         - Room is the last 2-3 digit token before the amount (post "Guest").
         - Name is between "Guest" and a Time field (or end of non-amount text).
    """
    rows = []

    last_start = -1
    for i, line in enumerate(lines):
        if header_phrase in line and "Page 1 of" in line:
            last_start = i
    if last_start == -1:
        return rows

    # Reconstruct logical lines — new row starts at AccountID anchor
    logical_lines: list[str] = []
    buf = ""
    for line in lines[last_start:]:
        stripped = line.strip()
        if not stripped:
            if buf:
                logical_lines.append(buf)
                buf = ""
            continue
        has_acct = bool(re.search(r"81618EE\d+|\d{9,12}H", stripped))
        if has_acct:
            if buf:
                logical_lines.append(buf)
            buf = stripped
        elif buf:
            buf = buf + " " + stripped
        else:
            logical_lines.append(stripped)
    if buf:
        logical_lines.append(buf)

    for logical in logical_lines:
        acct_m = re.search(r"(81618EE\d+)", logical)
        if not acct_m:
            continue

        # Skip AR offset rows — AccountType token right after AccountID is "AR"
        after_acct = logical[acct_m.end():].lstrip()
        if after_acct.startswith("AR"):
            continue

        if "Guest" not in logical:
            continue

        # Amount: last decimal on line
        amt_m = RE_AMOUNT_END.search(logical)
        if not amt_m:
            continue
        amount_raw = amt_m.group(1).replace(",", "")

        # Charge code: token after base-type keyword
        code_m = re.search(
            r"(?:Cash|CreditCard|RoomCharge|Taxes|DirectBill)\s+(\S+)", logical
        )
        txn_code = code_m.group(1) if code_m else ""

        # Room: last 2-3 digit number in the region after "Guest" and before amount
        guest_pos = logical.find("Guest")
        post_guest = logical[guest_pos:amt_m.start()]
        room_candidates = re.findall(r"(\d{2,3})", post_guest)
        room = room_candidates[-1] if room_candidates else ""

        # Name: text between "Guest" token and Time field (or amount region)
        name_section = logical[guest_pos + len("Guest"):].strip()
        time_m = re.search(r"\d{1,2}:\d{2}\s+[AP]M", name_section)
        if time_m:
            raw_name = name_section[:time_m.start()].strip()
        else:
            raw_name = re.sub(r"\s+\d{2,3}\s+-?[\d,]+\.\d{2}$", "", name_section).strip()
            raw_name = re.sub(r"\s+-?[\d,]+\.\d{2}$", "", raw_name).strip()

        # Strip trailing company / rate plan noise
        raw_name = re.sub(
            r"\s+(Wyndham|CLC\s+-|Hotel Engine|SU).*$", "", raw_name,
            flags=re.IGNORECASE
        ).strip()
        # Strip stray userID that leaked in
        raw_name = re.sub(r"\s+[A-Za-z]{0,6}\d{4,6}$", "", raw_name).strip()

        if not raw_name:
            continue

        first, last = split_name(raw_name)
        gid = make_guest_id(first, last) if (first or last) else ""

        rows.append({
            **source,
            "guest_id":    gid,
            "report_date": report_date,
            "room":        room,
            "txn_code":    txn_code,
            "amount":      amount_raw,
        })

    return rows


def parse_transactions_new(lines: list[str], report_date: str, source: dict,
                            fmt: str) -> list[dict]:
    """
    Route to the right header phrase based on detected format.

    2023: "Transaction Totals Detail For Today" — multiple shift blocks; we use
          the LAST "Page 1 of" occurrence (same logic as 2022).
    2024: Same header ("For Today") but one consolidated block.
    2025: "Transaction Totals Detail For Yesterday".
    """
    if fmt == FORMAT_2025:
        phrase = "Transaction Totals Detail For Yesterday"
    else:
        phrase = "Transaction Totals Detail For Today"

    return _parse_new_txn_block(lines, report_date, source, phrase)


def parse_guest_stays_new(lines: list[str], report_date: str, source: dict) -> list[dict]:
    """
    Standard Guest List Report (2024/2025) → one row per in-house guest.

    The report header is: "Standard Guest List Report: Today (DD Mon YYYY)"
    Each guest row has columns separated by variable whitespace.  We rely on
    the Confirmation Number (81618EE…) as a reliable anchor and parse outward.

    Note: The guest list shows guests who are currently *in house* at the time
    the night audit was run (typically early morning), which corresponds to the
    report_date guests.  Some long-stay guests will appear on multiple days —
    the guest_id (name hash) deduplicates them across reports.
    """
    rows = []
    active = False

    for line in lines:
        if "Standard Guest List Report" in line:
            active = True
            continue
        if not active:
            continue
        # Stop at the report criteria footer
        if line.strip().startswith("Report Criteria"):
            active = False
            continue

        stripped = line.strip()
        if not stripped:
            continue

        # Look for a confirmation number — anchor for this row
        conf_m = re.search(r"(81618EE\d+)", stripped)
        if not conf_m:
            continue

        acct_id = conf_m.group(1)
        # Everything before the account ID is the guest name
        name_part = stripped[:conf_m.start()].strip()
        first, last = split_name(name_part)
        gid = make_guest_id(first, last)

        # After the account ID: Status, Secondary Status, Arrival, Departure …
        remainder = stripped[conf_m.end():].strip()

        # Status tokens
        status_m = re.match(
            r"(Confirmed|Cancelled|Waitlisted)\s+(In House|Checked Out|None|No Show)\s+",
            remainder
        )
        if not status_m:
            continue
        remainder = remainder[status_m.end():]

        # Dates: "Dec 3, 2024 Dec 4, 2024" or "Nov 21, 2024 Dec 6, 2024"
        date_m = re.match(
            r"([A-Za-z]{3}\s+\d{1,2},\s+\d{4})\s+([A-Za-z]{3}\s+\d{1,2},\s+\d{4})\s+",
            remainder
        )
        if not date_m:
            continue
        arrival_raw  = date_m.group(1)
        departure_raw = date_m.group(2)
        remainder = remainder[date_m.end():]

        # GTD type: next non-space token
        gtd_m = re.match(r"(\S+)\s+", remainder)
        gtd = gtd_m.group(1) if gtd_m else ""

        # Rate plan: next token
        if gtd_m:
            remainder = remainder[gtd_m.end():]
        rp_m = re.match(r"(\S+)\s+", remainder)
        rate_plan = rp_m.group(1) if rp_m else ""

        # Adult/Child: "2,0" or "1,0"
        ac_m = re.search(r"(\d+),(\d+)", remainder)
        adults   = ac_m.group(1) if ac_m else ""
        children = ac_m.group(2) if ac_m else ""

        # Room number: last 2-3 digit number before the Adult/Child field
        room_candidates = re.findall(r"\b(\d{2,3})\b", remainder)
        # The room number comes before the adult/child pair
        room = ""
        if ac_m and room_candidates:
            # Find digits before the ac_m position
            pre_ac = remainder[:ac_m.start()]
            room_pre = re.findall(r"\b(\d{2,3})\b", pre_ac)
            if room_pre:
                room = room_pre[-1]

        # Normalize dates to M/D/YYYY
        def norm_date(d: str) -> str:
            try:
                return datetime.strptime(d, "%b %d, %Y").strftime("%-m/%-d/%Y")
            except Exception:
                try:
                    return datetime.strptime(d, "%b %d, %Y").strftime("%m/%d/%Y").lstrip("0")
                except Exception:
                    return d

        arrival   = norm_date(arrival_raw)
        departure = norm_date(departure_raw)

        rows.append({
            **source,
            "guest_id":       gid,
            "report_date":    report_date,
            "room":           room,
            "first_name":     first,
            "last_name":      last,
            "arrival":        arrival,
            "departure":      departure,
            "stay_length":    stay_length(arrival_raw, departure_raw),
            "adults":         adults,
            "children":       children,
            "payment_method": gtd,
        })

    return rows


# New-format Statistics Report field mapping.
# The new report uses slightly different labels than the 2022 version.
_STAT_LABELS_NEW: dict[str, str] = {
    "Rooms Occupied":                   "rooms_occupied",   # "Rooms Occupied" row (Today col)
    "OOO Rooms":                        "ooo_rooms",
    "Rooms Left Vacant":                "vacant_rooms",
    "Total Number of Guest":            "total_guests",
    "Total Walkin":                     "walk_ins",
    "Total No show":                    "no_shows",
    "Total Reservation Cancellation":   "cancellations",
}


def parse_occupancy_stats_new(lines: list[str], report_date: str, source: dict) -> list[dict]:
    """
    Statistics Report for: Yesterday (new format).

    The new report is a wide table with columns: Today | MTD | YTD | Today | MTD | YTD.
    We only want the first "Today" value (current year, single day).
    Each label is on the same line as the values, separated by spaces.
    """
    stat_buf, active = [], False
    for line in lines:
        if "Statistics Report for" in line:
            active = True
            continue
        if active:
            stat_buf.append(line)
            if "Total Direct Bill Transfers" in line or "Report Criteria" in line:
                active = False

    occ: dict[str, str] = {}
    for line in stat_buf:
        for label, key in _STAT_LABELS_NEW.items():
            if label in line and key not in occ:
                # First numeric value on the line is the "Today" column
                nums = re.findall(r"[\d,]+", line.split(label)[-1])
                if nums:
                    occ[key] = nums[0].replace(",", "")

    # Revenue fields from the Statistics Report (new format has them too)
    revenue: dict[str, str] = {}
    for line in stat_buf:
        if "Total Room Revenue" in line and "revenue" not in revenue:
            nums = re.findall(r"[\d,]+\.?\d*", line.split("Total Room Revenue")[-1])
            if nums:
                revenue["room_charges"] = nums[0].replace(",", "")
        if "Total Tax Revenue" in line:
            nums = re.findall(r"[\d,]+\.?\d*", line.split("Total Tax Revenue")[-1])
            if nums:
                revenue["taxes"] = nums[0].replace(",", "")
        if "Total Hotel Revenue" in line:
            nums = re.findall(r"[\d,]+\.?\d*", line.split("Total Hotel Revenue")[-1])
            if nums:
                revenue["total_revenue"] = nums[0].replace(",", "")
        if "Total Other Revenue" in line:
            nums = re.findall(r"[\d,]+\.?\d*", line.split("Total Other Revenue")[-1])
            if nums:
                revenue["other_revenue"] = nums[0].replace(",", "")

    return [{
        **source,
        "report_date": report_date,
        **{k: occ.get(k, "0") for k in _STAT_LABELS_NEW.values()},
    }]


def parse_daily_revenue_new(lines: list[str], report_date: str, source: dict) -> list[dict]:
    """
    Extract daily revenue from the Statistics Report (new format).

    The new format has no Monthly Summary table. Instead revenue lives in the
    Statistics Report under the Revenue section.  We pull the "Today" column values.
    ADR = ADR With Comps (Today), occupancy = Rooms Occupied / Rentable Rooms.
    """
    stat_buf, active = [], False
    for line in lines:
        if "Statistics Report for" in line:
            active = True
            continue
        if active:
            stat_buf.append(line)
            if "Report Criteria" in line:
                active = False

    if not stat_buf:
        return []

    def first_num(label: str) -> str:
        for line in stat_buf:
            if label in line:
                after = line.split(label)[-1]
                nums = re.findall(r"-?[\d,]+\.?\d*", after)
                if nums:
                    return nums[0].replace(",", "")
        return ""

    rooms_occupied = first_num("Rooms Occupied")
    rentable       = first_num("Rentable Rooms")
    try:
        occ_pct = f"{100 * int(rooms_occupied) / int(rentable):.2f}" if rentable and int(rentable) else ""
    except Exception:
        occ_pct = ""

    rooms_rented = first_num("Revenue Rooms")
    adr          = first_num("ADR With Comps")
    room_charges = first_num("Total Room Revenue")
    taxes        = first_num("Total Tax Revenue")
    other        = first_num("Total Other Revenue")
    total        = first_num("Total Hotel Revenue")

    return [{
        **source,
        "report_date":   report_date,
        "rooms_rented":  rooms_rented,
        "occupancy_pct": occ_pct,
        "adr":           adr,
        "room_charges":  room_charges,
        "taxes":         taxes,
        "other_revenue": other,
        "total_revenue": total,
    }]


# ─────────────────────────────────────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────────────────────────────────────

def process_pdf(pdf_path: Path) -> dict:
    result = {
        "path": str(pdf_path),
        "format": "",
        "guests": [], "transactions": [], "revenue": [], "occupancy": [],
        "error": None,
    }
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)

        lines = text.split("\n")
        report_date = date_from_filename(pdf_path)
        source = {"report_year": pdf_path.parent.name}
        fmt = detect_format(text)
        result["format"] = fmt

        if fmt == FORMAT_2022:
            result["guests"]       = parse_guest_stays_2022(lines, report_date, source)
            result["transactions"] = parse_transactions_2022(lines, report_date, source)
            result["revenue"]      = parse_daily_revenue_2022(lines, report_date, source)
            result["occupancy"]    = parse_occupancy_stats_2022(lines, report_date, source)

        elif fmt == FORMAT_2023:
            # 2023 PDFs have no guest list; transactions only from the last shift report
            result["guests"]       = []
            result["transactions"] = parse_transactions_new(lines, report_date, source, fmt)
            result["revenue"]      = []   # no Monthly Summary, no Statistics Report
            result["occupancy"]    = []

        elif fmt in (FORMAT_2024, FORMAT_2025):
            result["guests"]       = parse_guest_stays_new(lines, report_date, source)
            result["transactions"] = parse_transactions_new(lines, report_date, source, fmt)
            result["revenue"]      = parse_daily_revenue_new(lines, report_date, source)
            result["occupancy"]    = parse_occupancy_stats_new(lines, report_date, source)

    except Exception as exc:
        result["error"] = str(exc)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# CSV schemas (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

GUEST_FIELDS = [
    "report_year", "report_date",
    "guest_id", "room",
    "first_name", "last_name",
    "arrival", "departure", "stay_length",
    "adults", "children",
    "payment_method",
]

TXN_FIELDS = [
    "report_year", "report_date",
    "guest_id", "room",
    "txn_code", "amount",
]

REVENUE_FIELDS = [
    "report_year", "report_date",
    "rooms_rented", "occupancy_pct", "adr",
    "room_charges", "taxes", "other_revenue", "total_revenue",
]

OCCUPANCY_FIELDS = [
    "report_year", "report_date",
    "rooms_occupied", "ooo_rooms", "vacant_rooms",
    "total_guests", "walk_ins", "no_shows", "cancellations",
]


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MoteIQ audit report extractor v4")
    parser.add_argument("--dir",     default="data/raw/audit_reports")
    parser.add_argument("--out",     default="data/processed")
    parser.add_argument("--workers", type=int,
                        default=min(8, os.cpu_count() or 2))
    args = parser.parse_args()

    raw_dir = Path(args.dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(raw_dir.rglob("*.pdf"))
    if not pdf_files:
        print(f"No PDFs found under {raw_dir}")
        sys.exit(1)

    print(f"Found {len(pdf_files)} PDFs  |  workers={args.workers}\n")

    output_files = {
        "g": out_dir / "guest_stays.csv",
        "t": out_dir / "transactions.csv",
        "r": out_dir / "daily_revenue.csv",
        "o": out_dir / "occupancy_stats.csv",
    }
    field_map = {
        "g": GUEST_FIELDS,
        "t": TXN_FIELDS,
        "r": REVENUE_FIELDS,
        "o": OCCUPANCY_FIELDS,
    }

    with (
        open(output_files["g"], "w", newline="", encoding="utf-8") as fg,
        open(output_files["t"], "w", newline="", encoding="utf-8") as ft,
        open(output_files["r"], "w", newline="", encoding="utf-8") as fr,
        open(output_files["o"], "w", newline="", encoding="utf-8") as fo,
    ):
        file_handles = {"g": fg, "t": ft, "r": fr, "o": fo}
        writers = {
            k: csv.DictWriter(fh, fieldnames=field_map[k], extrasaction="ignore")
            for k, fh in file_handles.items()
        }
        for w in writers.values():
            w.writeheader()

        totals = {
            "guests": 0, "transactions": 0, "revenue": 0, "occupancy": 0,
            "errors": 0,
            "fmt_2022": 0, "fmt_2023": 0, "fmt_2024": 0, "fmt_2025": 0,
        }
        done = 0

        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process_pdf, p): p for p in pdf_files}
            for future in as_completed(futures):
                res = future.result()
                done += 1

                if res["error"]:
                    print(
                        f"  [ERROR {done:>4}/{len(pdf_files)}] "
                        f"{Path(res['path']).name}: {res['error']}"
                    )
                    totals["errors"] += 1
                    continue

                writers["g"].writerows(res["guests"])
                writers["t"].writerows(res["transactions"])
                writers["r"].writerows(res["revenue"])
                writers["o"].writerows(res["occupancy"])

                totals["guests"]       += len(res["guests"])
                totals["transactions"] += len(res["transactions"])
                totals["revenue"]      += len(res["revenue"])
                totals["occupancy"]    += len(res["occupancy"])
                fmt_key = f"fmt_{res['format']}"
                if fmt_key in totals:
                    totals[fmt_key] += 1

                print(
                    f"  [{done:>4}/{len(pdf_files)}] "
                    f"{Path(res['path']).name:<28} [{res['format']}]"
                    f"  guests={len(res['guests']):>3}"
                    f"  txns={len(res['transactions']):>4}"
                )

    print("\n-- Summary " + "-" * 55)
    print(f"  PDFs processed   : {len(pdf_files) - totals['errors']:>6} / {len(pdf_files)}")
    print(f"  Errors           : {totals['errors']:>6}")
    print(f"  Format 2022      : {totals['fmt_2022']:>6}")
    print(f"  Format 2023      : {totals['fmt_2023']:>6}")
    print(f"  Format 2024      : {totals['fmt_2024']:>6}")
    print(f"  Format 2025      : {totals['fmt_2025']:>6}")
    print(f"  Guest rows       : {totals['guests']:>6}")
    print(f"  Transaction rows : {totals['transactions']:>6}")
    print(f"  Revenue rows     : {totals['revenue']:>6}")
    print(f"  Occupancy rows   : {totals['occupancy']:>6}")
    print(f"\n  Output → {out_dir}/")
    for fname in output_files.values():
        if fname.exists():
            size = fname.stat().st_size
            print(f"    {fname.name:<32} {size:>10,} bytes")


if __name__ == "__main__":
    main()
