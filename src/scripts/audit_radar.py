#!/usr/bin/env python3
"""OpenDART-based audit lead recommendation radar."""

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
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


BASE_URL = "https://opendart.fss.or.kr/api"
SARAMIN_JOB_SEARCH_URL = "https://oapi.saramin.co.kr/job-search"
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do"
REPORT_CODE_ANNUAL = "11011"
DEFAULT_YEARS = 10
MIN_YEARS = 4
MAX_YEARS = 12
MAX_RECOMMENDATIONS = 3
APPOINTMENT_DEADLINE_DAYS = 45
THREE_YEAR_APPOINTMENT_TERM = 3
PERIODIC_APPOINTMENT_YEARS = 6
GOVERNANCE_DEFERRAL_YEARS = 9
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 30
RESPONSE_CACHE_TTL_SECONDS = 60 * 60
ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
CACHE_DIR = PROJECT_ROOT / ".cache"
CORP_CACHE = CACHE_DIR / "corp_codes.json"
ENV_FILES = (PROJECT_ROOT / ".env.local", PROJECT_ROOT / ".env")
FIRM_CONTEXT_ENV = "AUDIT_FIRM_CONTEXT"
FIRM_CONTEXT_CANDIDATES = (
    PROJECT_ROOT / "firm_context.local.json",
    ROOT / "examples" / "firm_context.sample.json",
)
REQUEST_LOGS: dict[str, list[float]] = {}
RESPONSE_CACHE: dict[str, tuple[float, Any]] = {}
SERVER_LOCK = threading.Lock()
CORP_CACHE_LOCK = threading.Lock()
CORP_CLASS_LABELS = {
    "Y": "코스피(유가증권시장)",
    "K": "코스닥",
    "N": "코넥스",
    "E": "기타",
}
LISTED_CORP_CLASSES = {"Y", "K", "N"}
AUDIT_RULE_SOURCES = {
    "external_audit_act_9": {
        "label": "외감법 제9조",
        "title": "감사인의 자격 제한 등",
        "url": "https://www.law.go.kr/LSW//lsLawLinkInfo.do?chrClsCd=010202&lsId=001701&lsJoLnkSeq=1001153472&print=print",
    },
    "external_audit_act_10": {
        "label": "외감법 제10조",
        "title": "감사인의 선임",
        "url": "https://www.law.go.kr/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=900643609",
    },
    "external_audit_act_11": {
        "label": "외감법 제11조",
        "title": "증권선물위원회에 의한 감사인 지정 등",
        "url": "https://www.law.go.kr/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=900643907",
    },
    "external_audit_act_13": {
        "label": "외감법 제13조",
        "title": "감사인의 해임",
        "url": "https://www.law.go.kr/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=900177856",
    },
    "external_audit_decree_13": {
        "label": "외감법 시행령 제13조",
        "title": "감사인 선정 등",
        "url": "https://www.law.go.kr/LSW//lumLsLinkPop.do?chrClsCd=010202&lspttninfSeq=149564",
    },
    "external_audit_decree_16": {
        "label": "외감법 시행령 제16조",
        "title": "감사인 지정의 기준",
        "url": "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=271155",
    },
    "fss_2026_appointment": {
        "label": "금감원 2026 선임 유의사항",
        "title": "2026년 외부감사인 선임시 유의사항 안내",
        "url": "https://eiec.kdi.re.kr/policy/materialView.do?num=273968",
    },
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
FINANCIAL_NAME_KEYWORDS = (
    "금융",
    "은행",
    "보험",
    "증권",
    "카드",
    "캐피탈",
    "자산운용",
    "투자신탁",
    "저축은행",
    "손해보험",
    "생명보험",
)
DEFAULT_FIRM_CONTEXT = {
    "code": "samil_pwc",
    "label": "삼일PwC",
    "positioning": "상장사 외부감사와 내부회계관리제도에서 시작해 Tax, Deals, 산업 전문 자문으로 확장하는 회계법인",
    "auditor_aliases": ["삼일회계법인", "삼일PwC", "Samil PwC", "PricewaterhouseCoopers"],
    "strengths": [
        "외부감사 및 내부회계관리제도 감사",
        "상장사 감사인 교체·지정감사 종료 타이밍 리서치",
        "세무 리스크, Deals, 실사, 가치평가 등 인접 서비스 연결",
        "PwC 글로벌 네트워크와 산업별 전문 조직",
    ],
    "service_lines": [
        "외부감사 선임/전환 리서치",
        "내부회계관리제도 및 감사위원회 커뮤니케이션 점검",
        "세무 리스크 진단",
        "M&A·실사·가치평가 사전 스크리닝",
        "산업 전문 자문",
    ],
    "preferred_leads": [
        "상장사 중 감사인 교체 또는 자유선임 전환 타이밍이 가까운 회사",
        "감사보수·감사시간·매출추이 등 공개 데이터로 영업 논리를 만들 수 있는 회사",
        "감사위원회·감사·사외이사 등 선임 의사결정 후보군을 공개자료로 확인할 수 있는 회사",
        "삼일PwC의 Tax, Deals, 산업·글로벌 네트워크 자문으로 확장 가능한 감사 관계 후보",
    ],
    "industry_focus_keywords": ["금융", "바이오", "방산", "제조", "플랫폼"],
    "industry_focus_codes": ["21", "26", "30", "64", "65", "66"],
    "firm_people": [],
    "target_accounts": [],
    "erp_signals": {
        "relationship_tags": [],
        "restricted_corp_codes": [],
        "warm_intro_corp_codes": [],
        "priority_accounts": [],
    },
    "public_basis": [
        "OpenDART 공개 공시",
        "외부감사법상 감사인 선임·지정 제도",
        "삼일PwC 공개 서비스 영역과 상장사 정기보고서 주요정보",
    ],
}
DEFAULT_FIRM_PERSONA = DEFAULT_FIRM_CONTEXT["code"]
DEFAULT_JOB_SIGNAL_SEEDS = [
    "내부회계",
    "연결결산",
    "K-IFRS",
    "DART",
    "XBRL",
    "이전가격",
    "국제조세",
    "세무조사",
    "M&A",
    "Valuation",
    "FDD",
    "IPO",
    "SAP",
    "ERP",
]
JOB_SIGNAL_RULES = {
    "assurance_risk": {
        "label": "Assurance/Risk",
        "service": "내부통제·회계자문·공시자문",
        "keywords": [
            "내부회계",
            "ICFR",
            "SOX",
            "외부감사",
            "감사대응",
            "감사 대응",
            "K-IFRS",
            "IFRS",
            "연결결산",
            "DART",
            "XBRL",
            "주석",
            "회계감리",
            "공시",
        ],
    },
    "tax": {
        "label": "Tax",
        "service": "세무자문·국제조세·이전가격",
        "keywords": [
            "이전가격",
            "국제조세",
            "세무조사",
            "법인세",
            "부가가치세",
            "VAT",
            "관세",
            "해외법인",
            "BEPS",
            "원천세",
        ],
    },
    "deals": {
        "label": "Deals",
        "service": "재무자문·실사·가치평가",
        "keywords": [
            "M&A",
            "인수합병",
            "PMI",
            "Valuation",
            "가치평가",
            "FDD",
            "실사",
            "PPA",
            "영업권",
            "투자검토",
            "사업양수도",
        ],
    },
    "capital_markets": {
        "label": "Capital Markets",
        "service": "IPO·상장·자금조달 자문",
        "keywords": ["IPO", "상장준비", "IR", "증권신고서", "유상증자", "회사채", "CB", "BW"],
    },
    "finance_transformation": {
        "label": "Finance Transformation",
        "service": "재무시스템·결산 자동화 자문",
        "keywords": ["SAP", "ERP", "결산 자동화", "재무시스템", "EPM", "연결시스템", "BI"],
    },
}


@dataclass
class AppConfig:
    api_key: str | None
    current_year: int
    demo: bool = False
    saramin_key: str | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Samil Listed Audit Radar")
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

    recommend = sub.add_parser("recommend", help="Rank audit sales targets for a firm context")
    recommend.add_argument("query", help="Company keyword, stock code, or corp_code")
    recommend.add_argument("--years", type=int, default=DEFAULT_YEARS)
    recommend.add_argument("--limit", type=int, default=MAX_RECOMMENDATIONS)
    recommend.add_argument("--format", choices=("markdown", "json"), default="markdown")
    recommend.add_argument("--demo", action="store_true")

    jobs = sub.add_parser("jobs", help="Fetch Saramin job posts and extract Samil service demand signals")
    jobs.add_argument("--company", help="Optional company name to combine with each signal keyword")
    jobs.add_argument(
        "--seed",
        action="append",
        dest="seeds",
        help="Search seed keyword. Repeat to override defaults, e.g. --seed 내부회계 --seed 이전가격",
    )
    jobs.add_argument("--days", type=int, default=14, help="Published date lookback window")
    jobs.add_argument("--limit", type=int, default=30)
    jobs.add_argument("--stock", default="kospi kosdaq konex", help="Saramin stock filter")
    jobs.add_argument("--format", choices=("markdown", "json"), default="markdown")
    jobs.add_argument("--demo", action="store_true")

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
        saramin_key=load_saramin_key(),
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
    if args.command == "recommend":
        payload = build_recommendations(
            args.query,
            config,
            years=clamp_years(args.years),
            limit=args.limit,
        )
        print(render_recommendations(payload, args.format))
        return 0
    if args.command == "jobs":
        payload = build_job_opportunities(
            config,
            company=args.company,
            seeds=args.seeds or DEFAULT_JOB_SIGNAL_SEEDS,
            days=args.days,
            limit=args.limit,
            stock=args.stock,
        )
        print(render_job_opportunities(payload, args.format))
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


def load_env_value(key_names: tuple[str, ...]) -> str | None:
    key_set = set(key_names)
    for key_name in key_names:
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
            if name.strip() in key_set:
                cleaned = value.strip().strip('"').strip("'")
                if cleaned:
                    return cleaned
    return None


def load_api_key() -> str | None:
    return load_env_value(("DART_API_KEY", "OPEN_DART_API_KEY", "OPENDART_API_KEY"))


def load_saramin_key() -> str | None:
    return load_env_value(("SARAMIN_ACCESS_KEY", "SARAMIN_API_KEY"))


def require_key(config: AppConfig) -> str:
    if config.demo:
        return "DEMO"
    if not config.api_key:
        raise RuntimeError(
            "DART_API_KEY is not set. Set it in your shell or create .env.local."
        )
    return config.api_key


def require_saramin_key(config: AppConfig) -> str:
    if config.demo:
        return "DEMO"
    if not config.saramin_key:
        raise RuntimeError(
            "SARAMIN_ACCESS_KEY is not set. Apply for a Saramin API access-key and set it in your shell or .env.local."
        )
    return config.saramin_key


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


def saramin_get(config: AppConfig, params: dict[str, Any]) -> dict[str, Any]:
    access_key = require_saramin_key(config)
    query = {key: str(value) for key, value in params.items() if value not in (None, "")}
    query["access-key"] = access_key
    url = f"{SARAMIN_JOB_SEARCH_URL}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def build_job_opportunities(
    config: AppConfig,
    *,
    company: str | None,
    seeds: list[str],
    days: int,
    limit: int,
    stock: str,
) -> dict[str, Any]:
    seeds = clean_job_seeds(seeds)
    days = max(1, min(90, days))
    limit = max(1, min(110, limit))
    if config.demo:
        jobs = demo_job_posts()
    else:
        jobs = fetch_saramin_job_posts(config, company=company, seeds=seeds, days=days, limit=limit, stock=stock)
    scored = [score_job_post(job) for job in jobs]
    scored = [job for job in scored if job.get("signals")]
    scored.sort(
        key=lambda item: (
            -int_or_zero(item.get("opportunity_score")),
            str(item.get("company", "")),
            str(item.get("title", "")),
        )
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Saramin Job Search API" if not config.demo else "Demo fixture",
        "company": company or "",
        "days": days,
        "stock": stock,
        "seeds": seeds,
        "jobs": scored[:limit],
        "notes": [
            "사람인 API의 keywords 검색은 기업명, 공고명, 업직종, 직무내용을 대상으로 합니다.",
            "채용공고 신호는 서비스 수요의 초기 징후이며, 영업 제안 전 원문과 독립성 검토가 필요합니다.",
        ],
    }


def clean_job_seeds(seeds: list[str]) -> list[str]:
    cleaned = []
    seen = set()
    for seed in seeds:
        item = re.sub(r"\s+", " ", str(seed or "")).strip()
        if not item:
            continue
        key = normalize_search(item)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return cleaned or list(DEFAULT_JOB_SIGNAL_SEEDS)


def fetch_saramin_job_posts(
    config: AppConfig,
    *,
    company: str | None,
    seeds: list[str],
    days: int,
    limit: int,
    stock: str,
) -> list[dict[str, Any]]:
    published_min = (date.today() - timedelta(days=days)).isoformat()
    per_seed_count = min(30, max(10, limit))
    jobs_by_id: dict[str, dict[str, Any]] = {}
    for seed in seeds:
        keyword = f"{company} {seed}".strip() if company else seed
        payload = saramin_get(
            config,
            {
                "keywords": keyword,
                "stock": stock,
                "published_min": published_min,
                "sort": "pd",
                "count": per_seed_count,
                "fields": "posting-date expiration-date keyword-code count",
            },
        )
        for raw_job in as_list(((payload.get("jobs") or {}).get("job"))):
            job = normalize_saramin_job(raw_job, seed)
            if not job.get("id"):
                continue
            existing = jobs_by_id.get(job["id"])
            if existing is None:
                jobs_by_id[job["id"]] = job
            else:
                merge_job_seed(existing, seed)
    return list(jobs_by_id.values())


