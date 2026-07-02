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
    audit_history = fetch_audit_history(corp["corp_code"], config, years=years)
    service_history = fetch_service_contracts(corp["corp_code"], config, years=min(years, 5))
    analysis = analyze_history(corp, audit_history, config.current_year)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "OpenDART public API",
        "company": corp,
        "history": audit_history,
        "service_contracts": service_history,
        "analysis": analysis,
        "disclaimers": [
            "Public DART data does not directly label free appointment, periodic designation, split designation, deferral, or all private-company eligibility facts.",
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
            bsns_year = item["bsns_year"]
            existing = rows_by_year.get(bsns_year)
            if existing is None or row_priority(item) > row_priority(existing):
                rows_by_year[bsns_year] = item

    history = list(rows_by_year.values())
    history.sort(key=lambda row: int_or_zero(row.get("bsns_year")), reverse=True)
    return history[:years]


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
            "message": "최근 사업보고서에서 감사인 이력을 찾지 못했습니다.",
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
    corp = {
        "corp_code": payload["corp_code"],
        "corp_name": payload["corp_name"],
        "stock_code": "",
        "modify_date": "",
        "corp_cls": payload["corp_cls"],
    }
    analysis = analyze_history(corp, payload["history"], date.today().year)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Demo fixture",
        "company": corp,
        "history": payload["history"],
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
                f"- 예상 이벤트: **{event.get('headline', '')}**",
                f"- 신뢰도: **{analysis['confidence']}**",
                f"- 해석: {event.get('message', '')}",
            ]
        )
    lines.extend(["", "## 감사인 이력", "", "| 사업연도 | 감사인 | 감사의견 | 강조사항 | 핵심감사사항 |", "| --- | --- | --- | --- | --- |"])
    for row in payload.get("history", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    clean_md(row.get("bsns_year", "")),
                    clean_md(row.get("adtor", "")),
                    clean_md(row.get("adt_opinion", "")),
                    clean_md(shorten(row.get("emphs_matter", ""))),
                    clean_md(shorten(row.get("core_adt_matter", ""))),
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
      --ink: #1f2933;
      --muted: #627386;
      --line: #d7dee8;
      --panel: #ffffff;
      --bg: #f5f7fb;
      --brand: #d04a02;
      --accent: #0f766e;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--ink); }
    header { background: #fff; border-bottom: 1px solid var(--line); padding: 18px 24px; }
    main { max-width: 1180px; margin: 0 auto; padding: 22px; }
    h1 { margin: 0; font-size: 22px; letter-spacing: 0; }
    h2 { font-size: 16px; margin: 0 0 12px; }
    .subtitle { margin-top: 4px; color: var(--muted); font-size: 13px; }
    .toolbar { display: grid; grid-template-columns: 1fr auto auto; gap: 10px; margin: 18px 0; }
    input, select, button { font: inherit; height: 42px; border: 1px solid var(--line); border-radius: 6px; padding: 0 12px; background: #fff; }
    button { background: var(--brand); color: #fff; border-color: var(--brand); cursor: pointer; font-weight: 700; }
    button.secondary { background: #fff; color: var(--ink); border-color: var(--line); }
    .grid { display: grid; grid-template-columns: 320px 1fr; gap: 16px; align-items: start; }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
    .status { color: var(--muted); font-size: 13px; margin-top: 8px; min-height: 18px; }
    .company { width: 100%; display: block; text-align: left; background: #fff; color: var(--ink); border: 1px solid var(--line); margin-bottom: 8px; height: auto; padding: 10px; border-radius: 6px; }
    .company strong { display: block; }
    .company span { color: var(--muted); font-size: 12px; }
    .summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
    .metric { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfcfe; min-height: 86px; }
    .metric span { display: block; color: var(--muted); font-size: 12px; }
    .metric strong { display: block; margin-top: 8px; font-size: 18px; line-height: 1.25; }
    .event { border-left: 4px solid var(--accent); padding: 12px 14px; background: #eefaf7; margin-bottom: 14px; border-radius: 4px; }
    .report-meta { display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-bottom: 14px; }
    .report-meta span { display: block; color: var(--muted); font-size: 12px; }
    .report-meta strong { display: block; font-size: 18px; margin-top: 2px; }
    .badge { border: 1px solid var(--line); border-radius: 999px; padding: 6px 10px; font-size: 12px; font-weight: 700; color: var(--accent); background: #eefaf7; white-space: nowrap; }
    .badge.demo { color: #8a4b00; background: #fff7ed; border-color: #fed7aa; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--line); padding: 9px 8px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 700; background: #fbfcfe; }
    ul { margin: 8px 0 0 18px; padding: 0; }
    li { margin: 5px 0; }
    @media (max-width: 860px) {
      .toolbar, .grid, .summary { grid-template-columns: 1fr; }
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
    <section class="panel">
      <div class="toolbar">
        <input id="query" placeholder="기업명, 종목코드, 고유번호" value="삼성전자" />
        <select id="years">
          <option value="8">8년</option>
          <option value="10" selected>10년</option>
          <option value="12">12년</option>
        </select>
        <button id="searchBtn">검색</button>
      </div>
      <button class="secondary" id="demoBtn">데모 보기</button>
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
    $("demoBtn").addEventListener("click", async () => renderReport(await getJson("/api/demo")));
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
        <div class="event"><strong>${esc(event.headline)}</strong><br>${esc(event.message)}<br><small>신뢰도: ${esc(a.confidence)}</small></div>
        <h2>감사인 이력</h2>
        <table><thead><tr><th>사업연도</th><th>감사인</th><th>의견</th><th>핵심감사사항</th></tr></thead>
        <tbody>${(data.history || []).map(row => `<tr><td>${esc(row.bsns_year)}</td><td>${esc(row.adtor)}</td><td>${esc(row.adt_opinion)}</td><td>${esc(short(row.core_adt_matter))}</td></tr>`).join("")}</tbody></table>
        <h2 style="margin-top:16px;">확인 필요</h2>
        <ul>${(a.follow_up || []).map(item => `<li>${esc(item)}</li>`).join("")}</ul>
      `;
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
      return String(value || "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
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
