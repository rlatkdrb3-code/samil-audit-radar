#!/usr/bin/env python3
"""OpenDART-based external auditor tenure and periodic designation radar."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


BASE_URL = "https://opendart.fss.or.kr/api"
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do"
REPORT_CODE_ANNUAL = "11011"
DEFAULT_YEARS = 10
MIN_YEARS = 4
MAX_YEARS = 12
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 30
RESPONSE_CACHE_TTL_SECONDS = 60 * 60
ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
CACHE_DIR = PROJECT_ROOT / ".cache"
CORP_CACHE = CACHE_DIR / "corp_codes.json"
ENV_FILES = (PROJECT_ROOT / ".env.local", PROJECT_ROOT / ".env")
REQUEST_LOGS: dict[str, list[float]] = {}
RESPONSE_CACHE: dict[str, tuple[float, Any]] = {}
SERVER_LOCK = threading.Lock()
CORP_CLASS_LABELS = {
    "Y": "코스피(유가증권시장)",
    "K": "코스닥",
    "N": "코넥스",
    "E": "기타",
}
BIG4_ALIASES = {
    "samil": "삼일",
    "pwc": "삼일",
    "삼일pwc": "삼일",
    "samilpricewaterhousecoopers": "삼일",
    "삼정kpmg": "삼정",
    "kpmg": "삼정",
    "deloitte": "안진",
    "안진": "안진",
    "ey": "한영",
    "ernstyoung": "한영",
    "한영": "한영",
}
SPECIAL_ISSUE_KEYWORDS = (
    "감사전재무제표미제출",
    "미제출",
    "지연",
    "제출기한",
    "연장",
    "정정",
)


@dataclass
class AppConfig:
    api_key: str | None
    current_year: int
    demo: bool = False


def main() -> int:
    parser = argparse.ArgumentParser(description="Samil Audit Radar")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="Search OpenDART corporation codes")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--demo", action="store_true")

    report = sub.add_parser("report", help="Generate an audit radar report")
    report.add_argument("company", help="Company name, stock code, or corp_code")
    report.add_argument("--corp-code", help="Explicit DART corp_code")
    report.add_argument("--years", type=int, default=DEFAULT_YEARS)
    report.add_argument("--output")
    report.add_argument("--format", choices=("markdown", "json"), default="markdown")
    report.add_argument("--demo", action="store_true")

    serve = sub.add_parser("serve", help="Run the local web service")
    serve.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=env_int("PORT", 8765))
    serve.add_argument("--demo", action="store_true")

    demo = sub.add_parser("demo", help="Print a sample report without an API key")
    demo.add_argument("--format", choices=("markdown", "json"), default="markdown")

    args = parser.parse_args()
    config = AppConfig(
        api_key=load_api_key(),
        current_year=date.today().year,
        demo=getattr(args, "demo", False) or args.command == "demo",
    )

    if args.command == "search":
        results = search_companies(args.query, config, limit=args.limit)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    if args.command == "report":
        payload = build_report(
            args.company,
            config,
            years=clamp_years(args.years),
            corp_code=args.corp_code,
        )
        rendered = render_report(payload, args.format)
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        return 0
    if args.command == "serve":
        run_server(args.host, args.port, config)
        return 0
    if args.command == "demo":
        payload = build_demo_report()
        print(render_report(payload, args.format))
        return 0
    return 1


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def clamp_years(value: int) -> int:
    return max(MIN_YEARS, min(MAX_YEARS, value))


def load_api_key() -> str | None:
    for key_name in ("DART_API_KEY", "OPEN_DART_API_KEY", "OPENDART_API_KEY"):
        value = os.environ.get(key_name)
        if value and value.strip():
            return value.strip()

    for env_path in ENV_FILES:
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            if name.strip() in {"DART_API_KEY", "OPEN_DART_API_KEY", "OPENDART_API_KEY"}:
                cleaned = value.strip().strip('"').strip("'")
                if cleaned:
                    return cleaned
    return None


def require_key(config: AppConfig) -> str:
    if config.demo:
        return "DEMO"
    if not config.api_key:
        raise RuntimeError(
            "DART_API_KEY is not set. Set it in your shell or create .env.local."
        )
    return config.api_key


def dart_get(endpoint: str, config: AppConfig, params: dict[str, str]) -> dict[str, Any]:
    api_key = require_key(config)
    query = dict(params)
    query["crtfc_key"] = api_key
    url = f"{BASE_URL}/{endpoint}.json?{urllib.parse.urlencode(query)}"
    with urllib.request.urlopen(url, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    status = str(payload.get("status", ""))
    if status in {"000", "013"}:
        return payload
    message = payload.get("message", "Unknown OpenDART error")
    raise RuntimeError(f"OpenDART error {status}: {message}")


def load_corp_codes(config: AppConfig, *, refresh: bool = False) -> list[dict[str, str]]:
    if config.demo:
        return load_demo_companies()
    if CORP_CACHE.is_file() and not refresh:
        return json.loads(CORP_CACHE.read_text(encoding="utf-8"))

    api_key = require_key(config)
    url = f"{BASE_URL}/corpCode.xml?{urllib.parse.urlencode({'crtfc_key': api_key})}"
    with urllib.request.urlopen(url, timeout=30) as response:
        content = response.read()

    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
        if not names:
            raise RuntimeError("OpenDART corpCode response had no files.")
        xml_bytes = archive.read(names[0])

    root = ElementTree.fromstring(xml_bytes)
    companies = []
    for node in root.findall("list"):
        companies.append(
            {
                "corp_code": text_of(node, "corp_code"),
                "corp_name": text_of(node, "corp_name"),
                "stock_code": text_of(node, "stock_code"),
                "modify_date": text_of(node, "modify_date"),
            }
        )
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CORP_CACHE.write_text(json.dumps(companies, ensure_ascii=False), encoding="utf-8")
    return companies


def text_of(node: ElementTree.Element, tag: str) -> str:
    child = node.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def search_companies(query: str, config: AppConfig, *, limit: int = 10) -> list[dict[str, str]]:
    normalized = normalize_search(query[:80])
    limit = max(1, min(20, limit))
    companies = load_corp_codes(config)
    scored = []
    for company in companies:
        name = normalize_search(company.get("corp_name", ""))
        stock_code = company.get("stock_code", "")
        corp_code = company.get("corp_code", "")
        score = 0
        if normalized == corp_code:
            score = 100
        elif normalized == stock_code:
            score = 95
        elif normalized == name:
            score = 90
        elif normalized and name.startswith(normalized):
            score = 70
        elif normalized and normalized in name:
            score = 50
        if score:
            scored.append((score, company))
    scored.sort(key=lambda item: (-item[0], item[1].get("corp_name", "")))
    return [item for _, item in scored[:limit]]


def normalize_search(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def resolve_company(company: str, config: AppConfig, corp_code: str | None = None) -> dict[str, str]:
    if config.demo:
        return load_demo_companies()[0]
    if corp_code:
        matches = [item for item in load_corp_codes(config) if item.get("corp_code") == corp_code]
        if matches:
            return matches[0]
        return {"corp_code": corp_code, "corp_name": company, "stock_code": "", "modify_date": ""}
    matches = search_companies(company, config, limit=5)
    if not matches:
        raise RuntimeError(f"No company matched: {company}")
    return matches[0]


def build_report(
    company: str,
    config: AppConfig,
    *,
    years: int = DEFAULT_YEARS,
    corp_code: str | None = None,
) -> dict[str, Any]:
    if config.demo:
        return build_demo_report()
    years = clamp_years(years)
    corp = resolve_company(company[:80], config, corp_code=corp_code)
    structured_history = fetch_audit_history(corp["corp_code"], config, years=years)
    disclosure_bundle = fetch_external_audit_disclosures(corp["corp_code"], config, years=years)
    audit_history = merge_audit_sources(
        structured_history,
        disclosure_bundle["history"],
        years=years,
    )
    service_history = fetch_service_contracts(corp["corp_code"], config, years=min(years, 5))
    analysis = analyze_history(corp, audit_history, config.current_year)
    if disclosure_bundle["special_issues"] and analysis.get("status") == "ok":
        analysis.setdefault("follow_up", []).insert(
            0,
            "감사보고서 미제출·지연·연장·정정 등 특이공시 원문 확인",
        )
    coverage = build_coverage_summary(
        audit_history,
        structured_history,
        disclosure_bundle["history"],
        disclosure_bundle["special_issues"],
        years=years,
        current_year=config.current_year,
        external_error=disclosure_bundle.get("error"),
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "OpenDART public API (periodic reports + external-audit disclosures)",
        "company": corp,
        "history": audit_history,
        "history_sources": {
            "periodic_report_api": structured_history,
            "external_audit_reports": disclosure_bundle["history"],
        },
        "audit_disclosures": disclosure_bundle["filings"],
        "special_issues": disclosure_bundle["special_issues"],
        "coverage": coverage,
        "service_contracts": service_history,
        "analysis": analysis,
        "disclaimers": [
            "Public DART data does not directly label free appointment, periodic designation, split designation, deferral, or all private-company eligibility facts.",
            "External-audit disclosure rows use DART filing-list metadata and should be checked against the original audit report when used for outreach or acceptance decisions.",
            "Missing-year notes mean the plugin did not find a matching public filing in the searched window; they are not proof of legal non-submission.",
            "The timing result is an estimate for research and follow-up planning, not a legal or audit acceptance conclusion.",
        ],
    }


def fetch_audit_history(corp_code: str, config: AppConfig, *, years: int) -> list[dict[str, Any]]:
    rows_by_year: dict[str, dict[str, Any]] = {}
    start_year = config.current_year - 1
    fetch_years = list(range(start_year, start_year - years - 2, -1))
    for year, rows in fetch_yearly_payloads(
        "accnutAdtorNmNdAdtOpinion",
        corp_code,
        config,
        fetch_years,
    ):
        for row in select_current_period_rows(rows):
            auditor = str(row.get("adtor", "")).strip()
            if not auditor:
                continue
            item = normalize_disclosure_row(row, year)
            item["source_kind"] = "periodic_report_api"
            item["source_detail"] = "정기보고서 주요정보 API"
            item["rcept_url"] = dart_viewer_url(item.get("rcept_no", ""))
            bsns_year = item["bsns_year"]
            existing = rows_by_year.get(bsns_year)
            if existing is None or row_priority(item) > row_priority(existing):
                rows_by_year[bsns_year] = item

    history = list(rows_by_year.values())
    history.sort(key=lambda row: int_or_zero(row.get("bsns_year")), reverse=True)
    return history[:years]


def fetch_external_audit_disclosures(
    corp_code: str,
    config: AppConfig,
    *,
    years: int,
) -> dict[str, Any]:
    start_year = max(1999, config.current_year - years - 1)
    start_date = f"{start_year}0101"
    end_date = date.today().strftime("%Y%m%d")
    try:
        filings = dart_list_filings(
            corp_code,
            config,
            bgn_de=start_date,
            end_de=end_date,
            pblntf_ty="F",
        )
    except RuntimeError as exc:
        return {"history": [], "filings": [], "special_issues": [], "error": str(exc)}

    normalized_filings = [normalize_filing_row(row) for row in filings]
    history_by_year: dict[str, dict[str, Any]] = {}
    special_issues: list[dict[str, Any]] = []

    for filing in normalized_filings:
        issue = classify_special_issue(filing)
        if issue:
            special_issues.append(issue)

        if not is_external_audit_report_filing(filing):
            continue
        history_row = filing_to_history_row(filing)
        if not history_row:
            continue
        year = history_row["bsns_year"]
        existing = history_by_year.get(year)
        if existing is None or filing_history_priority(history_row) > filing_history_priority(existing):
            history_by_year[year] = history_row

    history = list(history_by_year.values())
    history.sort(key=lambda row: int_or_zero(row.get("bsns_year")), reverse=True)
    special_issues.sort(key=lambda row: row.get("rcept_dt", ""), reverse=True)
    return {
        "history": history[:years],
        "filings": normalized_filings,
        "special_issues": special_issues[:20],
        "error": None,
    }


def dart_list_filings(
    corp_code: str,
    config: AppConfig,
    *,
    bgn_de: str,
    end_de: str,
    pblntf_ty: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page_no = 1
    while True:
        payload = dart_get(
            "list",
            config,
            {
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "pblntf_ty": pblntf_ty,
                "page_no": str(page_no),
                "page_count": "100",
                "sort": "date",
                "sort_mth": "desc",
            },
        )
        page_rows = payload.get("list", []) or []
        rows.extend(page_rows)
        total_page = int_or_zero(payload.get("total_page")) or 1
        if page_no >= total_page or not page_rows:
            break
        page_no += 1
    return rows


def normalize_filing_row(row: dict[str, Any]) -> dict[str, Any]:
    rcept_no = str(row.get("rcept_no", "")).strip()
    report_nm = str(row.get("report_nm", "")).strip()
    period = extract_report_period(report_nm)
    return {
        "corp_cls": str(row.get("corp_cls", "")).strip(),
        "corp_code": str(row.get("corp_code", "")).strip(),
        "corp_name": str(row.get("corp_name", "")).strip(),
        "report_nm": report_nm,
        "flr_nm": str(row.get("flr_nm", "")).strip(),
        "rcept_no": rcept_no,
        "rcept_dt": str(row.get("rcept_dt", "")).strip(),
        "rcept_url": dart_viewer_url(rcept_no),
        "rm": str(row.get("rm", "")).strip(),
        "period_year": period.get("year", ""),
        "period_month": period.get("month", ""),
    }


def extract_report_period(report_name: str) -> dict[str, str]:
    patterns = [
        r"\((20\d{2})[.\-/년\s]*(0[1-9]|1[0-2])?\)",
        r"(20\d{2})[.\-/](0[1-9]|1[0-2])",
    ]
    for pattern in patterns:
        match = re.search(pattern, report_name)
        if match:
            return {"year": match.group(1), "month": match.group(2) or ""}
    return {"year": "", "month": ""}


def is_external_audit_report_filing(filing: dict[str, Any]) -> bool:
    report_name = re.sub(r"\s+", "", filing.get("report_nm", ""))
    if "감사보고서" not in report_name:
        return False
    excluded = ("회계법인사업보고서", "감사전재무제표", "제출기한연장")
    return not any(keyword in report_name for keyword in excluded)


def filing_to_history_row(filing: dict[str, Any]) -> dict[str, Any] | None:
    auditor = filing.get("flr_nm", "").strip()
    if not is_meaningful_value(auditor):
        return None
    business_year = filing.get("period_year") or infer_business_year_from_receipt(
        filing.get("rcept_dt", "")
    )
    if not business_year:
        return None
    return {
        "bsns_year": business_year,
        "adtor": auditor,
        "adt_opinion": "",
        "corp_cls": filing.get("corp_cls", ""),
        "corp_code": filing.get("corp_code", ""),
        "corp_name": filing.get("corp_name", ""),
        "report_nm": filing.get("report_nm", ""),
        "rcept_no": filing.get("rcept_no", ""),
        "rcept_dt": filing.get("rcept_dt", ""),
        "rcept_url": filing.get("rcept_url", ""),
        "period_label": report_period_label(filing),
        "source_kind": "external_audit_report",
        "source_detail": "외부감사관련 감사보고서 공시목록",
        "source_note": "감사인은 공시목록 제출인 기준이며 감사의견은 원문 확인이 필요합니다.",
    }


def infer_business_year_from_receipt(rcept_dt: str) -> str:
    match = re.match(r"(20\d{2})(\d{2})(\d{2})", rcept_dt or "")
    if not match:
        return ""
    filing_year = int(match.group(1))
    filing_month = int(match.group(2))
    return str(filing_year - 1 if filing_month <= 6 else filing_year)


def report_period_label(filing: dict[str, Any]) -> str:
    year = filing.get("period_year", "")
    month = filing.get("period_month", "")
    if year and month:
        return f"{year}.{month}"
    return year


def filing_history_priority(row: dict[str, Any]) -> tuple[int, int, int]:
    report_name = row.get("report_nm", "")
    is_revision = int("정정" in report_name)
    is_standalone = int("연결감사보고서" not in report_name)
    receipt_date = digits_int(row.get("rcept_dt"))
    return is_revision, is_standalone, receipt_date


def classify_special_issue(filing: dict[str, Any]) -> dict[str, Any] | None:
    report_name = filing.get("report_nm", "")
    compact = re.sub(r"\s+", "", report_name)
    labels = []
    if "감사전재무제표미제출" in compact:
        labels.append("감사전 재무제표 미제출")
    if any(keyword in compact for keyword in ("지연", "제출기한", "연장")):
        labels.append("제출 지연/기한 연장")
    if "정정" in compact:
        labels.append("정정 공시")
    if not labels and not any(keyword in compact for keyword in SPECIAL_ISSUE_KEYWORDS):
        return None
    issue = dict(filing)
    issue["issue_type"] = ", ".join(labels or ["특이 키워드 포함"])
    return issue


def merge_audit_sources(
    structured_history: list[dict[str, Any]],
    external_history: list[dict[str, Any]],
    *,
    years: int,
) -> list[dict[str, Any]]:
    rows_by_year: dict[str, dict[str, Any]] = {}
    for row in external_history:
        year = str(row.get("bsns_year", "")).strip()
        if year:
            rows_by_year[year] = dict(row)
    for row in structured_history:
        year = str(row.get("bsns_year", "")).strip()
        if year:
            rows_by_year[year] = dict(row)

    merged = list(rows_by_year.values())
    merged.sort(key=lambda row: int_or_zero(row.get("bsns_year")), reverse=True)
    return merged[:years]


def build_coverage_summary(
    history: list[dict[str, Any]],
    structured_history: list[dict[str, Any]],
    external_history: list[dict[str, Any]],
    special_issues: list[dict[str, Any]],
    *,
    years: int,
    current_year: int,
    external_error: str | None,
) -> dict[str, Any]:
    history_years = {str(row.get("bsns_year", "")).strip() for row in history}
    recent_years = [str(year) for year in range(current_year - 1, current_year - min(years, 4), -1)]
    missing_recent_years = [year for year in recent_years if year not in history_years]
    notes = [
        "정기보고서 주요정보 API를 우선 사용하고, 누락 연도는 외부감사관련 감사보고서 공시목록으로 보완합니다."
    ]
    if missing_recent_years:
        notes.append(
            "최근 연도 중 감사인 이력이 확인되지 않은 연도는 미제출, 제출 지연, 비대상, 명칭 불일치 가능성을 구분해 원문 확인이 필요합니다."
        )
    if external_error:
        notes.append(f"외부감사관련 공시검색 보조 조회 실패: {external_error}")
    return {
        "merged_rows": len(history),
        "periodic_report_api_rows": len(structured_history),
        "external_audit_report_rows": len(external_history),
        "special_issue_rows": len(special_issues),
        "missing_recent_years": missing_recent_years,
        "notes": notes,
    }


def dart_viewer_url(rcept_no: Any) -> str:
    receipt = str(rcept_no or "").strip()
    if not receipt:
        return ""
    return f"{DART_VIEWER_URL}?{urllib.parse.urlencode({'rcpNo': receipt})}"


def fetch_yearly_payloads(
    endpoint: str,
    corp_code: str,
    config: AppConfig,
    years: list[int],
) -> list[tuple[int, list[dict[str, Any]]]]:
    if not years:
        return []

    def load_year(year: int) -> tuple[int, list[dict[str, Any]]]:
        try:
            payload = dart_get(
                endpoint,
                config,
                {
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": REPORT_CODE_ANNUAL,
                },
            )
        except RuntimeError:
            return year, []
        return year, payload.get("list", []) or []

    max_workers = min(5, len(years))
    results: list[tuple[int, list[dict[str, Any]]]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(load_year, year) for year in years]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item[0], reverse=True)
    return results


def select_current_period_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [row for row in rows if is_meaningful_value(row.get("adtor", ""))]
    current = [row for row in valid if is_current_period_row(row)]
    if current:
        return current
    return valid[:1]


def is_meaningful_value(value: Any) -> bool:
    text = re.sub(r"\s+", "", str(value or ""))
    return bool(text) and text not in {"-", "해당사항없음", "해당없음", "없음"}


def is_current_period_row(row: dict[str, Any]) -> bool:
    label = re.sub(r"\s+", "", str(row.get("bsns_year", "")))
    return "당기" in label


def normalize_disclosure_row(row: dict[str, Any], business_year: int) -> dict[str, Any]:
    item = dict(row)
    period_label = str(item.get("bsns_year", "")).strip()
    if period_label and period_label != str(business_year):
        item["period_label"] = period_label
    item["bsns_year"] = str(business_year)
    return item


def row_priority(row: dict[str, Any]) -> int:
    score = 0
    if row.get("core_adt_matter"):
        score += 2
    if row.get("emphs_matter"):
        score += 1
    if row.get("rcept_no"):
        score += 1
    return score


def fetch_service_contracts(corp_code: str, config: AppConfig, *, years: int) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    start_year = config.current_year - 1
    fetch_years = list(range(start_year, start_year - years, -1))
    for year, rows in fetch_yearly_payloads("adtServcCnclsSttus", corp_code, config, fetch_years):
        for row in select_current_period_rows(rows):
            contracts.append(normalize_disclosure_row(row, year))
    contracts.sort(key=lambda row: int_or_zero(row.get("bsns_year")), reverse=True)
    return contracts


def analyze_history(corp: dict[str, str], history: list[dict[str, Any]], current_year: int) -> dict[str, Any]:
    if not history:
        return {
            "status": "no_data",
            "confidence": "low",
            "current_auditor": None,
            "message": "최근 사업보고서 및 외부감사관련 공시에서 감사인 이력을 찾지 못했습니다.",
            "follow_up": ["회사명/고유번호가 맞는지 확인", "DART 감사보고서 원문에서 수동 확인"],
        }

    rows = normalize_history_rows(history)
    latest = rows[0]
    runs = build_runs(rows)
    latest_run = runs[0]
    previous_run = runs[1] if len(runs) > 1 else None
    subject = periodic_subject_estimate(corp, latest.get("corp_cls") or corp.get("corp_cls", ""))
    event = estimate_event(latest_run, previous_run, subject)

    return {
        "status": "ok",
        "confidence": event["confidence"],
        "corp_class": latest.get("corp_cls") or corp.get("corp_cls", ""),
        "corp_class_label": CORP_CLASS_LABELS.get(latest.get("corp_cls") or corp.get("corp_cls", ""), "알 수 없음"),
        "current_auditor": latest["adtor"],
        "current_auditor_key": latest["auditor_key"],
        "latest_source": latest.get("source_detail", "OpenDART"),
        "latest_source_note": latest.get("source_note", ""),
        "latest_business_year": latest["bsns_year"],
        "consecutive_years": latest_run["length"],
        "current_run": latest_run,
        "previous_run": previous_run,
        "periodic_subject_estimate": subject,
        "estimated_event": event,
        "as_of_calendar_year": current_year,
        "follow_up": build_follow_up(subject, event),
    }


def normalize_history_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in history:
        year = str(row.get("bsns_year", "")).strip()
        auditor = str(row.get("adtor", "")).strip()
        if not year or not auditor:
            continue
        item = dict(row)
        item["bsns_year"] = year
        item["adtor"] = auditor
        item["auditor_key"] = normalize_auditor(auditor)
        rows.append(item)
    rows.sort(key=lambda row: int_or_zero(row["bsns_year"]), reverse=True)
    return rows


def build_runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for row in rows:
        year = int_or_zero(row["bsns_year"])
        key = row["auditor_key"]
        if runs and runs[-1]["auditor_key"] == key and runs[-1]["start_year"] == year + 1:
            runs[-1]["start_year"] = year
            runs[-1]["length"] += 1
            runs[-1]["years"].append(str(year))
            continue
        runs.append(
            {
                "auditor": row["adtor"],
                "auditor_key": key,
                "end_year": year,
                "start_year": year,
                "length": 1,
                "years": [str(year)],
            }
        )
    return runs


def normalize_auditor(value: str) -> str:
    cleaned = value.lower()
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"(유한회사|회계법인|감사반|법인|주식회사|\(주\)|㈜)", "", cleaned)
    cleaned = re.sub(r"[^0-9a-z가-힣]", "", cleaned)
    for alias, canonical in BIG4_ALIASES.items():
        if alias in cleaned:
            return canonical
    return cleaned


def periodic_subject_estimate(corp: dict[str, str], corp_cls: str) -> dict[str, str]:
    if corp_cls in {"Y", "K"}:
        return {
            "status": "likely_subject",
            "label": f"{CORP_CLASS_LABELS[corp_cls]} 상장회사",
            "reason": "OpenDART 법인구분상 유가증권시장/코스닥 상장회사로 확인됩니다.",
        }
    if corp_cls == "N":
        return {
            "status": "needs_review",
            "label": "코넥스",
            "reason": "코넥스 회사는 주기적 지정 적용 여부와 예외를 별도 확인해야 합니다.",
        }
    return {
        "status": "unknown_private",
        "label": CORP_CLASS_LABELS.get(corp_cls, "기타/알 수 없음"),
        "reason": "공개 API의 법인구분만으로 대형비상장·소유경영미분리 여부를 확정할 수 없습니다.",
    }


def estimate_event(
    latest_run: dict[str, Any],
    previous_run: dict[str, Any] | None,
    subject: dict[str, str],
) -> dict[str, Any]:
    length = latest_run["length"]
    subject_status = subject["status"]
    if subject_status == "unknown_private":
        return {
            "type": "insufficient_private_company_data",
            "headline": "비상장 주기적 지정 대상 여부 확인 필요",
            "confidence": "low",
            "years_remaining": None,
            "message": "공개 법인구분만으로 대형비상장 및 소유·경영 미분리 요건을 판단할 수 없습니다.",
        }

    if previous_run and length <= 3 and previous_run["length"] >= 6:
        remaining = max(0, 3 - length)
        if remaining == 0:
            headline = "지정감사 3년차 가능성: 다음 자유선임 전환 검토 필요"
            message = "이전 감사인이 6년 이상 연속된 뒤 감사인이 변경되어, 현재 감사인이 지정감사인일 가능성이 있습니다."
        else:
            headline = f"지정감사 {length}년차 가능성: 약 {remaining}개 사업연도 남음"
            message = "이전 장기 자유선임 뒤 감사인이 변경된 패턴입니다. 지정감사 여부를 FSS 통지 또는 회사 공시로 확인해야 합니다."
        return {
            "type": "possible_designated_cycle",
            "headline": headline,
            "confidence": "medium",
            "years_remaining": remaining,
            "message": message,
        }

    if length >= 6:
        return {
            "type": "six_year_threshold_reached",
            "headline": "동일 감사인 6년 이상: 주기적 지정/유예/분산지정 확인 필요",
            "confidence": "medium",
            "years_remaining": 0,
            "message": "상장회사 등은 6년 자유선임 후 3년 지정 제도 적용 가능성이 있으므로 FSS 지정 통지, 유예, 분산지정 여부를 확인해야 합니다.",
        }

    remaining = 6 - length
    if length >= 4:
        confidence = "medium" if subject_status == "likely_subject" else "low"
        return {
            "type": "approaching_six_year_threshold",
            "headline": f"동일 감사인 {length}년차: 약 {remaining}개 사업연도 후 6년 기준 도달",
            "confidence": confidence,
            "years_remaining": remaining,
            "message": "동일 감사인이 계속 유지된다는 가정하에 주기적 지정 검토 시점이 다가옵니다.",
        }

    return {
        "type": "early_tenure",
        "headline": f"동일 감사인 {length}년차: 단기 모니터링",
        "confidence": "medium" if subject_status == "likely_subject" else "low",
        "years_remaining": 6 - length,
        "message": "현재 공개 이력상 즉시 주기적 지정 임박 신호는 약합니다.",
    }


def build_follow_up(subject: dict[str, str], event: dict[str, Any]) -> list[str]:
    checks = [
        "FSS 감사인 지정 사전/본통지 여부 확인",
        "감사인 변경 사유가 자유선임인지 지정인지 사업보고서 원문에서 확인",
        "감사위원회 또는 감사인선임위원회 승인 및 선임보고 기한 확인",
    ]
    if subject["status"] != "likely_subject":
        checks.append("대형비상장회사, 금융회사, 사업보고서 제출대상 여부 확인")
    if event["type"] == "possible_designated_cycle":
        checks.append("지정감사 종료 후 최초 자유선임 시 전기 지정감사인 배제 규정 적용 여부 확인")
    if event["type"] in {"six_year_threshold_reached", "approaching_six_year_threshold"}:
        checks.append("주기적 지정 유예 또는 분산지정 적용 여부 확인")
    return checks


def build_demo_report() -> dict[str, Any]:
    demo_path = ROOT / "examples" / "sample_audit_history.json"
    payload = json.loads(demo_path.read_text(encoding="utf-8"))
    history = []
    for row in payload["history"]:
        item = dict(row)
        item.setdefault("source_kind", "demo_fixture")
        item.setdefault("source_detail", "데모 데이터")
        history.append(item)
    corp = {
        "corp_code": payload["corp_code"],
        "corp_name": payload["corp_name"],
        "stock_code": "",
        "modify_date": "",
        "corp_cls": payload["corp_cls"],
    }
    analysis = analyze_history(corp, history, date.today().year)
    coverage = build_coverage_summary(
        history,
        history,
        [],
        [],
        years=len(history),
        current_year=date.today().year,
        external_error=None,
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Demo fixture",
        "company": corp,
        "history": history,
        "history_sources": {
            "periodic_report_api": history,
            "external_audit_reports": [],
        },
        "audit_disclosures": [],
        "special_issues": [],
        "coverage": coverage,
        "service_contracts": [],
        "analysis": analysis,
        "disclaimers": ["Demo data only."],
    }


def load_demo_companies() -> list[dict[str, str]]:
    return [
        {
            "corp_code": "00000000",
            "corp_name": "샘플테크",
            "stock_code": "000000",
            "modify_date": "20260702",
        }
    ]


def render_report(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return render_markdown(payload)


def render_markdown(payload: dict[str, Any]) -> str:
    company = payload["company"]
    analysis = payload["analysis"]
    event = analysis.get("estimated_event", {})
    lines = [
        "# Samil Audit Radar Report",
        "",
        f"- 회사: **{company.get('corp_name', '')}**",
        f"- 고유번호: `{company.get('corp_code', '')}`",
        f"- 생성시각: `{payload['generated_at']}`",
        f"- 출처: {payload['source']}",
        "",
        "## 핵심 판단",
        "",
    ]
    if analysis.get("status") != "ok":
        lines.append(f"- {analysis.get('message', 'No data')}")
    else:
        lines.extend(
            [
                f"- 현재 감사인: **{analysis['current_auditor']}**",
                f"- 최신 사업연도: **{analysis['latest_business_year']}**",
                f"- 동일 감사인 연속연차: **{analysis['consecutive_years']}년**",
                f"- 법인구분: **{analysis['corp_class_label']}**",
                f"- 최신 감사인 출처: **{analysis.get('latest_source', '')}**",
                f"- 예상 이벤트: **{event.get('headline', '')}**",
                f"- 신뢰도: **{analysis['confidence']}**",
                f"- 해석: {event.get('message', '')}",
            ]
        )
        if analysis.get("latest_source_note"):
            lines.append(f"- 출처 메모: {analysis['latest_source_note']}")

    coverage = payload.get("coverage", {})
    if coverage:
        lines.extend(
            [
                "",
                "## 공시 커버리지",
                "",
                f"- 병합 이력 행: {coverage.get('merged_rows', 0)}건",
                f"- 정기보고서 API 행: {coverage.get('periodic_report_api_rows', 0)}건",
                f"- 외부감사 감사보고서 공시 행: {coverage.get('external_audit_report_rows', 0)}건",
                f"- 특이공시 행: {coverage.get('special_issue_rows', 0)}건",
            ]
        )
        if coverage.get("missing_recent_years"):
            lines.append(f"- 최근 공시 미확인 연도: {', '.join(coverage['missing_recent_years'])}")
        for note in coverage.get("notes", []):
            lines.append(f"- {note}")

    lines.extend(
        [
            "",
            "## 감사인 이력",
            "",
            "| 사업연도 | 감사인 | 감사의견 | 출처 | 보고서/접수번호 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload.get("history", []):
        report_link = markdown_filing_label(row)
        lines.append(
            "| "
            + " | ".join(
                [
                    clean_md(row.get("bsns_year", "")),
                    clean_md(row.get("adtor", "")),
                    clean_md(row.get("adt_opinion", "")),
                    clean_md(row.get("source_detail", "")),
                    clean_md(report_link),
                ]
            )
            + " |"
        )

    issues = payload.get("special_issues", [])
    if issues:
        lines.extend(
            [
                "",
                "## 특이사항 공시",
                "",
                "| 접수일 | 유형 | 보고서명 | 제출인 | 원문 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for issue in issues:
            link = issue.get("rcept_no", "")
            if issue.get("rcept_url"):
                link = f"[{issue.get('rcept_no', '')}]({issue.get('rcept_url', '')})"
            lines.append(
                "| "
                + " | ".join(
                    [
                        clean_md(issue.get("rcept_dt", "")),
                        clean_md(issue.get("issue_type", "")),
                        clean_md(issue.get("report_nm", "")),
                        clean_md(issue.get("flr_nm", "")),
                        clean_md(link),
                    ]
                )
                + " |"
            )

    contracts = payload.get("service_contracts", [])
    if contracts:
        lines.extend(["", "## 감사용역 체결현황", "", "| 사업연도 | 감사인 | 계약보수 | 계약시간 | 실제보수 | 실제시간 |", "| --- | --- | --- | --- | --- | --- |"])
        for row in contracts[:8]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        clean_md(row.get("bsns_year", "")),
                        clean_md(row.get("adtor", "")),
                        clean_md(row.get("adt_cntrct_dtls_mendng") or row.get("mendng", "")),
                        clean_md(row.get("adt_cntrct_dtls_time") or row.get("tot_reqre_time", "")),
                        clean_md(row.get("real_exc_dtls_mendng", "")),
                        clean_md(row.get("real_exc_dtls_time", "")),
                    ]
                )
                + " |"
            )

    lines.extend(["", "## 확인 필요", ""])
    for item in analysis.get("follow_up", []):
        lines.append(f"- {item}")

    lines.extend(["", "## 주의", ""])
    for item in payload.get("disclaimers", []):
        lines.append(f"- {item}")
    return "\n".join(lines)


def clean_md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def markdown_filing_label(row: dict[str, Any]) -> str:
    label = str(row.get("report_nm") or row.get("rcept_no") or "").strip()
    receipt = str(row.get("rcept_no") or "").strip()
    url = str(row.get("rcept_url") or "").strip()
    if url and receipt:
        link = f"[{receipt}]({url})"
        return f"{label} {link}".strip()
    return label


def shorten(value: Any, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def int_or_zero(value: Any) -> int:
    match = re.search(r"(?:19|20)\d{2}", str(value))
    if match:
        return int(match.group(0))
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def digits_int(value: Any) -> int:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return int(digits) if digits else 0


def run_server(host: str, port: int, config: AppConfig) -> None:
    handler = make_handler(config)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Samil Audit Radar running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Samil Audit Radar.")


def make_handler(config: AppConfig) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            try:
                if parsed.path == "/healthz":
                    self.respond_json({"ok": True})
                    return
                if parsed.path == "/":
                    self.respond_html(INDEX_HTML)
                    return
                if parsed.path.startswith("/api/") and not allow_request(client_ip(self)):
                    self.respond_json(
                        {
                            "error": (
                                "Too many requests. Please wait a minute before "
                                "searching again."
                            )
                        },
                        status=429,
                    )
                    return
                if parsed.path == "/api/status":
                    self.respond_json(
                        {
                            "has_api_key": bool(config.api_key),
                            "demo": config.demo,
                            "cache_ttl_seconds": RESPONSE_CACHE_TTL_SECONDS,
                            "rate_limit": {
                                "window_seconds": RATE_LIMIT_WINDOW_SECONDS,
                                "max_requests": RATE_LIMIT_MAX_REQUESTS,
                            },
                        }
                    )
                    return
                if parsed.path == "/api/search":
                    q = first(query, "q")
                    if not q:
                        self.respond_json({"error": "검색어를 입력하세요."}, status=400)
                        return
                    cache_key = f"search:{q}"
                    self.respond_json(
                        cached(cache_key, lambda: search_companies(q, config, limit=10))
                    )
                    return
                if parsed.path == "/api/report":
                    company = first(query, "company")
                    corp_code = first(query, "corp_code") or None
                    if not company and not corp_code:
                        self.respond_json({"error": "기업을 선택하세요."}, status=400)
                        return
                    years = clamp_years(int_or_zero(first(query, "years") or DEFAULT_YEARS))
                    cache_key = f"report:{company}:{corp_code}:{years}"
                    self.respond_json(
                        cached(
                            cache_key,
                            lambda: build_report(
                                company,
                                config,
                                years=years,
                                corp_code=corp_code,
                            ),
                        )
                    )
                    return
                if parsed.path == "/api/demo":
                    self.respond_json(build_demo_report())
                    return
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            except Exception as exc:  # noqa: BLE001
                self.respond_json({"error": str(exc)}, status=500)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def respond_json(self, payload: Any, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def respond_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def client_ip(handler: BaseHTTPRequestHandler) -> str:
    forwarded = handler.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return handler.client_address[0]


def allow_request(ip_address: str) -> bool:
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with SERVER_LOCK:
        recent = [seen for seen in REQUEST_LOGS.get(ip_address, []) if seen >= cutoff]
        if len(recent) >= RATE_LIMIT_MAX_REQUESTS:
            REQUEST_LOGS[ip_address] = recent
            return False
        recent.append(now)
        REQUEST_LOGS[ip_address] = recent
    return True


def cached(cache_key: str, loader: Any) -> Any:
    now = time.time()
    with SERVER_LOCK:
        entry = RESPONSE_CACHE.get(cache_key)
        if entry and now - entry[0] <= RESPONSE_CACHE_TTL_SECONDS:
            return entry[1]
    payload = loader()
    with SERVER_LOCK:
        RESPONSE_CACHE[cache_key] = (now, payload)
    return payload


def first(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key, [""])
    return values[0].strip()


INDEX_HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Samil Audit Radar</title>
  <style>
    :root {
      color-scheme: light;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --ink: #102033;
      --muted: #5f728b;
      --line: #cbd8ea;
      --panel: #ffffff;
      --bg: #f4f8ff;
      --brand: #2563eb;
      --brand-dark: #1d4ed8;
      --brand-soft: #eaf2ff;
      --accent: #0891b2;
      --accent-soft: #e6f7fb;
      --shadow: 0 14px 40px rgba(37, 99, 235, 0.09);
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--ink); }
    header { background: #0f3f88; color: #fff; border-bottom: 1px solid #0b3474; padding: 20px 28px; }
    main { max-width: 1200px; margin: 0 auto; padding: 24px; }
    h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
    h2 { font-size: 16px; margin: 0 0 12px; }
    .subtitle { margin-top: 5px; color: #bfdbfe; font-size: 13px; }
    .toolbar { display: grid; grid-template-columns: 1fr 116px 92px; gap: 10px; margin: 0; }
    input, select, button { font: inherit; height: 44px; border: 1px solid var(--line); border-radius: 6px; padding: 0 12px; background: #fff; color: var(--ink); }
    input:focus, select:focus { outline: 2px solid rgba(37, 99, 235, 0.22); border-color: var(--brand); }
    button { background: var(--brand); color: #fff; border-color: var(--brand); cursor: pointer; font-weight: 700; }
    button:hover { background: var(--brand-dark); border-color: var(--brand-dark); }
    .grid { display: grid; grid-template-columns: 320px 1fr; gap: 18px; align-items: start; }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; box-shadow: var(--shadow); }
    .search-panel { margin-bottom: 18px; }
    .status { color: var(--muted); font-size: 13px; margin-top: 10px; min-height: 18px; }
    .company { width: 100%; display: block; text-align: left; background: #fff; color: var(--ink); border: 1px solid var(--line); margin-bottom: 8px; height: auto; padding: 11px 12px; border-radius: 6px; }
    .company:hover { border-color: var(--brand); background: var(--brand-soft); }
    .company strong { display: block; color: #12345d; }
    .company span { color: var(--muted); font-size: 12px; }
    .summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
    .metric { border: 1px solid #cfe0f6; border-radius: 8px; padding: 12px; background: #f8fbff; min-height: 86px; }
    .metric span { display: block; color: var(--muted); font-size: 12px; }
    .metric strong { display: block; margin-top: 8px; font-size: 18px; line-height: 1.25; }
    .event { border-left: 4px solid var(--brand); padding: 12px 14px; background: var(--brand-soft); margin-bottom: 14px; border-radius: 4px; }
    .event small { color: #46617f; }
    .coverage { border: 1px solid #cfe0f6; background: #f8fbff; border-radius: 8px; padding: 12px; margin-bottom: 14px; }
    .coverage-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 8px; }
    .coverage-grid div { border: 1px solid var(--line); border-radius: 6px; padding: 8px; background: #fff; }
    .coverage-grid span { display: block; color: var(--muted); font-size: 11px; }
    .coverage-grid strong { display: block; margin-top: 4px; font-size: 15px; }
    .report-meta { display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-bottom: 14px; }
    .report-meta span { display: block; color: var(--muted); font-size: 12px; }
    .report-meta strong { display: block; font-size: 18px; margin-top: 2px; }
    .badge { border: 1px solid #b9d5ff; border-radius: 999px; padding: 6px 10px; font-size: 12px; font-weight: 700; color: var(--brand-dark); background: var(--brand-soft); white-space: nowrap; }
    .badge.demo { color: #0e7490; background: var(--accent-soft); border-color: #a5e3ee; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--line); padding: 9px 8px; text-align: left; vertical-align: top; }
    th { color: #4b6380; font-weight: 700; background: #f2f7ff; }
    td small { color: var(--muted); display: block; margin-top: 3px; line-height: 1.35; }
    a { color: var(--brand-dark); font-weight: 700; text-decoration: none; }
    ul { margin: 8px 0 0 18px; padding: 0; }
    li { margin: 5px 0; }
    @media (max-width: 860px) {
      .toolbar, .grid, .summary, .coverage-grid { grid-template-columns: 1fr; }
      main { padding: 14px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Samil Audit Radar</h1>
    <div class="subtitle">OpenDART 기반 감사인 연속연차 및 다음 지정/선임 이벤트 추정</div>
  </header>
  <main>
    <section class="panel search-panel">
      <div class="toolbar">
        <input id="query" placeholder="기업명, 종목코드, 고유번호" value="삼성전자" />
        <select id="years">
          <option value="8">8년</option>
          <option value="10" selected>10년</option>
          <option value="12">12년</option>
        </select>
        <button id="searchBtn">검색</button>
      </div>
      <div class="status" id="status"></div>
    </section>
    <div class="grid" style="margin-top:16px;">
      <section class="panel">
        <h2>검색 결과</h2>
        <div id="results"></div>
      </section>
      <section class="panel">
        <h2>감사 레이더</h2>
        <div id="report">기업을 검색한 뒤 결과를 선택하세요.</div>
      </section>
    </div>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    $("searchBtn").addEventListener("click", search);
    $("query").addEventListener("keydown", (event) => { if (event.key === "Enter") search(); });

    async function getJson(url) {
      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || "요청 실패");
      return data;
    }

    async function search() {
      const q = $("query").value.trim();
      if (!q) return;
      $("status").textContent = "검색 중...";
      $("results").innerHTML = "";
      try {
        const rows = await getJson(`/api/search?q=${encodeURIComponent(q)}`);
        $("status").textContent = `${rows.length}개 후보`;
        $("results").innerHTML = rows.map(row => `<button class="company" data-code="${row.corp_code}" data-name="${row.corp_name}">
          <strong>${row.corp_name}</strong><span>고유번호 ${row.corp_code} · 종목코드 ${row.stock_code || "-"}</span>
        </button>`).join("") || "검색 결과가 없습니다.";
        document.querySelectorAll(".company").forEach(btn => btn.addEventListener("click", () => loadReport(btn.dataset.name, btn.dataset.code)));
      } catch (err) {
        $("status").textContent = err.message;
      }
    }

    async function loadReport(name, code) {
      $("status").textContent = "리포트 생성 중...";
      try {
        const years = $("years").value;
        const data = await getJson(`/api/report?company=${encodeURIComponent(name)}&corp_code=${encodeURIComponent(code)}&years=${years}`);
        renderReport(data);
        $("status").textContent = "완료";
      } catch (err) {
        $("status").textContent = err.message;
      }
    }

    function renderReport(data) {
      const a = data.analysis || {};
      const meta = reportMeta(data);
      if (a.status !== "ok") {
        $("report").innerHTML = `${meta}<p>${a.message || "데이터 없음"}</p>`;
        return;
      }
      const event = a.estimated_event || {};
      $("report").innerHTML = `
        ${meta}
        <div class="summary">
          <div class="metric"><span>현재 감사인</span><strong>${esc(a.current_auditor)}</strong></div>
          <div class="metric"><span>최신 사업연도</span><strong>${esc(a.latest_business_year)}</strong></div>
          <div class="metric"><span>연속연차</span><strong>${a.consecutive_years}년</strong></div>
          <div class="metric"><span>법인구분</span><strong>${esc(a.corp_class_label)}</strong></div>
        </div>
        <div class="event"><strong>${esc(event.headline)}</strong><br>${esc(event.message)}<br><small>신뢰도: ${esc(a.confidence)} · 최신 출처: ${esc(a.latest_source || "OpenDART")}</small></div>
        ${renderCoverage(data)}
        <h2>감사인 이력</h2>
        <table><thead><tr><th>사업연도</th><th>감사인</th><th>의견</th><th>출처</th><th>보고서</th></tr></thead>
        <tbody>${(data.history || []).map(row => `<tr><td>${esc(row.bsns_year)}</td><td>${esc(row.adtor)}</td><td>${esc(row.adt_opinion || "-")}</td><td>${esc(row.source_detail || "-")}<small>${esc(row.source_note || "")}</small></td><td>${filingLink(row)}</td></tr>`).join("")}</tbody></table>
        ${renderSpecialIssues(data)}
        <h2 style="margin-top:16px;">확인 필요</h2>
        <ul>${(a.follow_up || []).map(item => `<li>${esc(item)}</li>`).join("")}</ul>
      `;
    }

    function renderCoverage(data) {
      const c = data.coverage || {};
      if (!Object.keys(c).length) return "";
      const missing = (c.missing_recent_years || []).length
        ? `<p><strong>최근 공시 미확인 연도:</strong> ${esc(c.missing_recent_years.join(", "))}</p>`
        : "";
      const notes = (c.notes || []).map(note => `<li>${esc(note)}</li>`).join("");
      return `<div class="coverage">
        <div class="coverage-grid">
          <div><span>병합 이력</span><strong>${esc(c.merged_rows || 0)}건</strong></div>
          <div><span>정기보고서 API</span><strong>${esc(c.periodic_report_api_rows || 0)}건</strong></div>
          <div><span>외부감사 공시</span><strong>${esc(c.external_audit_report_rows || 0)}건</strong></div>
          <div><span>특이공시</span><strong>${esc(c.special_issue_rows || 0)}건</strong></div>
        </div>
        ${missing}
        ${notes ? `<ul>${notes}</ul>` : ""}
      </div>`;
    }

    function renderSpecialIssues(data) {
      const issues = data.special_issues || [];
      if (!issues.length) return "";
      return `<h2 style="margin-top:16px;">특이사항 공시</h2>
        <table><thead><tr><th>접수일</th><th>유형</th><th>보고서명</th><th>제출인</th><th>원문</th></tr></thead>
        <tbody>${issues.map(row => `<tr><td>${esc(row.rcept_dt)}</td><td>${esc(row.issue_type)}</td><td>${esc(row.report_nm)}</td><td>${esc(row.flr_nm)}</td><td>${filingLink(row)}</td></tr>`).join("")}</tbody></table>`;
    }

    function filingLink(row) {
      const report = row.report_nm || row.rcept_no || "-";
      if (!row.rcept_url) return esc(report);
      return `<a href="${esc(row.rcept_url)}" target="_blank" rel="noreferrer">${esc(report)}</a>`;
    }

    function reportMeta(data) {
      const company = data.company || {};
      const source = String(data.source || "");
      const isDemo = source.includes("Demo");
      const label = isDemo ? "데모 데이터" : "OpenDART 실데이터";
      return `<div class="report-meta">
        <div><span>조회 대상</span><strong>${esc(company.corp_name || "-")}</strong></div>
        <div class="badge ${isDemo ? "demo" : ""}">${label}</div>
      </div>`;
    }

    function esc(value) {
      return String(value == null ? "" : value).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    }
    function short(value) {
      const text = String(value || "").replace(/\s+/g, " ").trim();
      return text.length > 80 ? text.slice(0, 79) + "…" : text;
    }
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