def normalize_saramin_job(raw_job: dict[str, Any], seed: str) -> dict[str, Any]:
    company_detail = ((raw_job.get("company") or {}).get("detail") or {})
    position = raw_job.get("position") or {}
    industry = position.get("industry") or {}
    location = position.get("location") or {}
    job_type = position.get("job-type") or {}
    job = {
        "id": str(raw_job.get("id", "")).strip(),
        "url": str(raw_job.get("url", "")).strip(),
        "active": str(raw_job.get("active", "")).strip(),
        "company": str(company_detail.get("name", "")).strip(),
        "company_url": str(company_detail.get("href", "")).strip(),
        "title": str(position.get("title", "")).strip(),
        "industry": str(industry.get("name", "")).strip(),
        "location": str(location.get("name", "")).strip(),
        "job_type": str(job_type.get("name", "")).strip(),
        "posting_timestamp": str(raw_job.get("posting-timestamp", "")).strip(),
        "posting_date": str(raw_job.get("posting-date", "")).strip(),
        "expiration_date": str(raw_job.get("expiration-date", "")).strip(),
        "keyword_codes": raw_job.get("keyword-code", ""),
        "search_seeds": [],
    }
    merge_job_seed(job, seed)
    return job


def merge_job_seed(job: dict[str, Any], seed: str) -> None:
    seeds = job.setdefault("search_seeds", [])
    if seed and seed not in seeds:
        seeds.append(seed)


