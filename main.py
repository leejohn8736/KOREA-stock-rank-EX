import html
import json
import os
import re
import time
from datetime import datetime, timedelta

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
NEWS_PER_STOCK = 3
NOTICE_PER_STOCK = 2

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
    resp.raise_for_status()


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


# ------------------------------------------------- 뉴스 / 공시 / 실적 수집
def _news_link(href: str) -> str:
    oid = re.search(r"office_id=(\d+)", href)
    aid = re.search(r"article_id=(\d+)", href)
    if oid and aid:
        return f"https://n.news.naver.com/mnews/article/{oid.group(1)}/{aid.group(1)}"
    if href.startswith("/"):
        return "https://finance.naver.com" + href
    return href


def _fetch_list(path: str, code: str, limit: int) -> list[dict]:
    res = requests.get(
        f"https://finance.naver.com/item/{path}",
        params={"code": code, "page": 1},
        headers={**HEADERS, "Referer": f"https://finance.naver.com/item/news.naver?code={code}"},
        timeout=15,
    )
    res.raise_for_status()
    soup = BeautifulSoup(res.content.decode("euc-kr", errors="ignore"), "html.parser")
    out, seen = [], set()
    for tr in soup.select("tr"):
        if "relation_lst" in (tr.get("class") or []):
            continue
        a = tr.select_one("td.title a")
        if a is None:
            continue
        title = a.get_text(strip=True)
        if not title or title in seen:
            continue
        seen.add(title)
        info = tr.select_one("td.info")
        date = tr.select_one("td.date")
        out.append(
            {
                "title": title,
                "url": _news_link(a.get("href", "")),
                "src": info.get_text(strip=True) if info else "",
                "date": date.get_text(strip=True) if date else "",
            }
        )
        if len(out) >= limit:
            break
    return out


def fetch_news(code: str) -> list[dict]:
    return _fetch_list("news_news.naver", code, NEWS_PER_STOCK)


def fetch_notices(code: str) -> list[dict]:
    return _fetch_list("news_notice.naver", code, NOTICE_PER_STOCK)


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


def a_tag(item: dict) -> str:
    t = item["title"]
    t = t if len(t) <= 55 else t[:54] + "…"
    meta = " · ".join(x for x in (item["src"], item["date"]) if x)
    return f'• <a href="{html.escape(item["url"], quote=True)}">{html.escape(t)}</a>' + (
        f" <i>({html.escape(meta)})</i>" if meta else ""
    )


def build_stock_block(rank: int, s: dict) -> str:
    code, name = s["code"], s["name"]
    news, notices, earn = [], [], ""
    try:
        news = fetch_news(code)
    except Exception as e:
        print(f"[안내] 뉴스 실패 {name}: {e}")
    try:
        notices = fetch_notices(code)
    except Exception as e:
        print(f"[안내] 공시 실패 {name}: {e}")
    try:
        earn = fetch_earnings(code)
    except Exception as e:
        print(f"[안내] 실적 실패 {name}: {e}")

    lines = [f"<b>{rank}. {html.escape(name)}</b> ({code})"]
    tags = theme_tags([n["title"] for n in news + notices])
    if tags:
        lines.append("🏷 " + " / ".join(tags))
    if earn:
        lines.append(f"📈 {html.escape(earn)}")
    if news:
        lines.append("📰 뉴스")
        lines += [a_tag(n) for n in news]
    if notices:
        lines.append("📑 공시")
        lines += [a_tag(n) for n in notices]
    if not (news or notices or earn):
        lines.append("(수집된 재료 없음)")
    lines.append(f'🔗 <a href="https://finance.naver.com/item/main.naver?code={code}">종목 페이지</a>')
    return "\n".join(lines)


# ------------------------------------------------------------------ 실행 모드
def run_news():
    targets = load_state()
    note = "직전 순위 종목 기준"
    if not targets:
        _, picked = build_sections()
        targets = dedupe(picked)
        note = "저장된 순위가 없어 현재 순위 기준"
    targets = targets[:NEWS_MAX_STOCKS]

    kst = datetime.utcnow() + timedelta(hours=9)
    header = f"🗞️ <b>종목 재료 리포트</b>\n🕗 {kst:%Y-%m-%d %H:%M} KST · {note}"

    chunks, cur = [], header
    for i, s in enumerate(targets, 1):
        block = build_stock_block(i, s)
        if len(cur) + len(block) + 2 > 3800:
            chunks.append(cur)
            cur = block
        else:
            cur += "\n\n" + block
        time.sleep(0.3)
    chunks.append(cur)
    for c in chunks:
        send_telegram(c, use_html=True)


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
