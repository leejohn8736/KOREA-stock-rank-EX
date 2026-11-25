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
MAX_PAGES = 5          # 시장별 시총 상위 100개 x 5페이지 = 500종목에서 거래대금 정렬
SPLIT_BY_MARKET = False  # True: 코스피 20 + 코스닥 20 따로 / False: 합쳐서 20

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


# ---------- 1순위: 네이버 모바일 API ----------
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


# ---------- 2순위: 네이버 PC 증권 거래상위 표 ----------
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


def fmt_row(rank: int, r: dict, show_market: bool) -> str:
    tag = f"[{'코스피' if r['market'] == 'KOSPI' else '코스닥'}] " if show_market else ""
    return (
        f"{rank}. {tag}{r['name']} ({r['code']}) : "
        f"{int(r['price']):,}원 ({r['rate']:+.2f}%) | {fmt_value(r['value'])}"
    )


def build_message() -> str:
    kst = datetime.utcnow() + timedelta(hours=9)
    stamp = kst.strftime("%Y-%m-%d %H:%M")
    kospi = fetch_market("KOSPI")
    kosdaq = fetch_market("KOSDAQ")

    def top(rows):
        return sorted(rows, key=lambda x: x["value"], reverse=True)[:TOP_N]

    if SPLIT_BY_MARKET:
        lines = [f"📊 코스피 거래대금 TOP {TOP_N} ({stamp} KST · ETF·ETN 제외)\n"]
        lines += [fmt_row(i, r, False) for i, r in enumerate(top(kospi), 1)]
        lines += [f"\n📊 코스닥 거래대금 TOP {TOP_N}\n"]
        lines += [fmt_row(i, r, False) for i, r in enumerate(top(kosdaq), 1)]
    else:
        lines = [f"📊 코스피·코스닥 거래대금 TOP {TOP_N} ({stamp} KST · ETF·ETN 제외)\n"]
        lines += [fmt_row(i, r, True) for i, r in enumerate(top(kospi + kosdaq), 1)]
    return "\n".join(lines)


def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise ValueError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 환경변수가 비어 있습니다.")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    resp.raise_for_status()


if __name__ == "__main__":
    try:
        msg = build_message()
    except Exception as e:
        msg = f"⚠️ 순위 수집 실패\n{type(e).__name__}: {e}"
        print(msg)
        send_telegram(msg)
        raise SystemExit(1)
    send_telegram(msg)
    print("전송 완료")