def score_job_post(job: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(
        str(value or "")
        for value in (
            job.get("company"),
            job.get("title"),
            job.get("industry"),
            job.get("location"),
            " ".join(as_text_list(job.get("search_seeds"))),
        )
    )
    compact_text = normalize_search(text)
    signals = []
    all_matches = []
    for code, rule in JOB_SIGNAL_RULES.items():
        matches = []
        for keyword in rule["keywords"]:
            if keyword_matches_text(keyword, text, compact_text):
                matches.append(keyword)
        matches = unique_text(matches)
        if not matches:
            continue
        score = min(100, 25 + len(matches) * 15)
        signals.append(
            {
                "code": code,
                "label": rule["label"],
                "service": rule["service"],
                "score": score,
                "matched_keywords": matches,
            }
        )
        all_matches.extend(matches)
    signals.sort(key=lambda item: (-int_or_zero(item.get("score")), str(item.get("label", ""))))
    top_signal = signals[0] if signals else {}
    enriched = dict(job)
    enriched["signals"] = signals
    enriched["matched_keywords"] = unique_text(all_matches)
    enriched["recommended_service"] = top_signal.get("service", "")
    enriched["recommended_path"] = top_signal.get("label", "")
    enriched["opportunity_score"] = min(100, sum(int_or_zero(signal.get("score")) for signal in signals[:2]))
    return enriched


def keyword_matches_text(keyword: str, text: str, compact_text: str) -> bool:
    keyword_text = str(keyword or "").strip()
    if not keyword_text:
        return False
    return keyword_text.lower() in text.lower() or normalize_search(keyword_text) in compact_text


def unique_text(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        item = str(value or "").strip()
        key = normalize_search(item)
        if not item or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def demo_job_posts() -> list[dict[str, Any]]:
    rows = [
        {
            "id": "demo-icfr",
            "url": "https://example.com/jobs/demo-icfr",
            "active": "1",
            "company": "샘플테크",
            "title": "K-IFRS 연결결산 및 내부회계관리제도 담당자",
            "industry": "반도체·전자",
            "location": "서울",
            "job_type": "정규직",
            "posting_date": date.today().isoformat(),
            "expiration_date": "",
            "search_seeds": ["연결결산", "내부회계", "K-IFRS"],
        },
        {
            "id": "demo-tax",
            "url": "https://example.com/jobs/demo-tax",
            "active": "1",
            "company": "샘플글로벌",
            "title": "국제조세 및 이전가격 문서화 담당",
            "industry": "플랫폼",
            "location": "서울",
            "job_type": "정규직",
            "posting_date": date.today().isoformat(),
            "expiration_date": "",
            "search_seeds": ["국제조세", "이전가격"],
        },
        {
            "id": "demo-deals",
            "url": "https://example.com/jobs/demo-deals",
            "active": "1",
            "company": "샘플홀딩스",
            "title": "M&A 투자검토 및 Valuation 담당",
            "industry": "지주회사",
            "location": "서울",
            "job_type": "정규직",
            "posting_date": date.today().isoformat(),
            "expiration_date": "",
            "search_seeds": ["M&A", "Valuation", "투자검토"],
        },
    ]
    return rows


def load_corp_codes(config: AppConfig, *, refresh: bool = False) -> list[dict[str, str]]:
    if config.demo:
        return load_demo_companies()
    if CORP_CACHE.is_file() and not refresh:
        return json.loads(CORP_CACHE.read_text(encoding="utf-8"))

    with CORP_CACHE_LOCK:
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
        if not is_listed_search_candidate(company):
            continue
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


def is_listed_search_candidate(company: dict[str, str]) -> bool:
    return bool(str(company.get("stock_code", "")).strip()) or company.get("corp_cls") in LISTED_CORP_CLASSES


def is_listed_company(corp: dict[str, Any]) -> bool:
    corp_cls = str(corp.get("corp_cls", "")).strip()
    stock_code = str(corp.get("stock_code", "")).strip()
    return corp_cls in LISTED_CORP_CLASSES or bool(stock_code)


def normalize_search(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def resolve_company(company: str, config: AppConfig, corp_code: str | None = None) -> dict[str, str]:
    if config.demo:
        return load_demo_companies()[0]
    if corp_code:
        return {"corp_code": corp_code, "corp_name": company, "stock_code": "", "modify_date": ""}
    matches = search_companies(company, config, limit=5)
    if not matches:
        raise RuntimeError(f"No company matched: {company}")
    return matches[0]


def fetch_company_profile(corp_code: str, config: AppConfig) -> dict[str, Any]:
    if config.demo:
        return {}
    try:
        payload = dart_get("company", config, {"corp_code": corp_code})
    except RuntimeError as exc:
        return {"error": str(exc)}

    keep_fields = (
        "corp_code",
        "corp_name",
        "stock_name",
        "stock_code",
        "corp_cls",
        "induty_code",
        "est_dt",
        "acc_mt",
        "adres",
        "hm_url",
    )
    return {field: str(payload.get(field, "")).strip() for field in keep_fields}


def enrich_company(corp: dict[str, str], profile: dict[str, Any]) -> dict[str, str]:
    if not profile or profile.get("error"):
        return dict(corp)
    enriched = dict(corp)
    for key in ("corp_cls", "stock_code", "stock_name", "induty_code", "acc_mt"):
        value = str(profile.get(key, "")).strip()
        if value and not enriched.get(key):
            enriched[key] = value
    return enriched


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
    company_profile = fetch_company_profile(corp["corp_code"], config)
    corp = enrich_company(corp, company_profile)
    if not is_listed_company(corp):
        raise RuntimeError("현재 버전은 코스피·코스닥·코넥스 상장사만 지원합니다.")
    structured_history = fetch_audit_history(corp["corp_code"], config, years=years)
    disclosure_bundle = fetch_external_audit_disclosures(corp["corp_code"], config, years=years)
    audit_history = merge_audit_sources(
        structured_history,
        disclosure_bundle["history"],
        years=years,
    )
    service_history = fetch_service_contracts(corp["corp_code"], config, years=min(years, 5))
    executive_history = fetch_executive_status(corp["corp_code"], config, years=min(years, 3))
    analysis = analyze_history(corp, audit_history, config.current_year)
    attach_event_schedule(
        corp,
        company_profile,
        analysis,
        as_of=date.today(),
        executives=executive_history,
        special_issues=disclosure_bundle["special_issues"],
    )
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
    sales_strategy = build_sales_strategy(
        corp,
        analysis,
        coverage,
        disclosure_bundle["special_issues"],
        company_profile,
    )
    firm_persona = get_firm_persona()
    lead_recommendation = build_lead_recommendation(
        firm_persona,
        corp,
        analysis,
        sales_strategy,
        coverage,
        disclosure_bundle["special_issues"],
        service_history,
        company_profile,
    )
    analysis["sales_strategy"] = sales_strategy
    analysis["lead_recommendation"] = lead_recommendation
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "OpenDART public API + local firm context",
        "firm_persona": firm_persona,
        "company": corp,
        "company_profile": company_profile,
        "history": audit_history,
        "history_sources": {
            "periodic_report_api": structured_history,
            "external_audit_reports": disclosure_bundle["history"],
        },
        "audit_disclosures": disclosure_bundle["filings"],
        "special_issues": disclosure_bundle["special_issues"],
        "coverage": coverage,
        "service_contracts": service_history,
        "executives": executive_history,
        "analysis": analysis,
        "sales_strategy": sales_strategy,
        "lead_recommendation": lead_recommendation,
        "disclaimers": [
            "This version is scoped to listed companies with stock codes in OpenDART.",
            "Public DART data does not directly label free appointment, periodic designation, split designation, or deferral facts.",
            "Recommendations combine public filing signals with the configured firm context; they are not audit acceptance, independence, conflict, or quality-control decisions.",
            "Executive education is not a stable structured OpenDART field; it may appear only inside the main career text and should not be over-interpreted.",
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
    contracts_by_year: dict[str, dict[str, Any]] = {}
    start_year = config.current_year - 1
    fetch_years = list(range(start_year, start_year - years, -1))
    for year, rows in fetch_yearly_payloads("adtServcCnclsSttus", corp_code, config, fetch_years):
        for row in normalize_service_contract_rows(rows, year):
            bsns_year = str(row.get("bsns_year", "")).strip()
            if not bsns_year:
                continue
            existing = contracts_by_year.get(bsns_year)
            if existing is None or service_contract_priority(row) > service_contract_priority(existing):
                contracts_by_year[bsns_year] = row
    contracts = list(contracts_by_year.values())
    contracts.sort(key=lambda row: int_or_zero(row.get("bsns_year")), reverse=True)
    return contracts[:years]


def normalize_service_contract_rows(rows: list[dict[str, Any]], report_year: int) -> list[dict[str, Any]]:
    valid = [row for row in rows if is_meaningful_value(row.get("adtor", ""))]
    if not valid:
        return []
    term_numbers = [period_term_number(row.get("bsns_year", "")) for row in valid]
    term_numbers = [number for number in term_numbers if number is not None]
    max_term = max(term_numbers) if term_numbers else None
    normalized = []
    for row in valid:
        term_number = period_term_number(row.get("bsns_year", ""))
        inferred_year = report_year
        if max_term is not None and term_number is not None:
            inferred_year = report_year - (max_term - term_number)
        if inferred_year > report_year or inferred_year < report_year - 3:
            continue
        item = normalize_disclosure_row(row, inferred_year)
        item["source_report_year"] = str(report_year)
        item["rcept_url"] = dart_viewer_url(item.get("rcept_no", ""))
        normalized.append(item)
    return normalized


def period_term_number(value: Any) -> int | None:
    match = re.search(r"제\s*([0-9]+)\s*기", str(value or ""))
    return int(match.group(1)) if match else None


def service_contract_priority(row: dict[str, Any]) -> int:
    score = 0
    if str(row.get("source_report_year", "")) == str(row.get("bsns_year", "")):
        score += 4
    if row.get("real_exc_dtls_mendng") or row.get("real_exc_dtls_time"):
        score += 2
    if row.get("rcept_no"):
        score += 1
    return score


def fetch_executive_status(corp_code: str, config: AppConfig, *, years: int) -> list[dict[str, Any]]:
    if config.demo:
        return demo_executives()
    start_year = config.current_year - 1
    fetch_years = list(range(start_year, start_year - years, -1))
    for year, rows in fetch_yearly_payloads("exctvSttus", corp_code, config, fetch_years):
        normalized = [normalize_executive_row(row, year) for row in rows if is_meaningful_value(row.get("nm", ""))]
        if normalized:
            return rank_executive_rows(normalized)[:20]
    return []


def normalize_executive_row(row: dict[str, Any], business_year: int) -> dict[str, Any]:
    item = normalize_disclosure_row(row, business_year)
    item["decision_role_signal"] = executive_decision_role_signal(item)
    item["rcept_url"] = dart_viewer_url(item.get("rcept_no", ""))
    return item


def executive_decision_role_signal(row: dict[str, Any]) -> str:
    text = normalize_search(
        " ".join(
            [
                str(row.get("ofcps", "")),
                str(row.get("chrg_job", "")),
                str(row.get("main_career", "")),
            ]
        )
    )
    if "감사위원" in text:
        return "감사위원회 후보"
    if "감사" in text:
        return "감사 후보"
    if "사외이사" in text:
        return "사외이사 후보"
    if "대표이사" in text or "ceo" in text:
        return "대표이사"
    return ""


def rank_executive_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def priority(row: dict[str, Any]) -> tuple[int, str]:
        signal = row.get("decision_role_signal", "")
        score = 0
        if signal in {"감사위원회 후보", "감사 후보"}:
            score += 4
        elif signal == "사외이사 후보":
            score += 3
        elif signal == "대표이사":
            score += 2
        if str(row.get("rgist_exctv_at", "")).strip() == "등기임원":
            score += 1
        return -score, str(row.get("nm", ""))

    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("nm", "")).strip(),
            str(row.get("ofcps", "")).strip(),
            str(row.get("chrg_job", "")).strip(),
        )
        if key not in deduped:
            deduped[key] = row
    return sorted(deduped.values(), key=priority)


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
        "status": "out_of_listed_scope",
        "label": CORP_CLASS_LABELS.get(corp_cls, "기타/알 수 없음"),
        "reason": "현재 버전은 코스피·코스닥·코넥스 상장사만 분석 대상으로 삼습니다.",
    }


def estimate_event(
    latest_run: dict[str, Any],
    previous_run: dict[str, Any] | None,
    subject: dict[str, str],
) -> dict[str, Any]:
    length = latest_run["length"]
    subject_status = subject["status"]
    if subject_status == "out_of_listed_scope":
        return {
            "type": "out_of_listed_scope",
            "headline": "상장사 범위 밖: 분석 제외",
            "confidence": "low",
            "years_remaining": None,
            "message": "현재 버전은 상장사 공개 데이터 기반 영업 레이더로 제한되어 있습니다.",
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
        checks.append("코넥스 또는 기타 예외의 주기적 지정 적용 여부 확인")
    if event["type"] == "possible_designated_cycle":
        checks.append("지정감사 종료 후 최초 자유선임 시 전기 지정감사인 배제 규정 적용 여부 확인")
    if event["type"] in {"six_year_threshold_reached", "approaching_six_year_threshold"}:
        checks.append("주기적 지정 유예 또는 분산지정 적용 여부 확인")
    return checks


def attach_event_schedule(
    corp: dict[str, str],
    company_profile: dict[str, Any],
    analysis: dict[str, Any],
    *,
    as_of: date,
    executives: list[dict[str, Any]] | None = None,
    special_issues: list[dict[str, Any]] | None = None,
) -> None:
    analysis["applicable_rules"] = build_audit_applicability(
        corp,
        company_profile,
        analysis,
        as_of=as_of,
        executives=executives or [],
        special_issues=special_issues or [],
    )
    schedule = build_audit_event_schedule(corp, company_profile, analysis, as_of=as_of)
    analysis["event_schedule"] = schedule
    analysis["next_timeline_event"] = next_timeline_event(schedule)


def build_audit_applicability(
    corp: dict[str, str],
    company_profile: dict[str, Any],
    analysis: dict[str, Any],
    *,
    as_of: date,
    executives: list[dict[str, Any]],
    special_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if analysis.get("status") != "ok":
        return []

    corp_cls = str(analysis.get("corp_class") or corp.get("corp_cls") or "").strip()
    corp_label = CORP_CLASS_LABELS.get(corp_cls, "알 수 없음")
    is_listed = corp_cls in LISTED_CORP_CLASSES
    subject = analysis.get("periodic_subject_estimate") or {}
    event = analysis.get("estimated_event") or {}
    current_run = analysis.get("current_run") or {}
    fiscal_end_month = resolve_fiscal_end_month(corp, company_profile)
    latest_year = int_or_zero(analysis.get("latest_business_year"))
    next_fiscal_year = latest_year + 1 if latest_year else as_of.year
    appointment_date = appointment_deadline(next_fiscal_year, fiscal_end_month)
    audit_committee = audit_committee_evidence(executives)
    rules: list[dict[str, Any]] = []

    if is_listed:
        rules.append(
            audit_rule_card(
                "listed_registered_auditor",
                "상장회사 감사인 자격",
                "applies",
                "적용",
                f"OpenDART 법인구분: {corp_label}. 상장회사 감사인 자격 제한을 적용합니다.",
                "후보 감사인이 금융위원회 등록 상장회사 감사인인지 확인하세요.",
                ["external_audit_act_9", "fss_2026_appointment"],
            )
        )
        rules.append(
            audit_rule_card(
                "three_year_same_auditor",
                "3개 사업연도 동일 감사인 선임",
                "applies",
                "적용",
                f"{corp_label} 상장회사이므로 동일 감사인을 연속 3개 사업연도로 선임하는 기준을 적용합니다.",
                "3년 계약 구간의 시작·종료연도와 재선임/교체/지정 여부를 원문 공시로 대조하세요.",
                ["external_audit_act_10", "fss_2026_appointment"],
            )
        )
    else:
        rules.append(
            audit_rule_card(
                "listed_scope",
                "상장회사 기준",
                "not_applicable",
                "분석 제외",
                f"OpenDART 법인구분이 {corp_label}로 확인되어 현재 상장회사 전용 판단 범위에서 벗어납니다.",
                "비상장·금융회사·대형비상장 기준은 별도 체크리스트로 확인하세요.",
                ["external_audit_act_10"],
            )
        )

    rules.append(
        audit_rule_card(
            "appointment_deadline",
            "감사인 선임기한",
            "review",
            "기본 적용·예외 확인",
            (
                f"{next_fiscal_year} 사업연도 기준 기본 선임기한은 {appointment_date.isoformat()}로 계산됩니다. "
                "다만 감사위원회 의무설치 회사는 사업연도 개시 전 선임 기준을 우선 확인해야 합니다."
            ),
            "정관, 지배구조보고서, 감사위원회 설치 의무 여부를 확인해 실제 마감일을 확정하세요.",
            ["external_audit_act_10", "fss_2026_appointment"],
        )
    )

    if audit_committee:
        selection_evidence = f"{audit_committee} 감사위원회 설치 회사는 감사위원회가 외부감사인을 선정하는 절차를 우선 검토합니다."
        selection_action = "감사위원회 의사록, 후보평가표, 대면회의 기록과 선임보고 자료를 확인하세요."
        selection_status = "likely"
        selection_label = "감사위원회 절차 가능"
    else:
        selection_evidence = (
            "공개 임원 데이터만으로 감사위원회 설치 여부를 확정하지 못했습니다. "
            "감사위원회 미설치 상장회사라면 감사가 감사인선임위원회 승인을 받아 선정하는 절차를 확인해야 합니다."
        )
        selection_action = "사업보고서 지배구조, 감사위원회/감사 구성, 감사인선임위원회 승인 여부를 확인하세요."
        selection_status = "review"
        selection_label = "확인 필요"
    rules.append(
        audit_rule_card(
            "selection_body",
            "감사인 선정권자",
            selection_status,
            selection_label,
            selection_evidence,
            selection_action,
            ["external_audit_act_10", "external_audit_decree_13"],
        )
    )

    rules.append(
        audit_rule_card(
            "selection_criteria",
            "선정 기준 문서화",
            "applies" if is_listed else "review",
            "적용" if is_listed else "확인 필요",
            "감사시간, 감사인력, 감사보수, 감사계획의 적정성 및 감사인의 독립성·전문성을 기준으로 문서화해야 합니다.",
            "후보별 감사계획/보수/투입시간 비교표와 전기감사인 의견진술 내용을 선임 파일에 묶어 확인하세요.",
            ["external_audit_decree_13", "fss_2026_appointment"],
        )
    )

    length = int_or_zero(current_run.get("length"))
    if subject.get("status") == "likely_subject" and length >= PERIODIC_APPOINTMENT_YEARS:
        periodic_status = "warning"
        periodic_label = "주기적 지정 후보"
        periodic_evidence = (
            f"상장회사이고 현재 감사인이 공개 이력상 {length}개 사업연도 연속 확인되어 "
            "6년 자유선임 후 지정감사 검토 기준에 도달했거나 임박했을 가능성이 큽니다."
        )
    elif subject.get("status") == "likely_subject":
        periodic_status = "likely"
        periodic_label = "모니터링"
        periodic_evidence = (
            f"상장회사로 주기적 지정 제도 적용 대상 가능성이 높습니다. "
            f"현재 공개 이력상 동일 감사인 연속 {length}개 사업연도로 계산됩니다."
        )
    else:
        periodic_status = "review"
        periodic_label = "예외 확인"
        periodic_evidence = subject.get("reason") or "주기적 지정 적용 대상 여부를 별도 확인해야 합니다."
    rules.append(
        audit_rule_card(
            "periodic_designation",
            "주기적 지정 6년+3년",
            periodic_status,
            periodic_label,
            periodic_evidence,
            "금감원 지정 사전/본통지, 유예·분산지정, 지정감사 종료연도 여부를 원문 자료로 확인하세요.",
            ["external_audit_act_11", "external_audit_decree_16"],
        )
    )

    if event.get("type") == "possible_designated_cycle":
        rules.append(
            audit_rule_card(
                "designation_exit",
                "지정감사 종료 후 자유선임 전환",
                "review",
                "확인 필요",
                "이전 감사인이 6년 이상 이어진 뒤 현재 감사인으로 바뀐 패턴이라 현재 감사인이 지정감사인일 수 있습니다.",
                "지정감사 3년 종료 여부와 지정 사업연도 이후 최초 사업연도 전기 지정감사인 배제 규정을 확인하세요.",
                ["external_audit_act_11"],
            )
        )

    if special_issues:
        issue_labels = ", ".join(short(str(item.get("issue_type") or item.get("report_nm") or ""), 24) for item in special_issues[:3])
        rules.append(
            audit_rule_card(
                "designation_trigger_review",
                "직권지정 사유 점검",
                "warning",
                "주의",
                f"감사보고서 제출 지연·정정 등 특이공시 {len(special_issues)}건이 확인되었습니다. {issue_labels}",
                "해당 공시가 감사인 지정 사유, 재무제표 제출의무 위반, 감사시간 미달 등과 연결되는지 원문으로 확인하세요.",
                ["external_audit_act_11", "external_audit_act_13"],
            )
        )

    return rules


def audit_rule_card(
    rule_id: str,
    title: str,
    status: str,
    judgement: str,
    evidence: str,
    next_action: str,
    source_ids: list[str],
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "title": title,
        "status": status,
        "judgement": judgement,
        "evidence": evidence,
        "next_action": next_action,
        "sources": source_refs(source_ids),
    }


def source_refs(source_ids: list[str]) -> list[dict[str, str]]:
    refs = []
    for source_id in source_ids:
        source = AUDIT_RULE_SOURCES.get(source_id)
        if not source:
            continue
        refs.append({"id": source_id, **source})
    return refs


def audit_committee_evidence(executives: list[dict[str, Any]]) -> str:
    for row in executives:
        text = " ".join(str(row.get(key, "")) for key in ("ofcps", "chrg_job", "main_career", "rm"))
        if "감사위원" in text or "감사위원회" in text:
            name = str(row.get("nm", "")).strip()
            return f"임원 현황에서 {name or '임원'}의 감사위원회 관련 문구가 확인됩니다."
    return ""


def build_audit_event_schedule(
    corp: dict[str, str],
    company_profile: dict[str, Any],
    analysis: dict[str, Any],
    *,
    as_of: date,
) -> list[dict[str, Any]]:
    if analysis.get("status") != "ok":
        return []

    current_run = analysis.get("current_run") or {}
    previous_run = analysis.get("previous_run") or {}
    subject = analysis.get("periodic_subject_estimate") or {}
    event = analysis.get("estimated_event") or {}
    fiscal_end_month = resolve_fiscal_end_month(corp, company_profile)
    events: list[dict[str, Any]] = []

    term_event = build_three_year_term_event(current_run, fiscal_end_month, as_of)
    if term_event:
        events.append(term_event)

    designation_exit = build_designation_exit_event(
        current_run,
        previous_run,
        fiscal_end_month,
        subject,
        event,
        as_of,
    )
    if designation_exit:
        events.append(designation_exit)

    periodic_event = build_periodic_designation_event(
        current_run,
        fiscal_end_month,
        subject,
        as_of,
        base_years=PERIODIC_APPOINTMENT_YEARS,
        kind="periodic_designation",
        title="주기적 지정 후보",
        detail_suffix="6년 자유선임으로 추정되는 경우 금융당국 지정감사 후보가 됩니다.",
    )
    if periodic_event:
        events.append(periodic_event)

    deferred_event = build_periodic_designation_event(
        current_run,
        fiscal_end_month,
        subject,
        as_of,
        base_years=GOVERNANCE_DEFERRAL_YEARS,
        kind="governance_deferral",
        title="우수기업 유예 적용 시 후보",
        detail_suffix="회계·감사 지배구조 우수기업 유예가 인정되는 경우의 9년 자율선임 시나리오입니다.",
    )
    if deferred_event:
        events.append(deferred_event)

    events.sort(key=lambda item: (item.get("event_date", ""), item.get("priority_order", 99)))
    for index, item in enumerate(events, start=1):
        item["order"] = index
        item.pop("priority_order", None)
    return events


def build_three_year_term_event(
    current_run: dict[str, Any],
    fiscal_end_month: int,
    as_of: date,
) -> dict[str, Any] | None:
    start_year = int_or_zero(current_run.get("start_year"))
    end_year = int_or_zero(current_run.get("end_year"))
    auditor = str(current_run.get("auditor", "")).strip()
    if not start_year or not end_year:
        return None

    years_into_run = max(0, end_year - start_year)
    block_start = start_year + (years_into_run // THREE_YEAR_APPOINTMENT_TERM) * THREE_YEAR_APPOINTMENT_TERM
    block_end = block_start + THREE_YEAR_APPOINTMENT_TERM - 1
    review_fiscal_year = block_end + 1
    event_date = appointment_deadline(review_fiscal_year, fiscal_end_month)
    days_remaining = (event_date - as_of).days
    return audit_timeline_event(
        kind="three_year_term",
        title="3개 사업연도 동일 감사인 계약 검토",
        event_date=event_date,
        fiscal_year=review_fiscal_year,
        days_remaining=days_remaining,
        detail=(
            f"{auditor or '현재 감사인'} 기준 {block_start}~{block_end} 사업연도 3년 구간 이후 "
            "재선임, 교체, 지정 여부를 확인해야 합니다."
        ),
        basis="주권상장법인 등은 연속 3개 사업연도 동일 감사인 선임 의무가 있습니다.",
        confidence="medium",
        source_ids=["external_audit_act_10", "fss_2026_appointment"],
        priority_order=20,
    )


def build_designation_exit_event(
    current_run: dict[str, Any],
    previous_run: dict[str, Any],
    fiscal_end_month: int,
    subject: dict[str, str],
    estimated_event: dict[str, Any],
    as_of: date,
) -> dict[str, Any] | None:
    if subject.get("status") == "out_of_listed_scope":
        return None
    if estimated_event.get("type") != "possible_designated_cycle":
        return None

    start_year = int_or_zero(current_run.get("start_year"))
    if not start_year:
        return None
    term_end_year = start_year + 2
    transition_fiscal_year = term_end_year + 1
    event_date = appointment_deadline(transition_fiscal_year, fiscal_end_month)
    days_remaining = (event_date - as_of).days
    previous_auditor = str(previous_run.get("auditor", "")).strip()
    return audit_timeline_event(
        kind="designation_exit",
        title="지정감사 종료 후 자유선임 전환 후보",
        event_date=event_date,
        fiscal_year=transition_fiscal_year,
        days_remaining=days_remaining,
        detail=(
            f"이전 감사인 {previous_auditor or '확인 감사인'}이 6년 이상 이어진 뒤 감사인이 바뀐 패턴입니다. "
            f"{transition_fiscal_year} 사업연도 자유선임 전환 가능성을 확인하세요."
        ),
        basis="주기적 지정은 통상 3개 사업연도 지정감사 이후 자유선임 전환 검토가 필요합니다.",
        confidence="medium",
        source_ids=["external_audit_act_11", "external_audit_decree_16"],
        priority_order=10,
    )


def build_periodic_designation_event(
    current_run: dict[str, Any],
    fiscal_end_month: int,
    subject: dict[str, str],
    as_of: date,
    *,
    base_years: int,
    kind: str,
    title: str,
    detail_suffix: str,
) -> dict[str, Any] | None:
    if subject.get("status") == "out_of_listed_scope":
        return None

    start_year = int_or_zero(current_run.get("start_year"))
    end_year = int_or_zero(current_run.get("end_year"))
    length = int_or_zero(current_run.get("length"))
    auditor = str(current_run.get("auditor", "")).strip()
    if not start_year or not end_year:
        return None

    candidate_fiscal_year = start_year + base_years
    event_date = appointment_deadline(candidate_fiscal_year, fiscal_end_month)
    days_remaining = (event_date - as_of).days
    remaining_years = max(0, base_years - length)
    confidence = "medium" if subject.get("status") == "likely_subject" else "low"
    return audit_timeline_event(
        kind=kind,
        title=title,
        event_date=event_date,
        fiscal_year=candidate_fiscal_year,
        days_remaining=days_remaining,
        detail=(
            f"{auditor or '현재 감사인'} 연속 {length}개 사업연도 확인. "
            f"{remaining_years}개 사업연도 후 기준 도달로 계산했습니다. {detail_suffix}"
        ),
        basis="OpenDART 감사인 이력에는 자유선임/지정 구분이 직접 표시되지 않아 동일 감사인 연속연차를 기준으로 추정합니다.",
        confidence=confidence,
        source_ids=["external_audit_act_11", "external_audit_decree_16"],
        priority_order=30 if base_years == PERIODIC_APPOINTMENT_YEARS else 40,
    )


def audit_timeline_event(
    *,
    kind: str,
    title: str,
    event_date: date,
    fiscal_year: int,
    days_remaining: int,
    detail: str,
    basis: str,
    confidence: str,
    source_ids: list[str],
    priority_order: int,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "title": title,
        "event_date": event_date.isoformat(),
        "fiscal_year": str(fiscal_year),
        "days_remaining": days_remaining,
        "dday_label": dday_label(days_remaining),
        "urgency": event_urgency(days_remaining, kind),
        "detail": detail,
        "basis": basis,
        "confidence": confidence,
        "sources": source_refs(source_ids),
        "priority_order": priority_order,
    }


def next_timeline_event(schedule: list[dict[str, Any]]) -> dict[str, Any]:
    if not schedule:
        return {}
    return schedule[0]


def resolve_fiscal_end_month(corp: dict[str, str], company_profile: dict[str, Any]) -> int:
    for source in (company_profile, corp):
        value = str(source.get("acc_mt", "")).strip()
        if not value:
            continue
        month = int_or_zero(value)
        if 1 <= month <= 12:
            return month
    return 12


def fiscal_year_start(fiscal_year: int, fiscal_end_month: int) -> date:
    if fiscal_end_month == 12:
        return date(fiscal_year, 1, 1)
    start_month = fiscal_end_month + 1
    return date(fiscal_year - 1, start_month, 1)


def appointment_deadline(fiscal_year: int, fiscal_end_month: int) -> date:
    return fiscal_year_start(fiscal_year, fiscal_end_month) + timedelta(days=APPOINTMENT_DEADLINE_DAYS)


def dday_label(days_remaining: int) -> str:
    if days_remaining < 0:
        return f"D+{abs(days_remaining)}"
    if days_remaining == 0:
        return "D-day"
    return f"D-{days_remaining}"


def event_urgency(days_remaining: int, kind: str) -> str:
    if days_remaining < 0:
        return "overdue"
    if days_remaining <= 120:
        return "urgent"
    if days_remaining <= (540 if kind in {"periodic_designation", "governance_deferral"} else 365):
        return "watch"
    return "normal"


def build_sales_strategy(
    corp: dict[str, str],
    analysis: dict[str, Any],
    coverage: dict[str, Any],
    special_issues: list[dict[str, Any]],
    company_profile: dict[str, Any],
) -> dict[str, Any]:
    segment = estimate_company_segment(corp, analysis, coverage, special_issues, company_profile)
    flags = estimate_segment_flags(corp, company_profile)
    sales_case = estimate_sales_case(segment, flags, analysis, coverage, special_issues)
    return {
        "company_segment": segment,
        "flags": flags,
        "sales_case": sales_case,
        "case_badges": build_case_badges(segment, flags, sales_case, special_issues),
        "legal_rule_refs": [
            "주권상장법인은 연속 3개 사업연도 동일 감사인 선임 의무가 있습니다.",
            "주기적 지정제는 상장회사 등에 대해 6년 자유선임 후 3년 지정감사 구조로 적용될 수 있습니다.",
            "감사위원회 설치 회사는 감사위원회가 외부감사인을 선정하는 구조를 우선 확인해야 합니다.",
        ],
    }


def estimate_company_segment(
    corp: dict[str, str],
    analysis: dict[str, Any],
    coverage: dict[str, Any],
    special_issues: list[dict[str, Any]],
    company_profile: dict[str, Any],
) -> dict[str, Any]:
    corp_cls = (
        analysis.get("corp_class")
        or corp.get("corp_cls")
        or str(company_profile.get("corp_cls", "")).strip()
    )
    evidence: list[str] = []
    if corp_cls:
        evidence.append(f"OpenDART 법인구분 corp_cls={corp_cls} ({CORP_CLASS_LABELS.get(corp_cls, '알 수 없음')})")

    if corp_cls in {"Y", "K", "N"}:
        return {
            "code": "listed",
            "label": f"{CORP_CLASS_LABELS[corp_cls]} 상장사",
            "confidence": "high",
            "evidence": evidence or ["OpenDART 법인구분상 상장회사 신호가 있습니다."],
        }

    evidence.append("현재 버전의 상장사 분석 범위 밖입니다.")
    return {
        "code": "out_of_listed_scope",
        "label": "상장사 범위 밖",
        "confidence": "low",
        "evidence": evidence,
    }


def estimate_segment_flags(
    corp: dict[str, str],
    company_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    flags = []
    financial_evidence = financial_company_evidence(corp, company_profile)
    if financial_evidence:
        flags.append(
            {
                "code": "financial_candidate",
                "label": "금융회사 추정",
                "confidence": "medium" if financial_evidence[0].startswith("OpenDART 업종코드") else "low",
                "evidence": financial_evidence,
            }
        )

    return flags


def financial_company_evidence(
    corp: dict[str, str],
    company_profile: dict[str, Any],
) -> list[str]:
    evidence = []
    industry_code = str(company_profile.get("induty_code") or corp.get("induty_code") or "").strip()
    if re.match(r"^(64|65|66)", industry_code):
        evidence.append(f"OpenDART 업종코드 {industry_code}가 금융·보험업 계열로 보입니다.")
    name = " ".join(
        str(value or "")
        for value in (
            corp.get("corp_name"),
            corp.get("stock_name"),
            company_profile.get("corp_name"),
            company_profile.get("stock_name"),
        )
    )
    matched = [keyword for keyword in FINANCIAL_NAME_KEYWORDS if keyword in name]
    if matched:
        evidence.append(f"회사명에 금융회사 키워드({', '.join(sorted(set(matched)))})가 포함됩니다.")
    return evidence


def estimate_sales_case(
    segment: dict[str, Any],
    flags: list[dict[str, Any]],
    analysis: dict[str, Any],
    coverage: dict[str, Any],
    special_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    event = analysis.get("estimated_event", {}) or {}
    event_type = event.get("type", "")
    status = analysis.get("status")
    segment_code = segment.get("code", "")
    has_special_issues = bool(special_issues)
    caveats = [
        "공개 데이터 기반 추정이므로 감사인 선임보고, 원문 공시, 독립성·수임 가능성 검토가 필요합니다."
    ]

    if status != "ok":
        if has_special_issues:
            case = {
                "code": "compliance_risk_watch",
                "label": "특이공시 원문 확인",
                "priority": "high",
                "timing": "즉시",
                "next_action": "감사전 재무제표 미제출, 제출 지연/연장, 정정 공시 원문을 먼저 확인하고 외감대상 여부와 선임 공백을 분리하세요.",
                "rationale": "감사인 이력은 부족하지만 외부감사관련 특이공시가 확인됩니다.",
            }
        else:
            case = {
                "code": "source_gap_research",
                "label": "데이터 보강 필요",
                "priority": "low",
                "timing": "리드 검증 단계",
                "next_action": "회사명·고유번호를 재확인하고 DART 원문, 감사계약 체결보고, 상장사 여부를 수동으로 확인하세요.",
                "rationale": "최근 감사인 이력이 공개 API와 외부감사관련 공시목록에서 확인되지 않았습니다.",
            }
        case["caveats"] = caveats
        return case

    if event_type == "possible_designated_cycle":
        remaining = event.get("years_remaining")
        timing = "당기 또는 차기 자유선임 전환 확인" if remaining == 0 else f"약 {remaining}개 사업연도 후 전환 가능성 점검"
        case = {
            "code": "designation_exit_opportunity",
            "label": "지정감사 종료/자유선임 전환 후보",
            "priority": "high" if remaining == 0 else "medium",
            "timing": timing,
            "next_action": "현재 감사인이 지정감사인인지 확인하고, 지정 3년 종료 후 자유선임 전환 가능 시점에 맞춰 사전 컨택 후보로 관리하세요.",
            "rationale": event.get("message", ""),
        }
    elif event_type == "six_year_threshold_reached":
        case = {
            "code": "periodic_designation_watch",
            "label": "주기적 지정 도래 확인",
            "priority": "high",
            "timing": "즉시 확인",
            "next_action": "6년 자유선임 기준 도달 여부, 주기적 지정 통지, 유예·분산지정 적용 여부를 확인하세요.",
            "rationale": event.get("message", ""),
        }
    elif event_type == "approaching_six_year_threshold":
        remaining = event.get("years_remaining")
        case = {
            "code": "periodic_designation_watch",
            "label": "6년 기준 접근",
            "priority": "medium",
            "timing": f"약 {remaining}개 사업연도 후 6년 기준 도달",
            "next_action": "현재 감사인 유지가 계속되는지 모니터링하고, 지정제·유예제 적용 여부를 선제 확인하세요.",
            "rationale": event.get("message", ""),
        }
    elif segment_code == "listed":
        case = {
            "code": "listed_monitoring",
            "label": "상장사 장기 모니터링",
            "priority": "low",
            "timing": "분기별 모니터링",
            "next_action": "현재 3년 계약 구간, 지정제 적용 여부, 감사인 변경 공시를 계속 모니터링하세요.",
            "rationale": event.get("message", ""),
        }
    else:
        case = {
            "code": "out_of_listed_scope",
            "label": "상장사 범위 밖",
            "priority": "low",
            "timing": "리드 선별 단계",
            "next_action": "상장사 분석 대상인지 먼저 확인하고, 범위 밖이면 별도 리서치 프로세스로 넘기세요.",
            "rationale": "현재 버전은 상장사 OpenDART 데이터만 대상으로 합니다.",
        }

    if has_special_issues:
        case["priority"] = raise_priority(case["priority"])
        caveats.append("특이공시가 있어 원문 확인 후 미제출·지연·정정 사유를 먼저 분리해야 합니다.")

    flag_codes = {flag.get("code") for flag in flags}
    if "financial_candidate" in flag_codes:
        caveats.append("금융회사 추정 신호가 있어 금융회사 지배구조법 적용, 감사위원회, 3년 선임 의무를 별도 확인해야 합니다.")
    if coverage.get("missing_recent_years"):
        caveats.append("최근 공시 미확인 연도는 미제출이 아니라 API·명칭·비대상 가능성도 있습니다.")

    case["caveats"] = caveats
    return case


def raise_priority(priority: str) -> str:
    if priority == "high":
        return "high"
    if priority == "medium":
        return "high"
    return "medium"


def build_case_badges(
    segment: dict[str, Any],
    flags: list[dict[str, Any]],
    sales_case: dict[str, Any],
    special_issues: list[dict[str, Any]],
) -> list[str]:
    labels = [
        str(segment.get("label", "")).strip(),
        str(sales_case.get("label", "")).strip(),
        priority_label(str(sales_case.get("priority", ""))),
    ]
    labels.extend(str(flag.get("label", "")).strip() for flag in flags)
    if special_issues:
        labels.append(f"특이공시 {len(special_issues)}건")

    deduped = []
    for label in labels:
        if label and label not in deduped:
            deduped.append(label)
    return deduped[:6]


def priority_label(priority: str) -> str:
    return {
        "high": "우선순위 높음",
        "medium": "우선순위 보통",
        "low": "우선순위 낮음",
    }.get(priority, "우선순위 확인")


def get_firm_persona(code: str | None = None) -> dict[str, Any]:
    context = deep_copy(DEFAULT_FIRM_CONTEXT)
    path = firm_context_path()
    if path:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                deep_merge(context, loaded)
                context["_context_source"] = str(path)
        except (OSError, json.JSONDecodeError) as exc:
            context["_context_warning"] = f"{path}: {exc}"
    else:
        context["_context_source"] = "built-in default"
    if code and code != context.get("code"):
        context["_requested_context"] = code
    return context


def firm_context_path() -> Path | None:
    configured = os.environ.get(FIRM_CONTEXT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    for candidate in FIRM_CONTEXT_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def deep_copy(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def firm_auditor_keys(persona: dict[str, Any]) -> set[str]:
    aliases = [persona.get("label", ""), persona.get("code", "")]
    aliases.extend(as_text_list(persona.get("auditor_aliases", [])))
    keys = {normalize_auditor(alias) for alias in aliases if str(alias).strip()}
    return {key for key in keys if key}


def as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def build_lead_recommendation(
    persona: dict[str, Any],
    corp: dict[str, str],
    analysis: dict[str, Any],
    sales_strategy: dict[str, Any],
    coverage: dict[str, Any],
    special_issues: list[dict[str, Any]],
    service_contracts: list[dict[str, Any]],
    company_profile: dict[str, Any],
) -> dict[str, Any]:
    segment = sales_strategy.get("company_segment", {})
    sales_case = sales_strategy.get("sales_case", {})
    flags = sales_strategy.get("flags", [])
    event = analysis.get("estimated_event", {}) or {}
    current_auditor_key = analysis.get("current_auditor_key") or ""
    firm_label = str(persona.get("label") or "회계법인")
    is_current_firm = current_auditor_key in firm_auditor_keys(persona)

    drivers = []
    timing_points, timing_evidence = lead_timing_points(sales_case, event)
    drivers.append({"label": "타이밍", "points": timing_points, "evidence": timing_evidence})

    segment_points, segment_evidence = lead_segment_points(segment, flags)
    drivers.append({"label": "세그먼트 적합도", "points": segment_points, "evidence": segment_evidence})

    coverage_points, coverage_evidence = lead_coverage_points(coverage, special_issues)
    drivers.append({"label": "공개 검증성", "points": coverage_points, "evidence": coverage_evidence})

    expansion_points, expansion_evidence = lead_expansion_points(
        persona,
        corp,
        segment,
        flags,
        service_contracts,
        company_profile,
    )
    drivers.append({"label": "부가자문 확장성", "points": expansion_points, "evidence": expansion_evidence})

    context_points, context_evidence = lead_context_points(persona, corp)
    drivers.append({"label": "ERP/CRM 맥락", "points": context_points, "evidence": context_evidence})

    people_points, people_evidence = lead_people_network_points(
        persona,
        corp,
        company_profile,
        service_contracts,
    )
    drivers.append({"label": "인력·관계 커버리지", "points": people_points, "evidence": people_evidence})

    friction_points, friction_evidence = lead_friction_points(is_current_firm, firm_label, special_issues, sales_case)
    drivers.append({"label": "제약 조정", "points": friction_points, "evidence": friction_evidence})

    raw_score = sum(item["points"] for item in drivers)
    fit_score = max(0, min(100, raw_score))
    grade = recommendation_grade(fit_score)
    target_type = "기존 관계 확장 리드" if is_current_firm else "신규 감사영업 후보"
    verdict = recommendation_verdict(fit_score, target_type, sales_case)
    opening_angle = build_opening_angle(persona, sales_case, segment, flags, is_current_firm)

    return {
        "firm": {
            "code": persona.get("code", ""),
            "label": persona.get("label", ""),
            "positioning": persona.get("positioning", ""),
        },
        "target_type": target_type,
        "fit_score": fit_score,
        "grade": grade,
        "verdict": verdict,
        "opening_angle": opening_angle,
        "suggested_services": suggested_services_for_persona(persona, sales_case, segment, flags),
        "score_drivers": drivers,
        "next_steps": build_recommendation_next_steps(
            persona,
            sales_case,
            segment,
            flags,
            special_issues,
            is_current_firm,
        ),
        "caveats": build_recommendation_caveats(persona, is_current_firm, special_issues),
        "persona_basis": persona.get("public_basis", []),
        "firm_context_basis": persona.get("public_basis", []),
        "firm_context_source": persona.get("_context_source", ""),
    }


def lead_timing_points(sales_case: dict[str, Any], event: dict[str, Any]) -> tuple[int, str]:
    code = sales_case.get("code", "")
    priority = sales_case.get("priority", "")
    if code == "designation_exit_opportunity":
        points = 34 if event.get("years_remaining") == 0 else 28
    elif code == "periodic_designation_watch":
        points = 26 if priority == "high" else 22
    elif code == "compliance_risk_watch":
        points = 20
    elif code == "listed_monitoring":
        points = 12
    else:
        points = 8
    return points, sales_case.get("timing") or sales_case.get("label") or "선임/지정 이벤트 타이밍 확인 필요"


def lead_segment_points(segment: dict[str, Any], flags: list[dict[str, Any]]) -> tuple[int, str]:
    code = segment.get("code", "")
    points_by_segment = {
        "listed": 18,
        "out_of_listed_scope": 4,
    }
    points = points_by_segment.get(code, 8)
    flag_codes = {flag.get("code") for flag in flags}
    if "financial_candidate" in flag_codes:
        points += 5
    evidence = segment.get("label", "세그먼트 확인 필요")
    if flags:
        evidence += " · " + " · ".join(flag.get("label", "") for flag in flags)
    return min(points, 26), evidence


def lead_coverage_points(
    coverage: dict[str, Any],
    special_issues: list[dict[str, Any]],
) -> tuple[int, str]:
    rows = int_or_zero(coverage.get("merged_rows"))
    periodic_rows = int_or_zero(coverage.get("periodic_report_api_rows"))
    external_rows = int_or_zero(coverage.get("external_audit_report_rows"))
    points = 8
    if periodic_rows:
        points += 7
    if external_rows:
        points += 4
    if rows >= 6:
        points += 4
    if special_issues:
        points += 3
    if coverage.get("missing_recent_years"):
        points -= 4
    evidence = f"감사 이력 {rows}건, 정기보고서 API {periodic_rows}건, 외부감사 공시 {external_rows}건"
    if special_issues:
        evidence += f", 특이공시 {len(special_issues)}건"
    return max(0, min(points, 22)), evidence


def lead_expansion_points(
    persona: dict[str, Any],
    corp: dict[str, str],
    segment: dict[str, Any],
    flags: list[dict[str, Any]],
    service_contracts: list[dict[str, Any]],
    company_profile: dict[str, Any],
) -> tuple[int, str]:
    points = 8
    reasons = []
    segment_code = segment.get("code", "")
    if segment_code == "listed":
        points += 5
        reasons.append("공시·내부통제·세무 이슈 확장 가능")
    flag_codes = {flag.get("code") for flag in flags}
    if "financial_candidate" in flag_codes:
        points += 4
        reasons.append("금융회사 규제·내부통제 자문 연결 가능")
    if service_contracts:
        points += 2
        reasons.append("감사용역 보수·시간 데이터 확인 가능")
    if str(company_profile.get("hm_url", "")).strip():
        points += 1
        reasons.append("회사 프로필 보조 정보 존재")
    focus_reasons = firm_focus_reasons(persona, corp, company_profile)
    if focus_reasons:
        points += min(4, len(focus_reasons) * 2)
        reasons.extend(focus_reasons[:2])
    return min(points, 18), " · ".join(reasons) or "감사 리드 중심, 부가자문 확장성 추가 확인 필요"


def lead_context_points(persona: dict[str, Any], corp: dict[str, str]) -> tuple[int, str]:
    signals = persona.get("erp_signals", {})
    if not isinstance(signals, dict):
        signals = {}
    points = 0
    reasons = []
    if matches_corp_signal(signals.get("restricted_corp_codes"), corp):
        points -= 30
        reasons.append("ERP/CRM상 제한 또는 독립성 검토 대상")
    if matches_corp_signal(signals.get("priority_accounts"), corp):
        points += 10
        reasons.append("ERP/CRM 우선 계정")
    if matches_corp_signal(signals.get("warm_intro_corp_codes"), corp):
        points += 8
        reasons.append("기존 관계 기반 warm introduction 가능")
    relationship_tags = as_text_list(signals.get("relationship_tags"))
    if relationship_tags:
        points += min(6, len(relationship_tags) * 2)
        reasons.append("내부 관계 태그 존재")
    return max(-30, min(points, 18)), " · ".join(reasons) or "내부 ERP/CRM 신호 없음"


def lead_people_network_points(
    persona: dict[str, Any],
    corp: dict[str, str],
    company_profile: dict[str, Any],
    service_contracts: list[dict[str, Any]],
) -> tuple[int, str]:
    target_context = target_account_context(persona, corp)
    matched_people = matching_firm_people(persona, corp, company_profile, target_context)
    decision_makers = as_dict_list(target_context.get("decision_makers")) if target_context else []
    relationship_edges = as_dict_list(target_context.get("relationship_edges")) if target_context else []

    points = 0
    reasons = []
    if matched_people:
        points += min(8, 4 + len(matched_people) * 2)
        roles = sorted({str(person.get("role", "")).strip() for person in matched_people if str(person.get("role", "")).strip()})
        role_text = ", ".join(roles[:3]) if roles else f"{len(matched_people)}명"
        reasons.append(f"해당 업종 감사·도메인 인력 매칭: {role_text}")

    if decision_makers:
        points += min(5, 2 + len(decision_makers))
        roles = sorted({str(item.get("role", "")).strip() for item in decision_makers if str(item.get("role", "")).strip()})
        role_text = ", ".join(roles[:3]) if roles else f"{len(decision_makers)}건"
        reasons.append(f"감사인 선임 의사결정권자 role/tag 확보: {role_text}")

    if relationship_edges:
        warm_count = sum(1 for edge in relationship_edges if str(edge.get("strength", "")).lower() in {"warm", "strong"})
        points += 6 if warm_count else 3
        reasons.append(f"내부 네트워크 연결선 {len(relationship_edges)}건")

    overlap_count = people_decision_overlap_count(matched_people, decision_makers)
    if overlap_count:
        points += min(4, overlap_count)
        reasons.append(f"학력·이력·네트워크 태그 교집합 {overlap_count}개")

    if target_context:
        if str(target_context.get("revenue_trend", "")).strip():
            points += 2
            reasons.append("매출추이 컨텍스트 존재")
        if str(target_context.get("audit_fee_trend", "")).strip():
            points += 2
            reasons.append("감사지출비용 추이 컨텍스트 존재")

    fee_summary = service_fee_summary(service_contracts)
    if fee_summary:
        points += 2
        reasons.append(fee_summary)

    if not reasons:
        return 0, "인력·의사결정권자·업종경험·네트워크 데이터 미제공"
    return min(points, 14), " · ".join(reasons)


def target_account_context(persona: dict[str, Any], corp: dict[str, str]) -> dict[str, Any]:
    for item in as_dict_list(persona.get("target_accounts")):
        if matches_corp_signal([item], corp):
            return item
    return {}


def matching_firm_people(
    persona: dict[str, Any],
    corp: dict[str, str],
    company_profile: dict[str, Any],
    target_context: dict[str, Any],
) -> list[dict[str, Any]]:
    company_tags = company_context_tags(corp, company_profile, target_context)
    industry_code = str(company_profile.get("induty_code") or corp.get("induty_code") or "").strip()
    company_name = normalize_search(corp.get("corp_name", ""))
    matches = []
    for person in as_dict_list(persona.get("firm_people")):
        score = 0
        for keyword in as_text_list(person.get("industry_keywords")):
            normalized = normalize_search(keyword)
            if normalized and (
                normalized in company_name
                or normalized in company_tags
                or any(normalized in tag or tag in normalized for tag in company_tags)
            ):
                score += 2
        for prefix in as_text_list(person.get("industry_codes")):
            if industry_code and industry_code.startswith(prefix):
                score += 2
        person_tags = set(normalize_tag_list(person.get("domain_tags")))
        if tag_sets_overlap(person_tags, company_tags):
            score += 2
        if int_or_zero(person.get("audit_experience_years")) >= 5:
            score += 1
        if score:
            item = dict(person)
            item["_match_score"] = score
            matches.append(item)
    matches.sort(key=lambda item: -int_or_zero(item.get("_match_score")))
    return matches[:5]


def company_context_tags(
    corp: dict[str, str],
    company_profile: dict[str, Any],
    target_context: dict[str, Any],
) -> set[str]:
    tags = set(normalize_tag_list(target_context.get("industry_tags")))
    tags.update(normalize_tag_list(target_context.get("company_tags")))
    tags.update(normalize_tag_list(company_profile.get("induty_code")))
    tags.update(normalize_tag_list(corp.get("corp_name")))
    return tags


def people_decision_overlap_count(
    matched_people: list[dict[str, Any]],
    decision_makers: list[dict[str, Any]],
) -> int:
    overlap = set()
    for person in matched_people:
        person_tags = person_profile_tags(person)
        for decision_maker in decision_makers:
            overlap.update(person_tags & person_profile_tags(decision_maker))
    return len(overlap)


def person_profile_tags(item: dict[str, Any]) -> set[str]:
    tags = set()
    for key in ("education_tags", "career_tags", "network_tags", "domain_tags"):
        tags.update(normalize_tag_list(item.get(key)))
    return tags


def tag_sets_overlap(left: set[str], right: set[str]) -> bool:
    if left & right:
        return True
    return any(a and b and (a in b or b in a) for a in left for b in right)


def normalize_tag_list(value: Any) -> list[str]:
    return [normalize_search(item) for item in as_text_list(value) if normalize_search(item)]


def as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def service_fee_summary(service_contracts: list[dict[str, Any]]) -> str:
    if not service_contracts:
        return ""
    years = sorted(
        {str(item.get("bsns_year", "")).strip() for item in service_contracts if str(item.get("bsns_year", "")).strip()},
        reverse=True,
    )
    if len(years) >= 2:
        return f"OpenDART 감사용역 보수·시간 {len(years)}개년 추이 확인 가능"
    return "OpenDART 감사용역 보수·시간 확인 가능"


def firm_focus_reasons(
    persona: dict[str, Any],
    corp: dict[str, str],
    company_profile: dict[str, Any],
) -> list[str]:
    reasons = []
    haystack = normalize_search(
        " ".join(
            [
                str(corp.get("corp_name", "")),
                str(company_profile.get("corp_name", "")),
                str(company_profile.get("corp_name_eng", "")),
                str(company_profile.get("induty_code", "")),
            ]
        )
    )
    for keyword in as_text_list(persona.get("industry_focus_keywords")):
        normalized = normalize_search(keyword)
        if normalized and normalized in haystack:
            reasons.append(f"firm context 산업 키워드 일치: {keyword}")
            break

    industry_code = str(company_profile.get("induty_code") or corp.get("induty_code") or "").strip()
    for prefix in as_text_list(persona.get("industry_focus_codes")):
        if industry_code and industry_code.startswith(prefix):
            reasons.append(f"firm context 산업 코드 포커스 일치: {prefix}")
            break
    return reasons


def matches_corp_signal(value: Any, corp: dict[str, str]) -> bool:
    if not value:
        return False
    corp_code = str(corp.get("corp_code", "")).strip()
    stock_code = str(corp.get("stock_code", "")).strip()
    corp_name = normalize_search(corp.get("corp_name", ""))
    items = value if isinstance(value, list) else [value]
    for item in items:
        candidates = []
        if isinstance(item, dict):
            candidates.extend(
                [
                    item.get("corp_code", ""),
                    item.get("stock_code", ""),
                    item.get("corp_name", ""),
                    item.get("name", ""),
                ]
            )
        else:
            candidates.append(item)
        for candidate in candidates:
            text = str(candidate).strip()
            normalized = normalize_search(text)
            if not text:
                continue
            if text in {corp_code, stock_code}:
                return True
            if normalized and (normalized == corp_name or normalized in corp_name):
                return True
    return False


def lead_friction_points(
    is_current_firm: bool,
    firm_label: str,
    special_issues: list[dict[str, Any]],
    sales_case: dict[str, Any],
) -> tuple[int, str]:
    points = 0
    reasons = []
    if is_current_firm:
        points -= 18
        reasons.append(f"현재 감사인이 {firm_label}로 보여 신규 감사 수임 리드는 아님")
    if special_issues:
        points -= 4
        reasons.append("특이공시 원문 확인 전 컨택 리스크 존재")
    if sales_case.get("code") == "source_gap_research":
        points -= 6
        reasons.append("공개 감사인 이력 부족")
    return points, " · ".join(reasons) or "중대한 감점 신호 없음"


def recommendation_grade(score: int) -> str:
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 45:
        return "C"
    return "Watch"


def recommendation_verdict(
    score: int,
    target_type: str,
    sales_case: dict[str, Any],
) -> str:
    if score >= 75:
        return f"{target_type}: 우선 검토"
    if score >= 60:
        return f"{target_type}: 후보군 편입"
    if score >= 45:
        return f"{target_type}: 모니터링"
    return f"{target_type}: 데이터 보강 후 재평가"


def build_opening_angle(
    persona: dict[str, Any],
    sales_case: dict[str, Any],
    segment: dict[str, Any],
    flags: list[dict[str, Any]],
    is_current_firm: bool,
) -> str:
    if is_current_firm:
        return "기존 감사 관계를 전제로 독립성 허용 범위 내 세무·딜·내부통제 후속 니즈를 확인"
    case_code = sales_case.get("code", "")
    if case_code == "designation_exit_opportunity":
        return "지정감사 종료 후 자유선임 전환 가능 시점을 근거로 사전 관계 형성"
    if case_code == "periodic_designation_watch":
        return "주기적 지정·유예·분산지정 판단을 돕는 제도 점검 미팅 제안"
    flag_codes = {flag.get("code") for flag in flags}
    if "financial_candidate" in flag_codes:
        return "금융회사 규제·내부통제·감사위원회 맥락에서 감사 품질과 전환 가능성 점검"
    return f"{persona.get('label', '회계법인')}의 감사·세무·딜 네트워크 적합성 검토"


def suggested_services_for_persona(
    persona: dict[str, Any],
    sales_case: dict[str, Any],
    segment: dict[str, Any],
    flags: list[dict[str, Any]],
) -> list[str]:
    services = []
    for service in as_text_list(persona.get("service_lines")):
        if service not in services:
            services.append(service)
    for service in ["외부감사 선임/전환 리서치", "내부회계관리제도 및 감사위원회 커뮤니케이션 점검"]:
        if service not in services:
            services.append(service)
    case_code = sales_case.get("code", "")
    if case_code in {"designation_exit_opportunity", "periodic_designation_watch"}:
        services.append("주기적 지정·자유선임 전환 사전 진단")
    flag_codes = {flag.get("code") for flag in flags}
    if "financial_candidate" in flag_codes:
        services.append("금융규제·리스크관리·내부통제 자문")
    services.append("세무 리스크 및 Deals 기회 사전 스크리닝")
    return services[:5]


def build_recommendation_next_steps(
    persona: dict[str, Any],
    sales_case: dict[str, Any],
    segment: dict[str, Any],
    flags: list[dict[str, Any]],
    special_issues: list[dict[str, Any]],
    is_current_firm: bool,
) -> list[str]:
    steps = []
    firm_label = str(persona.get("label") or "해당 회계법인")
    if is_current_firm:
        steps.append(f"현재 {firm_label} 감사 관계 여부와 독립성 제한 범위를 ERP/CRM에서 확인")
    else:
        steps.append("현 감사인 선임 사유가 자유선임인지 지정인지 원문 공시로 확인")
        steps.append("ERP/CRM에서 기존 관계, 제한 계정, 담당 파트너, 산업 담당 조직 매칭 여부 확인")
        steps.append("감사인 선임 의사결정권자와 내부 인력 간 합법 보유 관계 신호를 확인")
    steps.append(sales_case.get("next_action", "감사인 선임보고 및 원문 공시 확인"))
    if special_issues:
        steps.append("미제출·지연·정정 공시 원문에서 사유와 후속 제출 여부 확인")
    if flags:
        steps.append("금융회사 보조 플래그의 법적 요건과 감사위원회 구조를 별도 확인")
    return steps[:5]


def build_recommendation_caveats(
    persona: dict[str, Any],
    is_current_firm: bool,
    special_issues: list[dict[str, Any]],
) -> list[str]:
    firm_label = str(persona.get("label") or "해당 회계법인")
    caveats = [
        "추천 점수는 공개 공시와 설정된 firm context 기반의 영업 리서치 신호입니다.",
        "감사 수임 가능성은 독립성, 이해상충, 품질관리, 내부 승인 절차를 통과해야 판단할 수 있습니다.",
        "개인 인적사항·학력·이력·네트워크 정보는 합법적으로 보유했거나 공개·동의된 범위의 업무 관련 태그로만 사용해야 합니다.",
    ]
    if is_current_firm:
        caveats.append(f"현재 감사인이 {firm_label}이면 신규 감사영업이 아니라 유지·부가자문 관점으로 해석해야 합니다.")
    if special_issues:
        caveats.append("특이공시가 있는 회사는 컨택 전 원문과 후속 정정 여부를 먼저 확인해야 합니다.")
    return caveats


def build_recommendations(
    query: str,
    config: AppConfig,
    *,
    years: int = DEFAULT_YEARS,
    limit: int = MAX_RECOMMENDATIONS,
) -> dict[str, Any]:
    limit = max(1, min(MAX_RECOMMENDATIONS, limit))
    if config.demo:
        report = build_demo_report()
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "firm_persona": report["firm_persona"],
            "recommendations": [recommendation_summary(report)],
            "errors": [],
            "notes": ["Demo data only."],
        }

    candidates = search_companies(query, config, limit=max(limit, 5))
    recommendations = []
    errors = []
    for company in candidates[:limit]:
        try:
            report = build_report(
                company.get("corp_name", query),
                config,
                years=years,
                corp_code=company.get("corp_code", ""),
            )
            recommendations.append(recommendation_summary(report))
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "corp_name": company.get("corp_name", ""),
                    "corp_code": company.get("corp_code", ""),
                    "error": str(exc),
                }
            )

    recommendations.sort(key=recommendation_timing_sort_key)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "firm_persona": get_firm_persona(),
        "recommendations": recommendations,
        "errors": errors,
        "notes": [
            "추천은 검색 후보 중 상위 일부를 공개 공시로 재조회해 산정합니다.",
            "무료 배포 환경에서는 응답 시간이 길 수 있어 한 번에 최대 3개 후보만 평가합니다.",
        ],
    }


def recommendation_summary(report: dict[str, Any]) -> dict[str, Any]:
    company = report.get("company", {})
    analysis = report.get("analysis", {})
    strategy = report.get("sales_strategy", {})
    recommendation = report.get("lead_recommendation", {})
    sales_case = strategy.get("sales_case", {})
    segment = strategy.get("company_segment", {})
    next_timeline_event_payload = analysis.get("next_timeline_event", {})
    return {
        "company": {
            "corp_name": company.get("corp_name", ""),
            "corp_code": company.get("corp_code", ""),
            "stock_code": company.get("stock_code", ""),
        },
        "current_auditor": analysis.get("current_auditor"),
        "latest_business_year": analysis.get("latest_business_year"),
        "consecutive_years": analysis.get("consecutive_years"),
        "next_timeline_event": next_timeline_event_payload,
        "event_schedule": (analysis.get("event_schedule") or [])[:3],
        "segment": segment.get("label", ""),
        "sales_case": sales_case.get("label", ""),
        "fit_score": recommendation.get("fit_score", 0),
        "grade": recommendation.get("grade", "Watch"),
        "verdict": recommendation.get("verdict", ""),
        "target_type": recommendation.get("target_type", ""),
        "opening_angle": recommendation.get("opening_angle", ""),
        "suggested_services": recommendation.get("suggested_services", []),
        "next_steps": recommendation.get("next_steps", []),
        "score_drivers": recommendation.get("score_drivers", []),
    }


def recommendation_timing_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    next_event = item.get("next_timeline_event") or {}
    raw_days = next_event.get("days_remaining")
    if raw_days is None or raw_days == "":
        days = 99999
    else:
        days = int_or_zero(raw_days)
        if days < 0:
            days = 0
    return (
        days,
        -int_or_zero(item.get("fit_score")),
        str(item.get("company", {}).get("corp_name", "")),
    )


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
        "stock_code": payload.get("stock_code", "000000"),
        "modify_date": "",
        "corp_cls": payload["corp_cls"],
    }
    executives = demo_executives()
    analysis = analyze_history(corp, history, date.today().year)
    attach_event_schedule(corp, {}, analysis, as_of=date.today(), executives=executives, special_issues=[])
    coverage = build_coverage_summary(
        history,
        history,
        [],
        [],
        years=len(history),
        current_year=date.today().year,
        external_error=None,
    )
    sales_strategy = build_sales_strategy(corp, analysis, coverage, [], {})
    firm_persona = get_firm_persona()
    lead_recommendation = build_lead_recommendation(
        firm_persona,
        corp,
        analysis,
        sales_strategy,
        coverage,
        [],
        [],
        {},
    )
    analysis["sales_strategy"] = sales_strategy
    analysis["lead_recommendation"] = lead_recommendation
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Demo fixture",
        "firm_persona": firm_persona,
        "company": corp,
        "company_profile": {},
        "history": history,
        "history_sources": {
            "periodic_report_api": history,
            "external_audit_reports": [],
        },
        "audit_disclosures": [],
        "special_issues": [],
        "coverage": coverage,
        "service_contracts": [],
        "executives": executives,
        "analysis": analysis,
        "sales_strategy": sales_strategy,
        "lead_recommendation": lead_recommendation,
        "disclaimers": [
            "Demo data only.",
            "임원 학력은 OpenDART 구조화 필드가 아니며, 데모의 주요경력 문구는 예시입니다.",
        ],
    }


def load_demo_companies() -> list[dict[str, str]]:
    return [
        {
            "corp_code": "00000000",
            "corp_name": "샘플테크",
            "stock_code": "000000",
            "modify_date": "20260702",
            "corp_cls": "K",
        }
    ]


def demo_executives() -> list[dict[str, Any]]:
    rows = [
        {
            "bsns_year": "2025",
            "nm": "김대표",
            "ofcps": "대표이사",
            "rgist_exctv_at": "등기임원",
            "fte_at": "상근",
            "chrg_job": "경영총괄",
            "main_career": "샘플테크 CFO, 글로벌 제조사업 총괄",
            "mxmm_shrholdr_relate": "-",
            "hffc_pd": "4년",
            "tenure_end_on": "2027.03",
            "rcept_no": "",
        },
        {
            "bsns_year": "2025",
            "nm": "박감사",
            "ofcps": "사외이사",
            "rgist_exctv_at": "등기임원",
            "fte_at": "비상근",
            "chrg_job": "감사위원회 위원장",
            "main_career": "회계감독 실무, 상장사 감사위원회 운영 자문",
            "mxmm_shrholdr_relate": "-",
            "hffc_pd": "2년",
            "tenure_end_on": "2028.03",
            "rcept_no": "",
        },
        {
            "bsns_year": "2025",
            "nm": "이위원",
            "ofcps": "사외이사",
            "rgist_exctv_at": "등기임원",
            "fte_at": "비상근",
            "chrg_job": "감사위원",
            "main_career": "제조업 재무담당 임원, 내부회계관리제도 구축 프로젝트",
            "mxmm_shrholdr_relate": "-",
            "hffc_pd": "1년",
            "tenure_end_on": "2029.03",
            "rcept_no": "",
        },
    ]
    return rank_executive_rows([normalize_executive_row(row, 2025) for row in rows])


def render_report(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return render_markdown(payload)


def render_recommendations(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    firm = payload.get("firm_persona", {})
    lines = [
        "# Samil Listed Audit Radar Recommendations",
        "",
        f"- 검색어: **{payload.get('query', '')}**",
        f"- Firm context: **{firm.get('label', '')}**",
        f"- 생성시각: `{payload.get('generated_at', '')}`",
        "",
        "## 추천 후보",
        "",
    ]
    recommendations = payload.get("recommendations", [])
    if not recommendations:
        lines.append("- 추천 후보를 만들 수 없습니다.")
    for index, item in enumerate(recommendations, start=1):
        company = item.get("company", {})
        lines.extend(
            [
                f"### {index}. {company.get('corp_name', '')}",
                "",
                f"- 추천등급: **{item.get('grade', '')} / {item.get('fit_score', 0)}점**",
                f"- 판단: {item.get('verdict', '')}",
                f"- 현재 감사인: {item.get('current_auditor') or '-'}",
                f"- 세그먼트: {item.get('segment', '')}",
                f"- 케이스: {item.get('sales_case', '')}",
                f"- 첫 컨택 각도: {item.get('opening_angle', '')}",
            ]
        )
        for service in item.get("suggested_services", [])[:3]:
            lines.append(f"- 제안 서비스: {service}")
        for step in item.get("next_steps", [])[:3]:
            lines.append(f"- 다음 확인: {step}")
        lines.append("")
    if payload.get("errors"):
        lines.extend(["## 조회 실패", ""])
        for error in payload["errors"]:
            lines.append(f"- {error.get('corp_name', '')}: {error.get('error', '')}")
    return "\n".join(lines).rstrip()


def render_job_opportunities(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    lines = [
        "# Saramin Job Opportunity Signals",
        "",
        f"- 출처: {payload.get('source', '')}",
        f"- 회사 필터: {payload.get('company') or '전체'}",
        f"- 기간: 최근 {payload.get('days', '')}일",
        f"- 상장 필터: {payload.get('stock', '')}",
        f"- 검색 seed: {', '.join(payload.get('seeds', []))}",
        "",
        "## 채용공고 신호",
        "",
    ]
    jobs = payload.get("jobs", [])
    if not jobs:
        lines.append("- 관련 키워드가 매칭된 채용공고를 찾지 못했습니다.")
    else:
        lines.extend(
            [
                "| # | 회사 | 공고 | 추천 경로 | 점수 | 매칭 키워드 | 링크 |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for index, job in enumerate(jobs, start=1):
            link = job.get("url", "")
            link_text = f"[원문]({link})" if link else "-"
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(index),
                        clean_md(job.get("company", "")),
                        clean_md(job.get("title", "")),
                        clean_md(job.get("recommended_service") or job.get("recommended_path") or "-"),
                        clean_md(job.get("opportunity_score", 0)),
                        clean_md(", ".join(job.get("matched_keywords", []))),
                        clean_md(link_text),
                    ]
                )
                + " |"
            )
    lines.extend(["", "## 해석 메모", ""])
    for note in payload.get("notes", []):
        lines.append(f"- {note}")
    return "\n".join(lines).rstrip()


def render_markdown(payload: dict[str, Any]) -> str:
    company = payload["company"]
    analysis = payload["analysis"]
    next_event = analysis.get("next_timeline_event", {})
    lines = [
        "# 감사인 교체 시기 조회",
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
                f"- 관련 기준: **{next_event.get('title', '-')}**",
                f"- 교체/검토 시기: **{next_event.get('event_date', '-')}**",
                f"- 남은 기간: **{next_event.get('dday_label', '-')}**",
                f"- 기준 근거: {next_event.get('basis', '')}",
                f"- 기준 출처: {source_labels(next_event.get('sources', [])) or '-'}",
            ]
        )

        rules = analysis.get("applicable_rules", [])
        if rules:
            lines.extend(
                [
                    "",
                    "## 적용 기준 판단",
                    "",
                    "| 기준 | 판단 | 근거 | 확인사항 | 출처 |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for rule in rules:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            clean_md(rule.get("title", "")),
                            clean_md(rule.get("judgement", "")),
                            clean_md(shorten(rule.get("evidence", ""), 120)),
                            clean_md(shorten(rule.get("next_action", ""), 120)),
                            clean_md(source_labels(rule.get("sources", []))),
                        ]
                    )
                    + " |"
                )

        schedule = analysis.get("event_schedule", [])
        if schedule:
            lines.extend(
                [
                    "",
                    "## 관련 기준별 일정",
                    "",
                    "| 순서 | 예상일 | D-day | 사업연도 | 이벤트 | 근거 | 신뢰도 | 출처 |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
            for item in schedule:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            clean_md(item.get("order", "")),
                            clean_md(item.get("event_date", "")),
                            clean_md(item.get("dday_label", "")),
                            clean_md(item.get("fiscal_year", "")),
                            clean_md(item.get("title", "")),
                            clean_md(shorten(item.get("basis", ""), 120)),
                            clean_md(item.get("confidence", "")),
                            clean_md(source_labels(item.get("sources", []))),
                        ]
                    )
                    + " |"
                )

    executives = payload.get("executives", [])
    if executives:
        lines.extend(
            [
                "",
                "## 임원",
                "",
                "| 성명 | 직위 | 담당업무 | 등기/상근 | 선임 관련 신호 | 주요 경력 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in executives[:12]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        clean_md(row.get("nm", "")),
                        clean_md(row.get("ofcps", "")),
                        clean_md(row.get("chrg_job", "")),
                        clean_md(" / ".join(filter(None, [str(row.get("rgist_exctv_at", "")).strip(), str(row.get("fte_at", "")).strip()]))),
                        clean_md(row.get("decision_role_signal", "")),
                        clean_md(shorten(row.get("main_career", ""), 120)),
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
                        clean_md(contract_period_display(row)),
                        clean_md(row.get("adtor", "")),
                        clean_md(row.get("adt_cntrct_dtls_mendng") or row.get("mendng", "")),
                        clean_md(row.get("adt_cntrct_dtls_time") or row.get("tot_reqre_time", "")),
                        clean_md(row.get("real_exc_dtls_mendng", "")),
                        clean_md(row.get("real_exc_dtls_time", "")),
                    ]
                )
                + " |"
            )

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

    return "\n".join(lines)


