import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or ""
).strip()
CHAT_ID = (
    os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID") or ""
).strip()

TOP_N = 20
MAX_PAGES = 5            # 시장별 시총 상위 100개 x 5페이지 = 500종목에서 거래대금 정렬
SPLIT_BY_MARKET = False  # True: 코스피 20 + 코스닥 20 따로 / False: 합쳐서 20
LINE = "━━━━━━━━━━━━━━━━━━"

STATE_FILE = "last_rank.json"
NEWS_MAX_STOCKS = int(os.environ.get("NEWS_MAX_STOCKS", "30"))  # 재료 조회할 최대 종목 수
GOOGLE_NEWS_DAYS = int(os.environ.get("GOOGLE_NEWS_DAYS", "3"))  # 최근 N일 기사만

THEME_KEYWORDS = {
    "📦 수주·계약": ["수주", "계약", "공급", "납품", "MOU"],
    "💹 실적": ["영업이익", "흑자", "어닝", "매출", "실적", "분기"],
    "🏛 정책·테마": ["정책", "정부", "관련주", "수혜", "규제", "법안"],
    "🔬 신사업·기술": ["AI", "반도체", "2차전지", "로봇", "바이오", "임상", "승인", "신약", "방산", "원전", "HBM"],
    "💰 자금·지분": ["유상증자", "자사주", "배당", "인수", "M&A", "지분", "무상증자"],
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

ETF_ETN_BRANDS = (
    "KODEX", "TIGER", "KBSTAR", "RISE", "ACE", "SOL", "HANARO", "PLUS",
    "TIMEFOLIO", "WOORI", "KOSEF", "ARIRANG", "TREX", "파워", "마이다스",
    "네비게이터", "히어로즈", "KIWOOM", "TDF",
)


# ---------------------------------------------------------------- 공통 유틸
def is_etf_or_etn(name: str) -> bool:
    n = (name or "").strip().upper()
    if re.search(r"\b(ETF|ETN)\b", n):
        return True
    for prefix in ETF_ETN_BRANDS:
        if n.startswith(prefix.upper()):
            if re.search(
                r"(ETN|ETF|레버리지|인버스|곱버스|선물|채권|액티브|커버드콜|2X|3X|"
                r"USD|달러|금현물|원유|S&P|나스닥|코스피|코스닥|국고채)",
                n,
            ):
                return True
    return False


def to_num(v) -> float:
    s = re.sub(r"[^0-9.\-]", "", str(v or ""))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def fmt_value(won_million: float) -> str:
    """거래대금(백만원 단위) -> '1조 2,345억' / '345억'"""
    eok = int(won_million // 100)
    if eok >= 10000:
        return f"{eok // 10000}조 {eok % 10000:,}억"
    return f"{eok:,}억"


def rank_mark(rank: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")


def rate_mark(rate: float) -> str:
    if rate > 0:
        return "🔺"
    if rate < 0:
        return "🔻"
    return "➖"


# ------------------------------------------------ 거래대금: 네이버 모바일 API
def fetch_mobile_api(market: str) -> list[dict]:
    out = []
    for page in range(1, MAX_PAGES + 1):
        url = f"https://m.stock.naver.com/api/stocks/marketValue/{market}"
        res = requests.get(
            url,
            params={"page": page, "pageSize": 100},
            headers=HEADERS,
            timeout=15,
        )
        res.raise_for_status()
        data = res.json()
        items = data.get("stocks") if isinstance(data, dict) else data
        if not items:
            break
        for it in items:
            end_type = str(it.get("stockEndType", "stock")).lower()
            name = str(it.get("stockName", "")).strip()
            code = str(it.get("itemCode", "")).strip()
            if end_type != "stock" or not name or not code or is_etf_or_etn(name):
                continue
            out.append(
                {
                    "market": market,
                    "name": name,
                    "code": code,
                    "price": to_num(it.get("closePrice")),
                    "rate": to_num(it.get("fluctuationsRatio")),
                    "value": to_num(it.get("accumulatedTradingValue")),  # 백만원
                }
            )
        time.sleep(0.3)
    if not out:
        raise RuntimeError(f"{market}: 모바일 API 응답에서 종목을 찾지 못했습니다.")
    return out


# ------------------------------------------- 거래대금: 네이버 PC 거래상위 표
def fetch_pc_page(market: str) -> list[dict]:
    sosok = 0 if market == "KOSPI" else 1
    url = f"https://finance.naver.com/sise/sise_quant.naver?sosok={sosok}"
    res = requests.get(url, headers=HEADERS, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.content.decode("euc-kr", errors="ignore"), "html.parser")
    table = soup.select_one("table.type_2")
    if table is None:
        raise RuntimeError(f"{market}: PC 페이지에서 표를 찾지 못했습니다.")
    out = []
    for tr in table.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        a = tds[1].find("a")
        if a is None:
            continue
        m = re.search(r"code=(\w{6})", a.get("href", ""))
        if not m:
            continue
        name = a.get_text(strip=True)
        if is_etf_or_etn(name):
            continue
        out.append(
            {
                "market": market,
                "name": name,
                "code": m.group(1),
                "price": to_num(tds[2].get_text()),
                "rate": to_num(tds[4].get_text()),
                "value": to_num(tds[6].get_text()),  # 백만원
            }
        )
    if not out:
        raise RuntimeError(f"{market}: PC 페이지에서 종목을 읽지 못했습니다.")
    return out


def fetch_market(market: str) -> list[dict]:
    errors = []
    for fn in (fetch_mobile_api, fetch_pc_page):
        try:
            return fn(market)
        except Exception as e:
            errors.append(f"{fn.__name__}: {type(e).__name__}: {e}")
            print(f"[안내] {market} {errors[-1]}")
    raise RuntimeError(f"{market} 모든 소스 실패 | " + " / ".join(errors))


# ---------------------------------------------- 인기종목: 네이버 검색상위
def fetch_popular_pc() -> list[dict]:
    url = "https://finance.naver.com/sise/lastsearch2.naver"
    res = requests.get(url, headers=HEADERS, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.content.decode("euc-kr", errors="ignore"), "html.parser")
    out, seen = [], set()
    for tr in soup.select("tr"):
        a = tr.select_one("a[href*='code=']")
        if a is None:
            continue
        m = re.search(r"code=(\w{6})", a.get("href", ""))
        if not m:
            continue
        code, name = m.group(1), a.get_text(strip=True)
        if code in seen or not name or is_etf_or_etn(name):
            continue
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if name not in cells:
            continue
        i = cells.index(name)
        # 열 순서: 종목명 | 검색비율 | 현재가 | 전일비 | 등락률 | ...
        if len(cells) <= i + 4:
            continue
        seen.add(code)
        out.append(
            {
                "name": name,
                "code": code,
                "ratio": cells[i + 1],
                "price": to_num(cells[i + 2]),
                "rate": to_num(cells[i + 4]),
            }
        )
    if not out:
        raise RuntimeError("검색상위 페이지에서 종목을 읽지 못했습니다.")
    return out


# ------------- 인기종목: stock.naver.com 랭킹 API (stocklist/top 페이지가 쓰는 API)
POPULAR_API_URL = "https://stock.naver.com/api/domestic/market/stock/default"
POPULAR_ORDER_TYPE = os.environ.get("POPULAR_ORDER_TYPE", "quantTop").strip() or "quantTop"
STOCK_HEADERS = {
    **HEADERS,
    "Referer": "https://stock.naver.com/market/stock/kr/stocklist/top",
    "Origin": "https://stock.naver.com",
}


def _pick(d: dict, keys: list[str]):
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v is not None and str(v).strip() != "":
            return v
    return None


def _find_stock_dicts(obj, out: list):
    if isinstance(obj, list):
        for x in obj:
            _find_stock_dicts(x, out)
    elif isinstance(obj, dict):
        code = _pick(obj, ["itemcode", "itemCode", "stockCode", "code"])
        name = _pick(obj, ["itemname", "itemName", "stockName", "name"])
        if code and name and re.fullmatch(r"[0-9A-Za-z]{6}", str(code).strip()):
            out.append(obj)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _find_stock_dicts(v, out)


def fetch_popular_api() -> list[dict]:
    out, seen = [], set()
    page_size = 50
    for start in range(0, page_size * 4, page_size):
        res = requests.get(
            POPULAR_API_URL,
            params={
                "tradeType": "KRX",
                "marketType": "ALL",
                "orderType": POPULAR_ORDER_TYPE,
                "startIdx": start,
                "pageSize": page_size,
            },
            headers=STOCK_HEADERS,
            timeout=15,
        )
        res.raise_for_status()
        items: list = []
        _find_stock_dicts(res.json(), items)
        if not items:
            break
        for it in items:
            code = str(_pick(it, ["itemcode", "itemCode", "stockCode", "code"])).strip()
            name = str(_pick(it, ["itemname", "itemName", "stockName", "name"])).strip()
            if code in seen or is_etf_or_etn(name):
                continue
            end_type = str(_pick(it, ["stockEndType", "endType"]) or "stock").lower()
            if end_type in ("etf", "etn"):
                continue
            seen.add(code)
            out.append(
                {
                    "name": name,
                    "code": code,
                    "price": to_num(_pick(it, ["nowPrice", "closePrice", "currentPrice"])),
                    "rate": to_num(
                        _pick(it, ["prevChangeRate", "fluctuationsRatio", "changeRate"])
                    ),
                    "ratio": "",
                }
            )
        if len(out) >= TOP_N:
            break
        time.sleep(0.3)
    if not out:
        raise RuntimeError("랭킹 API 응답에서 종목을 찾지 못했습니다.")
    return out


def fetch_popular() -> list[dict]:
    errors = []
    for fn in (fetch_popular_api, fetch_popular_pc):
        try:
            return fn()
        except Exception as e:
            errors.append(f"{fn.__name__}: {type(e).__name__}: {e}")
            print(f"[안내] 인기종목 {errors[-1]}")
    raise RuntimeError("인기종목 모든 소스 실패 | " + " / ".join(errors))


# --------------------------------------------------------------- 메시지 조립
def value_row(rank: int, r: dict, show_market: bool) -> str:
    tag = f"[{'코스피' if r['market'] == 'KOSPI' else '코스닥'}] " if show_market else ""
    return (
        f"{rank_mark(rank)} {tag}{r['name']} ({r['code']})\n"
        f"      {int(r['price']):,}원 {rate_mark(r['rate'])}{r['rate']:+.2f}% · 💵 {fmt_value(r['value'])}"
    )


def popular_row(rank: int, r: dict) -> str:
    extra = f" · 🔍 {r['ratio']}" if r.get("ratio") else ""
    return (
        f"{rank_mark(rank)} {r['name']} ({r['code']})\n"
        f"      {int(r['price']):,}원 {rate_mark(r['rate'])}{r['rate']:+.2f}%{extra}"
    )


def section(title: str, sub: str, rows: list[str]) -> str:
    head = f"{LINE}\n{title}\n{sub}\n"
    return head + "\n" + "\n\n".join(rows)


def build_sections():
    """(메시지 섹션 리스트, 순위에 오른 종목 리스트) 반환"""
    kst = datetime.utcnow() + timedelta(hours=9)
    stamp = kst.strftime("%Y-%m-%d %H:%M")
    sections = [f"✨📊 한국장 랭킹 리포트 📊✨\n🕗 {stamp} KST"]
    failures = []
    picked = []

    # 1) 거래대금
    try:
        kospi = fetch_market("KOSPI")
        kosdaq = fetch_market("KOSDAQ")

        def top(rows):
            return sorted(rows, key=lambda x: x["value"], reverse=True)[:TOP_N]

        if SPLIT_BY_MARKET:
            k, q = top(kospi), top(kosdaq)
            picked += k + q
            sections.append(
                section(
                    f"💰🔥 코스피 거래대금 TOP {TOP_N} 🔥💰",
                    "(ETF·ETN 제외)",
                    [value_row(i, r, False) for i, r in enumerate(k, 1)],
                )
            )
            sections.append(
                section(
                    f"💰🔥 코스닥 거래대금 TOP {TOP_N} 🔥💰",
                    "(ETF·ETN 제외)",
                    [value_row(i, r, False) for i, r in enumerate(q, 1)],
                )
            )
        else:
            t = top(kospi + kosdaq)
            picked += t
            sections.append(
                section(
                    f"💰🔥 거래대금 TOP {TOP_N} 🔥💰",
                    "(코스피+코스닥 · ETF·ETN 제외)",
                    [value_row(i, r, True) for i, r in enumerate(t, 1)],
                )
            )
    except Exception as e:
        failures.append(f"거래대금: {type(e).__name__}: {e}")
        sections.append(f"{LINE}\n💰 거래대금 순위 수집 실패\n{type(e).__name__}: {e}")

    # 2) 인기종목
    try:
        pop = fetch_popular()[:TOP_N]
        picked += pop
        sections.append(
            section(
                f"🔎🌟 인기종목 TOP {TOP_N} 🌟🔎",
                "(네이버 증권 TOP · ETF·ETN 제외)",
                [popular_row(i, r) for i, r in enumerate(pop, 1)],
            )
        )
    except Exception as e:
        failures.append(f"인기종목: {type(e).__name__}: {e}")
        sections.append(f"{LINE}\n🔎 인기종목 순위 수집 실패\n{type(e).__name__}: {e}")

    if len(failures) == 2:
        raise RuntimeError(" | ".join(failures))
    return sections, picked


# ------------------------------------------------------------------ 텔레그램
def send_telegram(text: str, use_html: bool = False):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise ValueError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 환경변수가 비어 있습니다.")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}
    if use_html:
        payload["parse_mode"] = "HTML"
    resp = requests.post(url, json=payload, timeout=15)
    if not resp.ok:  # URL(토큰 포함)은 빼고 텔레그램이 알려준 원인만 표시
        raise RuntimeError(f"Telegram {resp.status_code}: {resp.text[:300]}")


def send_sections(sections: list[str]):
    """한 메시지(4096자 제한) 안에 들어가면 합쳐서, 넘치면 섹션별로 나눠 전송."""
    full = "\n\n\n".join(sections)
    if len(full) <= 4000:
        send_telegram(full)
        return
    for s in sections:
        send_telegram(s)


# ------------------------------------------------------------ 상태 저장/불러오기
def dedupe(picked: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in picked:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        out.append({"name": r["name"], "code": r["code"], "rate": r.get("rate", 0)})
    return out


def save_state(picked: list[dict]):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(dedupe(picked), f, ensure_ascii=False, indent=1)


def load_state() -> list[dict]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# ------------------------------------------------- 뉴스 / 재료 / 실적 수집
# (네이버 뉴스·공시 페이지는 410으로 막혀서 구글 뉴스 검색으로 대체)
def esc(t) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc_attr(t) -> str:
    return esc(t).replace('"', "&quot;")


def _google_rss(query: str, limit: int) -> list[dict]:
    last_err = None
    for _ in range(2):
        try:
            res = requests.get(
                "https://news.google.com/rss/search",
                params={"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
                headers={"User-Agent": HEADERS["User-Agent"]},
                timeout=15,
            )
            res.raise_for_status()
            break
        except Exception as e:
            last_err = e
            time.sleep(2)
    else:
        raise last_err
    root = ET.fromstring(res.content)
    out, seen = [], set()
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3].strip()
        if not title or not link or title in seen:
            continue
        seen.add(title)
        out.append({"title": title, "url": link, "src": src})
        if len(out) >= limit:
            break
    return out


def fetch_google(name: str):
    """(최신 뉴스, 재료·공시·실적 뉴스) 구글 뉴스 검색 결과"""
    news = _google_rss(f'"{name}" 주식 when:{GOOGLE_NEWS_DAYS}d', 8)
    time.sleep(0.3)
    try:
        mats = _google_rss(
            f'"{name}" (공시 OR 영업이익 OR 매출 OR 수주 OR 계약 OR 테마 OR 관련주) when:{GOOGLE_NEWS_DAYS}d', 8
        )
    except Exception as e:
        print(f"[안내] 재료검색 실패 {name}: {e}")
        mats = []
    seen = {x["title"] for x in news}
    return news, [x for x in mats if x["title"] not in seen]


def fetch_earnings(code: str) -> str:
    """최근 분기 매출/영업이익 (억원). 응답 구조가 다르면 빈 문자열."""
    res = requests.get(
        f"https://m.stock.naver.com/api/stock/{code}/finance/quarter",
        headers=HEADERS,
        timeout=15,
    )
    res.raise_for_status()
    info = res.json().get("financeInfo", {})
    titles = [t for t in info.get("trTitleList", []) if t.get("isConsensus") != "Y"]
    if not titles:
        return ""
    rows = {r.get("title"): r.get("columns", {}) for r in info.get("rowList", [])}

    def val(row_name, key):
        return (rows.get(row_name, {}).get(key, {}) or {}).get("value", "-")

    last = titles[-1]
    return (
        f"{last.get('title', '')} 매출 {val('매출액', last['key'])}억 · "
        f"영업이익 {val('영업이익', last['key'])}억"
    )


def theme_tags(texts: list[str]) -> list[str]:
    blob = " ".join(texts)
    return [tag for tag, kws in THEME_KEYWORDS.items() if any(k in blob for k in kws)]


def collect_stock_data(s: dict) -> dict:
    d = {"news": [], "mats": [], "earn": "", "tags": []}
    try:
        d["news"], d["mats"] = fetch_google(s["name"])
    except Exception as e:
        print(f"[안내] 구글뉴스 실패 {s['name']}: {e}")
    try:
        d["earn"] = fetch_earnings(s["code"])
    except Exception as e:
        print(f"[안내] 실적 실패 {s['name']}: {e}")
    d["tags"] = theme_tags([x["title"] for x in d["news"] + d["mats"]])
    return d


# ------------------------------------------------------------ 메시지 조립(재료)
SEP = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈"


def a_tag(item: dict, tlen: int) -> str:
    t = item["title"]
    t = t if len(t) <= tlen else t[: tlen - 1] + "…"
    src = (item.get("src") or "")[:8]
    return f'  ▫️ <a href="{esc_attr(item["url"])}">{esc(t)}</a>' + (f" · {esc(src)}" if src else "")


def visible_len(text: str) -> int:
    """텔레그램 글자수 제한은 태그·URL을 뺀 보이는 글자 기준."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def count_entities(text: str) -> int:
    """텔레그램은 메시지당 서식/링크 개수 제한(100)이 있음."""
    return len(re.findall(r"<(?:b|a|i)[ >]", text))


def format_stock(rank: int, s: dict, d: dict, g: int, m: int, tlen: int) -> str:
    code, name = s["code"], s["name"]
    rate = float(s.get("rate") or 0)
    lines = [SEP, f"{rank_mark(rank)} <b>{esc(name)}</b> ({code})  {rate_mark(rate)}{rate:+.2f}%"]
    if d["tags"]:
        lines.append("🏷 " + " / ".join(d["tags"]))
    if d["earn"]:
        lines.append(f"📈 {esc(d['earn'])}")
    more = f"https://news.google.com/search?q={quote(name + ' 주식')}&hl=ko&gl=KR&ceid=KR:ko"
    lines.append(
        f'🔗 <a href="https://finance.naver.com/item/main.naver?code={code}">종목페이지</a>'
        f'  ·  🌐 <a href="{esc_attr(more)}">구글뉴스 전체</a>'
    )
    if d["news"][:g]:
        lines.append("📰 <b>뉴스</b>")
        lines += [a_tag(x, tlen) for x in d["news"][:g]]
    if d["mats"][:m]:
        lines.append("💡 <b>재료·공시·실적</b>")
        lines += [a_tag(x, tlen) for x in d["mats"][:m]]
    if not (d["news"][:g] or d["mats"][:m]):
        lines.append("💤 최근 기사 없음")
    return "\n".join(lines)


# 한 메시지에 안 들어가면 위에서부터 차례로 줄여서 재시도: (뉴스 수, 재료 수, 제목 길이)
LADDER = [(5, 4, 48), (4, 3, 44), (3, 3, 44), (3, 2, 42), (2, 2, 40), (2, 1, 38), (1, 1, 34), (1, 0, 28)]
MAX_CHARS = 4000
MAX_ENTITIES = 90
NEWS_MESSAGES = int(os.environ.get("NEWS_MESSAGES", "5"))  # 재료 리포트를 나눠 보낼 메시지 수


def split_groups(targets: list[dict], k: int) -> list[tuple]:
    base, extra = divmod(len(targets), k)
    groups, pos = [], 0
    for i in range(k):
        size = base + (1 if i < extra else 0)
        if size:
            groups.append((pos + 1, targets[pos : pos + size]))
        pos += size
    return groups


def build_messages(targets: list[dict], data: dict, note: str) -> list[str]:
    """종목을 NEWS_MESSAGES개 메시지로 균등 분할. 길면 기사 수를 줄일 뿐 종목은 빼지 않음."""
    kst = datetime.utcnow() + timedelta(hours=9)
    stamp = f"🕗 {kst:%Y-%m-%d %H:%M} KST · {note}"
    groups = split_groups(targets, NEWS_MESSAGES)
    total = len(groups)

    def too_big(t: str) -> bool:
        return visible_len(t) > MAX_CHARS or count_entities(t) > MAX_ENTITIES

    msgs = []
    for ci, cfg in enumerate(LADDER):
        msgs, ok = [], True
        for gi, (start, grp) in enumerate(groups):
            part = f" ({gi + 1}/{total})" if total > 1 else ""
            head = f"🗞️✨ <b>종목 재료 리포트{part}</b> ✨🗞️"
            if gi == 0:
                head += "\n" + stamp
            blocks = [format_stock(start + i, x, data[x["code"]], *cfg) for i, x in enumerate(grp)]
            msg = head + "\n\n" + "\n\n".join(blocks)
            msgs.append(msg)
            if too_big(msg):
                ok = False
        if ok:
            print(f"[안내] {total}개 메시지 / 압축 단계 {ci + 1}/{len(LADDER)} 적용 {cfg}")
            return msgs
    print("[안내] 최소 단계로도 길어 그대로 전송 시도")
    return msgs


def send_news_message(m: str):
    try:
        send_telegram(m, use_html=True)
    except Exception as e:
        print(f"[안내] HTML 전송 실패, 일반 텍스트로 재시도: {e}")
        plain = html.unescape(re.sub(r"<[^>]+>", "", m))[:4000]
        send_telegram(plain)


# ------------------------------------------------------------------ 실행 모드
def run_news():
    targets = load_state()
    note = "직전 순위 종목 기준"
    if not targets:
        _, picked = build_sections()
        targets = dedupe(picked)
        note = "저장된 순위가 없어 현재 순위 기준"
    targets = targets[:NEWS_MAX_STOCKS]
    if not targets:
        send_telegram("🗞️ 재료를 조회할 종목이 없습니다.")
        return

    data = {}
    for s in targets:
        data[s["code"]] = collect_stock_data(s)
        time.sleep(0.3)

    for m in build_messages(targets, data, note):
        send_news_message(m)
        time.sleep(1.2)


def run_rank():
    secs, picked = build_sections()
    send_sections(secs)
    save_state(picked)


def pick_mode() -> str:
    m = os.environ.get("RUN_MODE", "auto").strip().lower()
    if m in ("rank", "news"):
        return m
    kst = datetime.utcnow() + timedelta(hours=9)
    return "rank" if kst.hour % 2 == 1 else "news"  # 홀수 시=순위, 짝수 시=재료


if __name__ == "__main__":
    mode = pick_mode()
    print(f"모드: {mode}")
    try:
        run_rank() if mode == "rank" else run_news()
    except Exception as e:
        msg = f"⚠️ {mode} 실패\n{type(e).__name__}: {e}"
        print(msg)
        send_telegram(msg)
        raise SystemExit(1)
    print("전송 완료")
