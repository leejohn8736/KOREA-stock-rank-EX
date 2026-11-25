import os
import re
from bs4 import BeautifulSoup
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def pick_first(d: dict, keys: list[str], default=""):
  for k in keys:
    if k in d and d[k] is not None and str(d[k]).strip() != "":
      return d[k]
  return default


def extract_items(data):
  if isinstance(data, list):
    return data
  if isinstance(data, dict):
    for key in (
        "stocks",
        "items",
        "list",
        "result",
        "data",
        "stockList",
        "topStocks",
    ):
      val = data.get(key)
      if isinstance(val, list) and len(val) > 0:
        return val
      if isinstance(val, dict):
        for inner_key in (
            "stocks",
            "items",
            "list",
            "stockList",
            "topStocks",
        ):
          if isinstance(val.get(inner_key), list) and len(val[inner_key]) > 0:
            return val[inner_key]
  return []


def fetch_from_naver_json_candidates():
  candidates = [
      "https://m.stock.naver.com/api/stocks/top",
      "https://m.stock.naver.com/api/stock/top",
      "https://stock.naver.com/api/stock/top",
  ]
  for url in candidates:
    try:
      res = requests.get(
          url, headers=HEADERS, params={"page": 1, "pageSize": 20}, timeout=12
      )
      if res.status_code != 200:
        continue
      data = res.json()
      stocks = extract_items(data)
      if not stocks:
        continue

      lines = ["📊 *한국장 랭킹 순위*\n"]
      for idx, item in enumerate(stocks[:15], 1):
        name = pick_first(
            item, ["stockName", "name", "itemName", "reutersName"]
        )
        code = pick_first(
            item, ["itemCode", "code", "stockCode", "symbolCode"]
        )
        price = pick_first(
            item, ["closePrice", "nowVal", "currentPrice", "price"]
        )
        rate = pick_first(
            item, ["fluctuationsRatio", "changeRate", "rat", "prdyCtrt"]
        )
        if name:
          lines.append(f"{idx}. *{name}* ({code}) : {price}원 ({rate}%)")
      if len(lines) > 1:
        return "\n".join(lines)
    except Exception:
      continue
  return None


def fetch_from_naver_finance_popular():
  url = "https://finance.naver.com/sise/lastsearch2.naver"
  res = requests.get(url, headers=HEADERS, timeout=15)
  res.encoding = "euc-kr"
  res.raise_for_status()

  soup = BeautifulSoup(res.text, "html.parser")
  rows = soup.select("table.type_5 tr")

  items = []
  for tr in rows:
    cols = [td.get_text(" ", strip=True) for td in tr.select("td")]
    if len(cols) < 5:
      continue
    link = tr.select_one("a.tltle")
    if not link:
      continue

    name = link.get_text(strip=True)
    href = link.get("href", "")
    m = re.search(r"code=(\d+)", href)
    code = m.group(1) if m else ""

    price = cols[2] if len(cols) > 2 else ""
    rate = cols[4] if len(cols) > 4 else ""
    items.append((name, code, price, rate))
    if len(items) >= 15:
      break

  if not items:
    raise RuntimeError(
        "인기검색종목 데이터를 파싱하지 못했습니다. (응답 길이:"
        f" {len(res.text)})"
    )

  lines = ["📊 *한국장 인기검색 랭킹 TOP 15*\n"]
  for idx, (name, code, price, rate) in enumerate(items, 1):
    lines.append(f"{idx}. *{name}* ({code}) : {price}원 ({rate})")

  return "\n".join(lines)


def get_top_stocks_message():
  msg = fetch_from_naver_json_candidates()
  if msg:
    return msg
  return fetch_from_naver_finance_popular()


def send_telegram(text):
  if not TELEGRAM_TOKEN or not CHAT_ID:
    raise ValueError("TELEGRAM_TOKEN 또는 CHAT_ID 환경변수가 비어 있습니다.")

  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
  resp = requests.post(url, json=payload, timeout=15)
  resp.raise_for_status()


if __name__ == "__main__":
  msg = get_top_stocks_message()
  send_telegram(msg)
  print("전송 완료")