def clean_md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def source_labels(sources: Any) -> str:
    labels = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        label = str(source.get("label") or source.get("title") or "").strip()
        url = str(source.get("url") or "").strip()
        if label and url:
            labels.append(f"{label}({url})")
        elif label:
            labels.append(label)
    return ", ".join(labels)


def contract_period_display(row: dict[str, Any]) -> str:
    year = str(row.get("bsns_year", "")).strip()
    period = re.sub(r"\s+", " ", str(row.get("period_label", ""))).strip()
    if period and period != year:
        return f"{year} ({period})"
    return year


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
    print(f"Samil Listed Audit Radar running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Samil Listed Audit Radar.")


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
                    firm_context = get_firm_persona()
                    self.respond_json(
	                        {
	                            "has_api_key": bool(config.api_key),
	                            "has_saramin_key": bool(config.saramin_key),
	                            "demo": config.demo,
                            "firm_context": {
                                "label": firm_context.get("label", ""),
                                "code": firm_context.get("code", ""),
                                "source": firm_context.get("_context_source", ""),
                            },
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
                if parsed.path == "/api/recommend":
                    q = first(query, "q")
                    if not q:
                        self.respond_json({"error": "검색어를 입력하세요."}, status=400)
                        return
                    years = clamp_years(int_or_zero(first(query, "years") or DEFAULT_YEARS))
                    limit = max(1, min(MAX_RECOMMENDATIONS, int_or_zero(first(query, "limit") or MAX_RECOMMENDATIONS)))
                    cache_key = f"recommend:{q}:{years}:{limit}"
                    self.respond_json(
                        cached(
                            cache_key,
                            lambda: build_recommendations(
                                q,
                                config,
                                years=years,
                                limit=limit,
                            ),
                        )
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
  <title>Samil Listed Audit Radar</title>
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
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; box-shadow: var(--shadow); }
    .search-panel { margin-bottom: 18px; }
    .report-panel { margin-top: 18px; }
    .status { color: var(--muted); font-size: 13px; margin-top: 10px; min-height: 18px; }
    .result-strip { display: grid; grid-template-columns: auto 1fr; gap: 12px; align-items: start; margin-top: 12px; padding-top: 12px; border-top: 1px solid #d7e5f8; }
    .result-strip[hidden] { display: none; }
    .result-label { color: var(--muted); font-size: 12px; font-weight: 800; line-height: 36px; white-space: nowrap; }
    .result-list { display: flex; flex-wrap: wrap; gap: 8px; min-width: 0; }
    .company { min-width: 210px; max-width: 320px; display: inline-flex; flex-direction: column; align-items: flex-start; text-align: left; background: #fff; color: var(--ink); border: 1px solid var(--line); margin: 0; height: auto; min-height: 44px; padding: 9px 12px; border-radius: 6px; }
    .company:hover { border-color: var(--brand); background: var(--brand-soft); }
    .company.selected { border-color: #0f3f88; background: var(--brand-soft); box-shadow: inset 3px 0 0 #0f3f88; }
    .company strong { display: block; max-width: 100%; color: #12345d; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .company span { display: block; max-width: 100%; color: var(--muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .empty-results { color: var(--muted); font-size: 13px; line-height: 36px; }
    .summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
    .metric { border: 1px solid #cfe0f6; border-radius: 8px; padding: 12px; background: #f8fbff; min-height: 86px; }
    .metric span { display: block; color: var(--muted); font-size: 12px; }
    .metric strong { display: block; margin-top: 8px; font-size: 18px; line-height: 1.25; }
    .decision { border: 1px solid #96c4ff; border-left: 4px solid #0f3f88; background: #f8fbff; border-radius: 6px; padding: 14px; margin-bottom: 14px; }
    .decision h3 { margin: 0 0 10px; font-size: 15px; }
    .decision-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .decision-block { border-top: 1px solid #d7e5f8; padding-top: 10px; min-width: 0; }
    .decision-block span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .decision-block strong { display: block; font-size: 16px; line-height: 1.35; }
    .decision-block p { margin: 6px 0 0; color: #29425f; line-height: 1.45; }
    .event { border-left: 4px solid var(--brand); padding: 12px 14px; background: var(--brand-soft); margin-bottom: 14px; border-radius: 4px; }
    .event small { color: #46617f; }
    .timeline { border: 1px solid #b9d5ff; border-radius: 8px; background: #f8fbff; padding: 14px; margin-bottom: 14px; }
    .timeline h3 { margin: 0 0 10px; font-size: 15px; }
    .timeline-list { display: grid; gap: 8px; }
    .timeline-item { display: grid; grid-template-columns: 108px 1fr; gap: 10px; border: 1px solid #cfe0f6; border-left: 4px solid var(--brand); border-radius: 6px; padding: 10px; background: #fff; }
    .timeline-item.overdue { border-left-color: #dc2626; background: #fff7f7; }
    .timeline-item.urgent { border-left-color: #ea580c; background: #fff8f0; }
    .timeline-item.watch { border-left-color: #ca8a04; background: #fffbeb; }
    .timeline-date span { display: block; color: var(--muted); font-size: 11px; }
    .timeline-date strong { display: block; margin-top: 3px; font-size: 17px; }
    .timeline-body strong { display: block; font-size: 14px; line-height: 1.35; }
    .timeline-body p { margin: 5px 0 0; color: #29425f; line-height: 1.45; }
    .timeline-body small { display: block; margin-top: 5px; color: var(--muted); line-height: 1.4; }
    .rule-section { border: 1px solid #b9d5ff; border-radius: 8px; background: #f8fbff; padding: 14px; margin-bottom: 14px; }
    .rule-section h3 { margin: 0 0 10px; font-size: 15px; }
    .rule-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 10px; }
    .rule-card { border: 1px solid #cfe0f6; border-left: 4px solid var(--brand); border-radius: 6px; padding: 12px; background: #fff; min-width: 0; }
    .rule-card.warning { border-left-color: #dc2626; background: #fff7f7; }
    .rule-card.review { border-left-color: #ca8a04; background: #fffbeb; }
    .rule-card.likely { border-left-color: var(--accent); background: var(--accent-soft); }
    .rule-card.not_applicable { border-left-color: #94a3b8; background: #f8fafc; }
    .rule-head { display: flex; justify-content: space-between; gap: 8px; align-items: flex-start; }
    .rule-head strong { font-size: 14px; line-height: 1.35; }
    .rule-status { border: 1px solid #b9d5ff; border-radius: 999px; padding: 3px 7px; font-size: 11px; font-weight: 800; color: var(--brand-dark); background: var(--brand-soft); white-space: nowrap; }
    .rule-card.warning .rule-status { color: #b91c1c; border-color: #fecaca; background: #fee2e2; }
    .rule-card.review .rule-status { color: #92400e; border-color: #fde68a; background: #fef3c7; }
    .rule-card.likely .rule-status { color: #0e7490; border-color: #a5e3ee; background: #ecfeff; }
    .rule-card.not_applicable .rule-status { color: #475569; border-color: #cbd5e1; background: #f1f5f9; }
    .rule-card p { margin: 8px 0 0; color: #29425f; line-height: 1.45; }
    .source-links { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
    .source-link { border: 1px solid #b9d5ff; border-radius: 999px; padding: 4px 8px; background: #fff; font-size: 11px; line-height: 1.2; }
    .recommendation { border: 1px solid #96c4ff; border-left: 4px solid #0f3f88; background: #f8fbff; border-radius: 6px; padding: 14px; margin-bottom: 14px; }
    .recommendation h3 { margin: 0 0 10px; font-size: 15px; }
    .recommendation-score { display: grid; grid-template-columns: 112px 1fr; gap: 12px; align-items: center; }
    .score-ring { display: grid; place-items: center; min-height: 86px; border-radius: 8px; background: #0f3f88; color: #fff; }
    .score-ring strong { display: block; font-size: 24px; line-height: 1; }
    .score-ring span { display: block; margin-top: 5px; color: #bfdbfe; font-size: 12px; }
    .recommendation-body strong { display: block; font-size: 16px; line-height: 1.35; }
    .recommendation-body p { margin: 6px 0 0; color: #29425f; line-height: 1.45; }
    .driver-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(112px, 1fr)); gap: 8px; margin-top: 12px; }
    .driver { border: 1px solid #cfe0f6; border-radius: 6px; padding: 8px; background: #fff; min-width: 0; }
    .driver span { display: block; color: var(--muted); font-size: 11px; }
    .driver strong { display: block; margin-top: 4px; font-size: 14px; }
    .recommend-list { display: grid; gap: 10px; }
    .recommend-card { border: 1px solid #cfe0f6; border-left: 4px solid var(--brand); border-radius: 6px; padding: 12px; background: #f8fbff; }
    .recommend-card h3 { margin: 0 0 8px; font-size: 15px; }
    .recommend-card p { margin: 6px 0 0; color: #29425f; line-height: 1.45; }
    .strategy { border: 1px solid #b9d5ff; border-left: 4px solid var(--brand); background: #f8fbff; border-radius: 6px; padding: 14px; margin-bottom: 14px; }
    .strategy h3 { margin: 0 0 10px; font-size: 15px; }
    .case-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
    .case-badge { border: 1px solid #b9d5ff; border-radius: 999px; padding: 5px 9px; font-size: 12px; font-weight: 700; color: var(--brand-dark); background: #fff; }
    .strategy-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .strategy-block { border-top: 1px solid #d7e5f8; padding-top: 10px; min-width: 0; }
    .strategy-block span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .strategy-block strong { display: block; font-size: 15px; line-height: 1.35; }
    .strategy-block p { margin: 6px 0 0; color: #29425f; line-height: 1.45; }
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
      .toolbar, .result-strip, .summary, .coverage-grid, .strategy-grid, .recommendation-score, .driver-grid, .decision-grid { grid-template-columns: 1fr; }
      .result-label { line-height: 1.2; }
      .company { width: 100%; max-width: none; }
      .timeline-item { grid-template-columns: 1fr; }
      main { padding: 14px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>감사인 교체 시기 조회</h1>
    <div class="subtitle">OpenDART 기반 상장사 감사인·교체 기준·남은 기간</div>
  </header>
  <main>
    <section class="panel search-panel">
      <div class="toolbar">
        <input id="query" placeholder="상장사명, 종목코드, 고유번호" value="삼성전자" />
        <select id="years">
          <option value="8">8년</option>
          <option value="10" selected>10년</option>
          <option value="12">12년</option>
        </select>
        <button id="searchBtn">검색</button>
      </div>
      <div class="status" id="status"></div>
      <div class="result-strip" id="resultStrip" hidden>
        <div class="result-label">검색 결과</div>
        <div class="result-list" id="results"></div>
      </div>
    </section>
    <section class="panel report-panel">
      <h2>감사인 교체 시기</h2>
      <div id="report">기업을 검색한 뒤 결과를 선택하세요.</div>
    </section>
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
      $("resultStrip").hidden = true;
      try {
        const rows = await getJson(`/api/search?q=${encodeURIComponent(q)}`);
        $("status").textContent = `${rows.length}개 상장사 후보`;
        $("resultStrip").hidden = false;
        $("results").innerHTML = rows.length ? rows.map(row => `<button class="company" data-code="${row.corp_code}" data-name="${row.corp_name}">
          <strong>${row.corp_name}</strong><span>고유번호 ${row.corp_code} · 종목코드 ${row.stock_code || "-"}</span>
        </button>`).join("") : `<span class="empty-results">검색 결과가 없습니다.</span>`;
        document.querySelectorAll(".company").forEach(btn => btn.addEventListener("click", () => loadReport(btn.dataset.name, btn.dataset.code)));
        if (rows.length === 1) {
          await loadReport(rows[0].corp_name, rows[0].corp_code);
        }
      } catch (err) {
        $("status").textContent = err.message;
      }
    }

    async function loadReport(name, code) {
      document.querySelectorAll(".company").forEach(btn => btn.classList.toggle("selected", btn.dataset.code === code));
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
      const company = data.company || {};
      const next = a.next_timeline_event || {};
      $("report").innerHTML = `
        ${meta}
        <div class="summary">
          <div class="metric"><span>대상 회사</span><strong>${esc(company.corp_name || "-")}</strong></div>
          <div class="metric"><span>현재 감사인</span><strong>${esc(a.current_auditor || "-")}</strong></div>
          <div class="metric"><span>교체/검토 시기</span><strong>${esc(next.event_date || "-")}</strong></div>
          <div class="metric"><span>남은 기간</span><strong>${esc(next.dday_label || "-")}</strong></div>
        </div>
        ${renderPrimaryDecision(data)}
        ${renderApplicableRules(a)}
        ${renderAuditTimeline(a)}
        ${renderExecutives(data)}
        <h2>감사인 이력</h2>
        <table><thead><tr><th>사업연도</th><th>감사인</th><th>의견</th><th>출처</th><th>보고서</th></tr></thead>
        <tbody>${(data.history || []).map(row => `<tr><td>${esc(row.bsns_year)}</td><td>${esc(row.adtor)}</td><td>${esc(row.adt_opinion || "-")}</td><td>${esc(row.source_detail || "-")}<small>${esc(row.source_note || "")}</small></td><td>${filingLink(row)}</td></tr>`).join("")}</tbody></table>
        ${renderServiceContracts(data)}
        ${renderSpecialIssues(data)}
        ${renderCoverage(data)}
      `;
    }

    function renderPrimaryDecision(data) {
      const analysis = data.analysis || {};
      const company = data.company || {};
      const next = analysis.next_timeline_event || {};
      return `<div class="decision">
        <h3>교체/검토 기준</h3>
        <div class="decision-grid">
          <div class="decision-block"><span>대상 회사</span><strong>${esc(company.corp_name || "-")}</strong><p>${esc(company.stock_code || "-")} · ${esc(analysis.corp_class_label || "-")}</p></div>
          <div class="decision-block"><span>감사인</span><strong>${esc(analysis.current_auditor || "-")}</strong><p>${esc(analysis.latest_business_year || "-")} 사업연도 기준 · 연속 ${esc(analysis.consecutive_years || "-")}년</p></div>
          <div class="decision-block"><span>관련 기준</span><strong>${esc(next.title || "-")}</strong><p>${esc(next.basis || "")}</p>${renderSources(next.sources || [])}</div>
          <div class="decision-block"><span>시기</span><strong>${esc(next.event_date || "-")} · ${esc(next.dday_label || "-")}</strong><p>${esc(next.detail || "")}</p></div>
        </div>
      </div>`;
    }

    function renderApplicableRules(analysis) {
      const rules = analysis.applicable_rules || [];
      if (!rules.length) return "";
      return `<div class="rule-section">
        <h3>적용 기준 판단</h3>
        <div class="rule-grid">
          ${rules.map(rule => `<div class="rule-card ${esc(rule.status || "review")}">
            <div class="rule-head"><strong>${esc(rule.title || "-")}</strong><span class="rule-status">${esc(rule.judgement || "-")}</span></div>
            <p>${esc(rule.evidence || "")}</p>
            <p><strong>확인사항</strong> ${esc(rule.next_action || "")}</p>
            ${renderSources(rule.sources || [])}
          </div>`).join("")}
        </div>
      </div>`;
    }

    function renderAuditTimeline(analysis) {
      const rows = analysis.event_schedule || [];
      if (!rows.length) return "";
      return `<div class="timeline">
        <h3>관련 기준별 일정</h3>
        <div class="timeline-list">
          ${rows.map(row => `<div class="timeline-item ${esc(row.urgency || "normal")}">
            <div class="timeline-date">
              <span>${esc(row.event_date || "-")}</span>
              <strong>${esc(row.dday_label || "-")}</strong>
              <span>${esc(row.fiscal_year || "-")} 사업연도</span>
            </div>
            <div class="timeline-body">
              <strong>${esc(row.order || "")}. ${esc(row.title || "-")}</strong>
              <p>${esc(row.detail || "")}</p>
              <small>${esc(row.basis || "")} · 신뢰도 ${esc(row.confidence || "-")}</small>
              ${renderSources(row.sources || [])}
            </div>
          </div>`).join("")}
        </div>
      </div>`;
    }

    function renderSources(sources) {
      if (!sources.length) return "";
      return `<div class="source-links">${sources.map(source => `<a class="source-link" href="${esc(source.url || "#")}" target="_blank" rel="noreferrer">${esc(source.label || source.title || "출처")}</a>`).join("")}</div>`;
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

    function renderServiceContracts(data) {
      const rows = data.service_contracts || [];
      if (!rows.length) return "";
      return `<h2 style="margin-top:16px;">감사용역 보수·시간</h2>
        <table><thead><tr><th>사업연도</th><th>감사인</th><th>계약보수</th><th>계약시간</th><th>실제보수</th><th>실제시간</th></tr></thead>
        <tbody>${rows.slice(0, 8).map(row => `<tr>
          <td>${esc(row.bsns_year)}<small>${esc(row.period_label || "")}</small></td>
          <td>${esc(row.adtor || "-")}</td>
          <td>${esc(row.adt_cntrct_dtls_mendng || row.mendng || "-")}</td>
          <td>${esc(row.adt_cntrct_dtls_time || row.tot_reqre_time || "-")}</td>
          <td>${esc(row.real_exc_dtls_mendng || "-")}</td>
          <td>${esc(row.real_exc_dtls_time || "-")}</td>
        </tr>`).join("")}</tbody></table>`;
    }

    function renderExecutives(data) {
      const rows = data.executives || [];
      if (!rows.length) return "";
      return `<h2 style="margin-top:16px;">임원</h2>
        <table><thead><tr><th>성명</th><th>직위/담당</th><th>등기/상근</th><th>선임 관련 신호</th><th>주요경력</th><th>원문</th></tr></thead>
        <tbody>${rows.slice(0, 12).map(row => `<tr>
          <td>${esc(row.nm || "-")}</td>
          <td>${esc(row.ofcps || "-")}<small>${esc(row.chrg_job || "")}</small></td>
          <td>${esc([row.rgist_exctv_at, row.fte_at].filter(Boolean).join(" / ") || "-")}</td>
          <td>${esc(row.decision_role_signal || "-")}</td>
          <td>${esc(short(row.main_career || "-", 120))}</td>
          <td>${filingLink(row)}</td>
        </tr>`).join("")}</tbody></table>`;
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
    function short(value, limit = 80) {
      const text = String(value || "").replace(/\s+/g, " ").trim();
      return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
    }
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
