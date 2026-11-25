from datetime import datetime, timedelta
import os
import re
from bs4 import BeautifulSoup
from pykrx import stock
import requests

TELEGRAM_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or ""
).strip()
CHAT_ID = (
    os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID") or ""
).strip()

# 원하시면 브라우저 개발자 도구(F12 -> Network -> Fetch/XHR)에서 확인한
# 실제 Npay 증권 랭킹 JSON API 주소를 여기에 넣으면 최우선으로 시도합니다.
CUSTOM_RANK_API_URL = os.environ.get("CUSTOM_RANK_API_URL", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://stock.naver.com/market/stock/kr/stocklist/top",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

ETF_ETN_BRANDS = (
    "KODEX",
    "TIGER",
    "KBSTAR",
    "RISE",
    "ACE",
    "SOL",
    "HANARO",
    "PLUS",
    "TIMEFOLIO",
    "WOORI",
    "KOSEF",
    "ARIRANG",
    "TREX",
    "파워",
    "마이다스",
    "네비게이터",
    "히어로즈",
    "KIWOOM",
    "TDF",
)


def is_etf_or_etn(name: str, item_type: str = "") -> bool:
  n = (name or "").strip()
  n_upper = n.upper()
  t_upper = (item_type or "").upper()

  if "ETF" in t_upper or "ETN" in t_upper:
    return True
  if re.search(r"\b(ETF|ETN)\b", n_upper):
    return True
  if n_upper.endswith(" ETF") or n_upper.endswith(" ETN"):
    return True

  for prefix in ETF_ETN_BRANDS:
    p_upper = prefix.upper()
    if n_upper.startswith(p_upper + " ") or n_upper.startswith(p_upper):
      if re.search(
          r"(ETN|ETF|레버리지|인버스|곱버스|선물|채권|액티브|커버드콜|2X|3X|USD|달러|금현물|원유|S&P|나스닥|코스피|코스닥|채권혼합|국고채)",
          n_upper,
      ):
        return True
  return False


def pick_first(d: dict, keys: list[str], default: str = "") -> str:
  for k in keys:
    if k in d and d[k] is not None and str(d[k]).strip() != "":
      return str(d[k]).strip()
  return default


def walk_stock_dicts(obj, out: list):
  if isinstance(obj, list):
    for item in obj:
      walk_stock_dicts(item, out)
  elif isinstance(obj, dict):
    name = pick_first(
        obj, ["stockName", "itemName", "name", "reutersName", "stkNm"]
    )
    code = pick_first(
        obj, ["itemCode", "stockCode", "code", "symbolCode", "isuCd"]
    )
    if name and re.fullmatch(r"[0-9A-Za-z]{6}", code):
      out.append(obj)
    for v in obj.values():
      if isinstance(v, (dict, list)):
        walk_stock_dicts(v, out)


def try_custom_api() -> tuple[str, list[tuple[str, str, str]]] | None:
  if not CUSTOM_RANK_API_URL:
    return None
  try:
    res = requests.get(CUSTOM_RANK_API_URL, headers=HEADERS, timeout=12)
    res.raise_for_status()
    data = res.json()
    raw_items = []
    walk_stock_dicts(data, raw_items)

    rows = []
    seen = set()
    for item in raw_items:
      name = pick_first(
          item, ["stockName", "itemName", "name", "reutersName", "stkNm"]
      )
      code = pick_first(
          item, ["itemCode", "stockCode", "code", "symbolCode", "isuCd"]
      )
      itype = pick_first(item, ["itemType", "type", "marketType", "stockType"])
      price = pick_first(
          item, ["closePrice", "nowVal", "currentPrice", "price", "nv"]
      )
      rate = pick_first(
          item, ["fluctuationsRatio", "changeRate", "rat", "prdyCtrt", "cr"]
      )

      if not code or code in seen or is_etf_or_etn(name, itype):
        continue
      seen.add(code)
      price_str = (
          f"{price}원" if price and not price.endswith("원") else (price or "-")
      )
      rate_str = f" ({rate}%)" if rate and not rate.endswith("%") else (f" ({rate})" if rate else "")
      rows.append((name, code, f"{price_str}{rate_str}"))
      if len(rows) >= 20:
        break

    if rows:
      return "Npay 증권 랭킹 TOP 20 (ETF·ETN 제외)", rows
  except Exception as e:
    print(f"[안내] CUSTOM_RANK_API_URL 조회 실패: {e}")
  return None


def get_latest_trading_date() -> str:
  # KST 기준 최근 영업일(최대 10일 전까지 조회)
  kst_now = datetime.utcnow() + timedelta(hours=9)
  for delta in range(0, 10):
    target = (kst_now - timedelta(days=delta)).strftime("%Y%m%d")
    df = stock.get_market_ohlcv(target, market="KOSPI")
    if df is not None and not df.empty and "거래대금" in df.columns:
      if df["거래대금"].sum() > 0:
        return target
  return kst_now.strftime("%Y%m%d")


def try_krx_top20() -> tuple[str, list[tuple[str, str, str]]]:
  biz_date = get_latest_trading_date()
  df = stock.get_market_ohlcv(biz_date, market="ALL")
  if df is None or df.empty:
    raise RuntimeError(f"KRX 시세 데이터를 불러오지 못했습니다. (기준일: {biz_date})")

  df_sorted = df.sort_values(by="거래대금", ascending=False)

  rows = []
  seen = set()
  for ticker, row in df_sorted.iterrows():
    code = str(ticker).strip()
    if code in seen:
      continue
    name = stock.get_market_ticker_name(code) or ""
    if not name or is_etf_or_etn(name):
      continue

    close_val = int(row.get("종가", 0))
    rate_val = float(row.get("등락률", 0.0))
    amount_억 = int(row.get("거래대금", 0) // 100_000_000)

    seen.add(code)
    info = f"{close_val:,}원 ({rate_val:+.2f}%) | {amount_억:,}억"
    rows.append((name, code, info))

    if len(rows) >= 20:
      break

  if not rows:
    raise RuntimeError("필터링 후 남은 종목이 없습니다.")

  formatted_date = f"{biz_date[:4]}-{biz_date[4:6]}-{biz_date[6:]}"
  return f"한국장 주목 종목 TOP 20 ({formatted_date} · ETF·ETN 제외)", rows


def build_message() -> str:
  custom = try_custom_api()
  if custom:
    title, rows = custom
  else:
    title, rows = try_krx_top20()

  lines = [f"📊 {title}\n"]
  for rank, (name, code, info) in enumerate(rows[:20], 1):
    lines.append(f"{rank}. {name} ({code}) : {info}")

  return "\n".join(lines)


def send_telegram(text: str):
  if not TELEGRAM_TOKEN or not CHAT_ID:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 환경변수가 비어 있습니다."
    )

  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  payload = {
      "chat_id": CHAT_ID,
      "text": text,
      "disable_web_page_preview": True,
  }
  resp = requests.post(url, json=payload, timeout=15)
  resp.raise_for_status()


if __name__ == "__main__":
  msg = build_message()
  send_telegram(msg)
  print("전송 완료")
