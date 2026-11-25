import os
import re
from bs4 import BeautifulSoup
import requests

TELEGRAM_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or ""
).strip()
CHAT_ID = (
    os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID") or ""
).strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://stock.naver.com/market/stock/kr/stocklist/top",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8"
    ),
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
          r"(ETN|ETF|레버리지|인버스|곱버스|선물|채권|액티브|커버드콜|2X|3X|USD|달러|금현물|원유|S&P|나스닥|코스피|코스닥)",
          n_upper,
      ):
        return True
  return False


def decode_response(res: requests.Response) -> str:
  raw = res.content
  for enc in ("utf-8", "euc-kr", "cp949"):
    try:
      text = raw.decode(enc)
      if "종목명" in text or "인기검색" in text or "stockName" in text or "code=" in text:
        return text
    except UnicodeDecodeError:
      continue
  return raw.decode("utf-8", errors="replace")


def pick_first(d: dict, keys: list[str], default: str = "") -> str:
  for k in keys:
    if k in d and d[k] is not None and str(d[k]).strip() != "":
      return str(d[k]).strip()
  return default


def walk_items(obj, out: list):
  if isinstance(obj, list):
    for x in obj:
      walk_items(x, out)
  elif isinstance(obj, dict):
    name = pick_first(obj, ["stockName", "itemName", "name", "reutersName", "stkNm"])
    code = pick_first(obj, ["itemCode", "stockCode", "code", "symbolCode", "isuCd"])
    if name and re.fullmatch(r"[0-9A-Za-z]{6}", code):
      out.append(obj)
    for v in obj.values():
      if isinstance(v, (dict, list)):
        walk_items(v, out)


def try_json_apis(diagnostics: list[str]) -> list[tuple[str, str, str, str]]:
  api_candidates = [
      ("https://m.stock.naver.com/api/stocks/top", {"page": 1, "pageSize": 50}),
      ("https://m.stock.naver.com/api/stock/top", {"page": 1, "pageSize": 50}),
      ("https://stock.naver.com/api/stock/top", {"page": 1, "pageSize": 50}),
  ]
  for url, params in api_candidates:
    try:
      res = requests.get(url, headers=HEADERS, params=params, timeout=12)
      diagnostics.append(f"API {url} -> HTTP {res.status_code}")
      if res.status_code != 200:
        continue
      data = res.json()
      raw_items = []
      walk_items(data, raw_items)
      if not raw_items:
        continue

      results = []
      seen = set()
      for item in raw_items:
        name = pick_first(item, ["stockName", "itemName", "name", "reutersName", "stkNm"])
        code = pick_first(item, ["itemCode", "stockCode", "code", "symbolCode", "isuCd"])
        itype = pick_first(item, ["itemType", "type", "marketType", "stockType"])
        price = pick_first(item, ["closePrice", "nowVal", "currentPrice", "price", "nv"])
        rate = pick_first(item, ["fluctuationsRatio", "changeRate", "rat", "prdyCtrt", "cr"])

        if not code or code in seen or is_etf_or_etn(name, itype):
          continue
        seen.add(code)
        results.append((name, code, price, rate))
        if len(results) >= 20:
          return results
    except Exception as e:
      diagnostics.append(f"API {url} -> 예외: {e}")
  return []


def try_html_pages(diagnostics: list[str]) -> list[tuple[str, str, str, str]]:
  page_urls = [
      "https://finance.naver.com/sise/lastsearch2.naver",
      "https://stock.naver.com/market/stock/kr/stocklist/top",
  ]
  for url in page_urls:
    try:
      res = requests.get(url, headers=HEADERS, timeout=15)
      html = decode_response(res)
      soup = BeautifulSoup(html, "html.parser")
      title = soup.title.get_text(strip=True) if soup.title else "제목없음"
      diagnostics.append(
          f"HTML {url} -> HTTP {res.status_code}, len={len(html)}, title='{title}'"
      )
      if res.status_code != 200:
        continue

      results = []
      seen = set()
      for tr in soup.select("table tr"):
        link = tr.select_one('a[href*="code="], a[href*="/stock/"]')
        if not link:
          continue
        name = link.get_text(" ", strip=True)
        href = link.get("href", "")
        m = re.search(r"(?:code=|/stock/)([0-9A-Za-z]{6})", href)
        if not m or not name:
          continue
        code = m.group(1)
        if code in seen or is_etf_or_etn(name):
          continue

        cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
        price = cells[3] if len(cells) > 3 else ""
        rate = cells[5] if len(cells) > 5 else ""

        seen.add(code)
        results.append((name, code, price, rate))
        if len(results) >= 20:
          return results

      if results:
        return results
    except Exception as e:
      diagnostics.append(f"HTML {url} -> 예외: {e}")
  return []


def get_popular_top20_message() -> str:
  diagnostics: list[str] = []
  items = try_json_apis(diagnostics)
  if not items:
    items = try_html_pages(diagnostics)

  if not items:
    diag_text = "\n - ".join(diagnostics)
    raise RuntimeError(
        "인기검색어 순위 데이터를 추출하지 못했습니다.\n - " + diag_text
    )

  lines = ["📊 한국장 인기검색어 순위 TOP 20 (ETF·ETN 제외)\n"]
  for rank, (name, code, price, rate) in enumerate(items[:20], 1):
    price_str = f"{price}원" if price and not price.endswith("원") else (price or "-")
    rate_str = f" ({rate})" if rate else ""
    lines.append(f"{rank}. {name} ({code}) : {price_str}{rate_str}")

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
  msg = get_popular_top20_message()
  send_telegram(msg)
  print("전송 완료")
