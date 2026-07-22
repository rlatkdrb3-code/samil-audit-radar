#!/usr/bin/env python3
"""Reconcile OpenDART audit-market CSV rows against source filings.

The OpenDART periodic-report endpoints expose audit fees as free-form text.
When an endpoint is blank or a parsed value is implausible, this script reads
the source filing's audit table from document.xml and records that provenance.
It can also fill missing revenue from fnlttSinglAcntAll.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


API_BASE = "https://opendart.fss.or.kr/api"
BIG4_GROUPS = ("samil_pwc", "samjong_kpmg", "ey_hanyoung", "deloitte_anjin")
BASE_FIELDS = [
    "year",
    "report_code",
    "corp_code",
    "corp_name",
    "stock_code",
    "corp_cls",
    "auditor_raw",
    "auditor_group",
    "audit_contract_fee",
    "audit_actual_fee",
    "audit_contract_hours",
    "audit_actual_hours",
    "audit_opinion",
    "revenue",
    "revenue_account",
    "revenue_fs_div",
    "source_rcept_no",
    "warnings",
]
PROVENANCE_FIELDS = [
    "auditor_source",
    "fee_source",
    "revenue_source",
    "validation_status",
]
UNIT_MULTIPLIERS = {
    "백만원": 1_000_000,
    "천만원": 10_000_000,
    "억원": 100_000_000,
    "만원": 10_000,
    "천원": 1_000,
    "원": 1,
}
UNIT_ORDER = tuple(UNIT_MULTIPLIERS)
FOREIGN_CURRENCY_RE = re.compile(r"\b(?:USD|CNY|JPY|EUR|HKD|SGD|GBP)\b", re.I)
FISCAL_YEAR_RE_TEMPLATE = r"사업보고서\s*\(\s*{year}\.(?:0?[1-9]|1[0-2])\s*\)"


class DartError(RuntimeError):
    pass


class DartClient:
    def __init__(self, api_key: str, timeout: int = 60, retries: int = 4) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries

    def _request(self, endpoint: str, params: dict[str, Any]) -> bytes:
        query = urllib.parse.urlencode({"crtfc_key": self.api_key, **params})
        request = urllib.request.Request(
            f"{API_BASE}/{endpoint}?{query}",
            headers={"User-Agent": "audit-market-lens/1.0"},
        )
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt + 1 == self.retries:
                    raise DartError(f"{endpoint} request failed: {type(exc).__name__}") from exc
                time.sleep(0.6 * (2**attempt))
        raise DartError(f"{endpoint} request failed")

    def get_json(self, endpoint: str, **params: Any) -> dict[str, Any]:
        raw = self._request(endpoint, params)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DartError(f"{endpoint} returned invalid JSON") from exc
        status = data.get("status")
        if status not in (None, "000", "013"):
            raise DartError(f"{endpoint} returned {status}: {data.get('message', 'unknown error')}")
        return data

    def get_document(self, receipt_no: str) -> str:
        raw = self._request("document.xml", {"rcept_no": receipt_no})
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise DartError("document.xml returned an invalid archive") from exc
        preferred = f"{receipt_no}.xml"
        names = archive.namelist()
        if preferred in names:
            name = preferred
        else:
            main_files = [n for n in names if n.endswith(".xml") and "_" not in Path(n).stem]
            if not main_files:
                raise DartError("document.xml has no main filing XML")
            name = max(main_files, key=lambda n: archive.getinfo(n).file_size)
        return archive.read(name).decode("utf-8", errors="ignore")


def clean_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def parse_number(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def format_number(value: float | int | None) -> str:
    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def normalize_auditor(value: str) -> str:
    compact = re.sub(r"[\s()㈜주식회사.,·ㆍ_-]+", "", str(value or "")).lower()
    if any(token in compact for token in ("삼일", "pwc", "pricewaterhouse", "프라이스워터")):
        return "samil_pwc"
    if any(token in compact for token in ("삼정", "kpmg")):
        return "samjong_kpmg"
    if any(token in compact for token in ("한영", "ernst", "young")) or compact == "ey":
        return "ey_hanyoung"
    if any(token in compact for token in ("안진", "deloitte", "딜로이트")):
        return "deloitte_anjin"
    return "other_or_unknown"


def append_warning(row: dict[str, str], warning: str) -> None:
    warnings = [part for part in re.split(r"[;,]", row.get("warnings", "")) if part]
    if warning not in warnings:
        warnings.append(warning)
    row["warnings"] = ";".join(warnings)


def quarter_ranges(start: dt.date, end: dt.date) -> Iterable[tuple[dt.date, dt.date]]:
    cursor = start
    while cursor <= end:
        quarter = (cursor.month - 1) // 3
        end_month = quarter * 3 + 3
        if end_month == 12:
            quarter_end = dt.date(cursor.year, 12, 31)
        else:
            quarter_end = dt.date(cursor.year, end_month + 1, 1) - dt.timedelta(days=1)
        yield cursor, min(quarter_end, end)
        cursor = quarter_end + dt.timedelta(days=1)


def fetch_latest_filings(client: DartClient, fiscal_year: int) -> dict[str, dict[str, str]]:
    today = dt.date.today()
    start = dt.date(fiscal_year, 1, 1)
    pattern = re.compile(FISCAL_YEAR_RE_TEMPLATE.format(year=fiscal_year))
    latest: dict[str, dict[str, str]] = {}
    for begin, end in quarter_ranges(start, today):
        params = {
            "bgn_de": begin.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "pblntf_detail_ty": "A001",
            "last_reprt_at": "Y",
            "page_count": 100,
            "page_no": 1,
        }
        first = client.get_json("list.json", **params)
        pages = int(first.get("total_page") or 0)
        page_payloads = [first]
        for page in range(2, pages + 1):
            page_payloads.append(client.get_json("list.json", **{**params, "page_no": page}))
        for payload in page_payloads:
            for filing in payload.get("list", []):
                if not pattern.search(filing.get("report_nm", "")):
                    continue
                corp_code = filing.get("corp_code", "")
                if not corp_code:
                    continue
                key = (filing.get("rcept_dt", ""), filing.get("rcept_no", ""))
                previous = latest.get(corp_code)
                previous_key = (
                    previous.get("rcept_dt", "") if previous else "",
                    previous.get("rcept_no", "") if previous else "",
                )
                if previous is None or key > previous_key:
                    latest[corp_code] = {k: str(v or "") for k, v in filing.items()}
    return latest


def default_row(year: int, filing: dict[str, str]) -> dict[str, str]:
    row = {field: "" for field in BASE_FIELDS + PROVENANCE_FIELDS}
    row.update(
        {
            "year": str(year),
            "report_code": "11011",
            "corp_code": filing.get("corp_code", ""),
            "corp_name": filing.get("corp_name", ""),
            "stock_code": filing.get("stock_code", ""),
            "corp_cls": filing.get("corp_cls", ""),
            "source_rcept_no": filing.get("rcept_no", ""),
            "validation_status": "pending",
        }
    )
    return row


def merge_universe(
    rows: list[dict[str, str]],
    filings_by_year: dict[int, dict[str, dict[str, str]]],
) -> tuple[list[dict[str, str]], set[tuple[str, str]]]:
    by_key = {(row.get("year", ""), row.get("corp_code", "")): row for row in rows}
    changed: set[tuple[str, str]] = set()
    for year, filings in filings_by_year.items():
        for corp_code, filing in filings.items():
            key = (str(year), corp_code)
            row = by_key.get(key)
            if row is None:
                row = default_row(year, filing)
                rows.append(row)
                by_key[key] = row
                changed.add(key)
            previous_receipt = row.get("source_rcept_no", "")
            latest_receipt = filing.get("rcept_no", "")
            if latest_receipt and latest_receipt != previous_receipt:
                row["source_rcept_no"] = latest_receipt
                changed.add(key)
            for target, source in (
                ("corp_name", "corp_name"),
                ("stock_code", "stock_code"),
                ("corp_cls", "corp_cls"),
            ):
                if filing.get(source):
                    row[target] = filing[source]
    return rows, changed


def context_unit(raw: str) -> str:
    context = clean_text(raw)
    found: list[str] = []
    for match in re.finditer(r"단위\s*[:：]?\s*([^)]{0,80})", context):
        segment = match.group(1)
        for unit in UNIT_ORDER:
            if unit in segment:
                found.append(unit)
                break
    return found[-1] if found else ""


def parse_money(cell: str, unit: str) -> tuple[float | None, str | None]:
    if FOREIGN_CURRENCY_RE.search(cell):
        return None, "foreign_currency_fee"
    match = re.search(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)", cell)
    if not match:
        return None, None
    raw = float(match.group(1).replace(",", ""))
    tail = cell[match.end() : match.end() + 20].replace(" ", "")
    explicit = next((candidate for candidate in UNIT_ORDER if tail.startswith(candidate)), None)
    if explicit:
        return raw * UNIT_MULTIPLIERS[explicit], None

    # Some reports carry stale or malformed unit metadata. These magnitude
    # guards match the common DART representations: won, thousand won, million won.
    if raw >= 1_000_000:
        return raw, None
    if raw >= 10_000:
        return raw * 1_000, None
    if unit:
        return raw * UNIT_MULTIPLIERS[unit], None
    return raw * 1_000_000, None


def parse_hours(cell: str) -> int | None:
    match = re.search(r"(?<!\d)(\d[\d,]*)", cell)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def table_cells(row_html: str) -> list[str]:
    values = re.findall(
        r"<(?:TD|TH|TE)\b[^>]*>(.*?)</(?:TD|TH|TE)>",
        row_html,
        flags=re.I | re.S,
    )
    return [clean_text(value) for value in values]


def parse_audit_service_table(raw: str) -> dict[str, Any] | None:
    table_starts = [match.start() for match in re.finditer(r"<TABLE\b", raw, flags=re.I)]
    for table_start in table_starts:
        table_end = raw.find("</TABLE>", table_start)
        if table_end < 0:
            continue
        table = raw[table_start : table_end + len("</TABLE>")]
        body_start = table.upper().find("<TBODY")
        header = clean_text(table[: body_start if body_start >= 0 else len(table)])
        if "감사계약내역" not in header or "실제수행내역" not in header:
            continue
        unit = context_unit(raw[max(0, table_start - 2500) : table_start])
        body = table[body_start if body_start >= 0 else 0 :]
        candidates: list[tuple[int, dict[str, Any]]] = []
        for row_html in re.findall(r"<TR\b.*?</TR>", body, flags=re.I | re.S):
            cells = table_cells(row_html)
            auditor_index = next((i for i, cell in enumerate(cells) if "회계법인" in cell), None)
            if auditor_index is None:
                continue
            contract_cell = cells[auditor_index + 2] if auditor_index + 2 < len(cells) else ""
            actual_cell = cells[auditor_index + 4] if auditor_index + 4 < len(cells) else ""
            contract_fee, contract_warning = parse_money(contract_cell, unit)
            actual_fee, actual_warning = parse_money(actual_cell, unit)
            contract_hours = parse_hours(cells[auditor_index + 3]) if auditor_index + 3 < len(cells) else None
            actual_hours = parse_hours(cells[auditor_index + 5]) if auditor_index + 5 < len(cells) else None
            period = cells[0] if cells else ""
            score = 3 if "당기" in period else 2 if re.search(r"제\s*\d+\s*기", period) else 1
            candidates.append(
                (
                    score,
                    {
                        "auditor": cells[auditor_index],
                        "contract_fee": contract_fee,
                        "actual_fee": actual_fee,
                        "contract_hours": contract_hours,
                        "actual_hours": actual_hours,
                        "warnings": [
                            warning
                            for warning in (contract_warning, actual_warning)
                            if warning
                        ],
                    },
                )
            )
        if candidates:
            return max(candidates, key=lambda item: item[0])[1]
    return None


def parse_auditor_from_opinion_table(raw: str) -> str:
    table_starts = [match.start() for match in re.finditer(r"<TABLE\b", raw, flags=re.I)]
    for table_start in table_starts:
        table_end = raw.find("</TABLE>", table_start)
        if table_end < 0:
            continue
        table = raw[table_start : table_end + len("</TABLE>")]
        body_start = table.upper().find("<TBODY")
        header = clean_text(table[: body_start if body_start >= 0 else len(table)])
        if "감사인" not in header or "감사의견" not in header:
            continue
        body = table[body_start if body_start >= 0 else 0 :]
        rows = re.findall(r"<TR\b.*?</TR>", body, flags=re.I | re.S)
        scored: list[tuple[int, str]] = []
        for row_html in rows:
            cells = table_cells(row_html)
            auditor = next((cell for cell in cells if "회계법인" in cell), "")
            if not auditor:
                continue
            period = cells[0] if cells else ""
            score = 3 if "당기" in period else 2 if re.search(r"제\s*\d+\s*기", period) else 1
            scored.append((score, auditor))
        if scored:
            return max(scored, key=lambda item: item[0])[1]

    flat = clean_text(raw)
    patterns = (
        r"사업연도\s+감사인.{0,500}?제\s*\d+\s*기(?:\s*\([^)]*당기[^)]*\))?.{0,100}?([가-힣A-Za-z0-9&· ]+회계법인)",
        r"제\s*\d+\s*기\s*\([^)]*당기[^)]*\).{0,100}?([가-힣A-Za-z0-9&· ]+회계법인)",
    )
    for pattern in patterns:
        match = re.search(pattern, flat)
        if match:
            return match.group(1).strip()
    return ""


def document_needs_reconciliation(row: dict[str, str], receipt_changed: bool) -> bool:
    contract = parse_number(row.get("audit_contract_fee"))
    actual = parse_number(row.get("audit_actual_fee"))
    if receipt_changed or not row.get("auditor_raw", "").strip():
        return True
    if contract is None or actual is None or contract < 0 or actual < 0 or actual == 0:
        return True
    ratio = contract / actual
    return ratio < 0.1 or ratio > 10


def reconcile_document(
    client: DartClient,
    row: dict[str, str],
) -> tuple[tuple[str, str], dict[str, Any] | None, str]:
    key = (row.get("year", ""), row.get("corp_code", ""))
    receipt = row.get("source_rcept_no", "")
    if not receipt:
        return key, None, "missing_receipt"
    try:
        raw = client.get_document(receipt)
        audit = parse_audit_service_table(raw) or {}
        if not audit.get("auditor"):
            audit["auditor"] = parse_auditor_from_opinion_table(raw)
        if not audit.get("auditor") and not any(
            audit.get(field) is not None
            for field in ("contract_fee", "actual_fee")
        ):
            return key, None, "document_fields_unresolved"
        return key, audit, ""
    except DartError as exc:
        return key, None, str(exc)


def parse_financial_amount(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def choose_revenue(accounts: list[dict[str, Any]]) -> tuple[float, str] | None:
    candidates = [
        account
        for account in accounts
        if account.get("sj_div") in ("IS", "CIS")
        and str(account.get("currency") or "KRW").upper() == "KRW"
    ]
    id_priority = (
        ("ifrs-full_Revenue", "매출액"),
        ("ifrs_Revenue", "매출액"),
        ("dart_Revenue", "매출액"),
        ("ifrs-full_RevenueFromInterest", "이자수익"),
    )
    for account_id, label in id_priority:
        for account in candidates:
            if account.get("account_id") != account_id:
                continue
            amount = parse_financial_amount(account.get("thstrm_amount"))
            if amount is not None and amount >= 0:
                return amount, label

    exact_names = (
        ("매출액", "매출액"),
        ("영업수익", "매출액"),
        ("수익(매출액)", "매출액"),
        ("매출", "매출액"),
        ("수익", "매출액"),
        ("이자수익", "이자수익"),
    )
    for account_name, label in exact_names:
        for account in candidates:
            if clean_text(str(account.get("account_nm") or "")) != account_name:
                continue
            amount = parse_financial_amount(account.get("thstrm_amount"))
            if amount is not None and amount >= 0:
                return amount, label
    return None


def fetch_revenue(
    client: DartClient,
    row: dict[str, str],
) -> tuple[tuple[str, str], dict[str, str] | None, str]:
    key = (row.get("year", ""), row.get("corp_code", ""))
    for fs_div in ("CFS", "OFS"):
        try:
            payload = client.get_json(
                "fnlttSinglAcntAll.json",
                corp_code=row.get("corp_code", ""),
                bsns_year=row.get("year", ""),
                reprt_code="11011",
                fs_div=fs_div,
            )
        except DartError as exc:
            return key, None, str(exc)
        if payload.get("status") == "013":
            continue
        selected = choose_revenue(payload.get("list", []))
        if selected:
            amount, account = selected
            return key, {
                "revenue": format_number(amount),
                "revenue_account": account,
                "revenue_fs_div": fs_div,
            }, ""
    return key, None, "revenue_unavailable"


def run_parallel(function: Any, items: list[Any], workers: int) -> list[Any]:
    if not items:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(function, items))


def load_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [{key: str(value or "") for key, value in row.items()} for row in reader]
        fields = list(reader.fieldnames or [])
    for row in rows:
        for field in BASE_FIELDS + PROVENANCE_FIELDS:
            row.setdefault(field, "")
    return rows, fields


def save_csv(path: Path, rows: list[dict[str, str]], original_fields: list[str]) -> None:
    fields = list(dict.fromkeys(original_fields + BASE_FIELDS + PROVENANCE_FIELDS))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def metric_summary(rows: list[dict[str, str]], field: str) -> tuple[int, float]:
    totals: Counter[str] = Counter()
    coverage = 0
    for row in rows:
        value = 1.0 if field == "company_count" else parse_number(row.get(field))
        if value is None or value < 0:
            continue
        totals[row.get("auditor_group", "other_or_unknown")] += value
        coverage += 1
    denominator = sum(totals.values())
    numerator = sum(totals[group] for group in BIG4_GROUPS)
    return coverage, round(numerator / denominator * 100, 2) if denominator else 0.0


def print_summary(rows: list[dict[str, str]], years: list[int]) -> None:
    for year in years:
        selected = [row for row in rows if row.get("year") == str(year)]
        unresolved = sum(row.get("validation_status") == "unresolved" for row in selected)
        values: dict[str, Any] = {
            "year": year,
            "records": len(selected),
            "unresolved": unresolved,
        }
        for field in ("company_count", "audit_contract_fee", "audit_actual_fee", "revenue"):
            coverage, share = metric_summary(selected, field)
            values[field] = {"coverage": coverage, "big4_share": share}
        print(json.dumps(values, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--years", nargs="+", type=int, default=[2023, 2024])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--skip-universe-refresh", action="store_true")
    parser.add_argument("--skip-revenue", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        print("DART_API_KEY is required", file=sys.stderr)
        return 2

    rows, original_fields = load_csv(args.input)
    client = DartClient(api_key)
    changed_receipts: set[tuple[str, str]] = set()
    if not args.skip_universe_refresh:
        filings_by_year: dict[int, dict[str, dict[str, str]]] = {}
        for year in args.years:
            filings_by_year[year] = fetch_latest_filings(client, year)
            print(f"{year}: latest annual-report filings {len(filings_by_year[year]):,}")
        rows, changed_receipts = merge_universe(rows, filings_by_year)

    selected_rows = [row for row in rows if int(row.get("year") or 0) in args.years]
    for row in selected_rows:
        row["auditor_group"] = normalize_auditor(row.get("auditor_raw", ""))
        row["auditor_source"] = row.get("auditor_source") or (
            "adtServcCnclsSttus" if row.get("auditor_raw") else ""
        )
        row["fee_source"] = row.get("fee_source") or (
            "adtServcCnclsSttus" if row.get("audit_contract_fee") else ""
        )
        row["revenue_source"] = row.get("revenue_source") or (
            "fnlttSinglAcntAll" if row.get("revenue") else ""
        )

    document_targets = [
        row
        for row in selected_rows
        if document_needs_reconciliation(
            row,
            (row.get("year", ""), row.get("corp_code", "")) in changed_receipts,
        )
    ]
    print(f"source filing reconciliation targets: {len(document_targets):,}")
    document_results = run_parallel(
        lambda row: reconcile_document(client, row),
        document_targets,
        args.workers,
    )
    by_key = {(row.get("year", ""), row.get("corp_code", "")): row for row in rows}
    for key, result, error in document_results:
        row = by_key[key]
        if result:
            if result.get("auditor"):
                row["auditor_raw"] = str(result["auditor"])
                row["auditor_group"] = normalize_auditor(row["auditor_raw"])
                row["auditor_source"] = "document.xml"
            replacements = (
                ("audit_contract_fee", "contract_fee"),
                ("audit_actual_fee", "actual_fee"),
                ("audit_contract_hours", "contract_hours"),
                ("audit_actual_hours", "actual_hours"),
            )
            replaced_fee = False
            for target, source in replacements:
                if result.get(source) is not None:
                    row[target] = format_number(result[source])
                    replaced_fee = True
            if replaced_fee:
                row["fee_source"] = "document.xml"
            for warning in result.get("warnings", []):
                append_warning(row, warning)
        else:
            append_warning(row, error or "document_fallback_failed")

    if not args.skip_revenue:
        revenue_targets = [row for row in selected_rows if parse_number(row.get("revenue")) is None]
        print(f"revenue enrichment targets: {len(revenue_targets):,}")
        revenue_results = run_parallel(
            lambda row: fetch_revenue(client, row),
            revenue_targets,
            args.workers,
        )
        for key, result, error in revenue_results:
            row = by_key[key]
            if result:
                row.update(result)
                row["revenue_source"] = "fnlttSinglAcntAll"
            elif error and error != "revenue_unavailable":
                append_warning(row, error)

    for row in selected_rows:
        row["auditor_group"] = normalize_auditor(row.get("auditor_raw", ""))
        auditor_ok = bool(row.get("auditor_raw", "").strip())
        fees_ok = parse_number(row.get("audit_contract_fee")) is not None
        if auditor_ok and fees_ok:
            row["validation_status"] = (
                "source_verified"
                if "document.xml" in (row.get("auditor_source"), row.get("fee_source"))
                else "api_complete"
            )
        else:
            row["validation_status"] = "unresolved"

    rows.sort(key=lambda row: (row.get("year", ""), row.get("corp_name", ""), row.get("corp_code", "")))
    save_csv(args.output, rows, original_fields)
    print_summary(rows, args.years)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
