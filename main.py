import json
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

ETF_ETN_BRAND_PREFIXES = (
    "KODEX", "TIGER", "KBSTAR", "RISE", "ACE", "SOL", "HANARO", "PLUS",
    "TIMEFOLIO", "WOORI", "KOSEF", "ARIRANG", "TREX", "파워", "마이다스",
    "네비게이터", "히어로즈", "파인", "BNK", "KTOP", "VITA", "KIWOOM",
)


def is_etf_or_etn(name: str, item_type: str = "", code: str = "") -> bool:
  n_upper = (name or "").upper().strip()
  t_upper = (item_type or "").upper().strip()

  if any(k in t_upper for k in ("ETF", "ETN")):
    return True
  if re.search(r"\b(ETF|ETN)\b", n_upper):
    return True
  if n_upper.endswith(" ETF") or n_upper.endswith(" ETN"):
    return True
  if any(n_upper.startswith(p.upper() + " ") or n_upper.startswith(p.upper()) for p in ETF_ETN_BRAND_PREFIXES):
    # 일반 기업명 오탐 방지를 위해 ETN/ETF 관련 단어나 레버리지/인버스/선물/채권/액티브/고배당 패턴 동반 여부 체크
    if re.search(r"(ETN|ETF|레버리지|인버스|곱버스|선물|채권|액티브|커버드콜|테마|2X|3X)", n_upper):
      return True
  return False


def pick_first(d: dict, keys: list[str], default=""):
  for k in keys:
    if k in d and d[k] is not None and str(d[k]).strip() != "":
      return str(d[k]).strip()
  return default


def walk_find_stock_dicts(obj, found: list):
  if isinstance(obj, list):
    for elem in obj:
      walk_find_stock_dicts(elem, found)
  elif isinstance(obj, dict):
    name = pick_first(obj, ["stockName", "itemName", "name", "reutersName", "stkNm"])
    code = pick_first(obj, ["itemCode", "stockCode", "code", "symbolCode", "isuCd"])
    if name and re.fullmatch(r"[0-9A-Za-z]{6}", code):
      found.append(obj)
    for v in obj.values():
      if isinstance(v, (dict, list)):
        walk_find_stock_dicts(v, found)


def fetch_from_json_endpoints() -> list[tuple[str, str, str, str]]:
  endpoints = [
      ("https://stock.naver.com/api/stock/top", {"page": 1, "pageSize": 50}),
      ("https://m.stock.naver.com/api/stocks/top", {"page": 1, "pageSize": 50}),
      ("https://m.stock.naver.com/api/stock/top", {"page": 1, "pageSize": 50}),
  ]
  for url, params in endpoints:
    try:
      res = requests.get(url, headers=HEADERS, params=params, timeout=12)
      if res.status_code != 200:
        continue
      data = res.json()
      candidates = []
      walk_find_stock_dicts(data, candidates)
      if not candidates:
        continue

      results = []
      seen = set()
      for item in candidates:
        name = pick_first(item, ["stockName", "itemName", "name", "reutersName", "stkNm"])
        code = pick_first(item, ["itemCode", "stockCode", "code", "symbolCode", "isuCd"])
        itype = pick_first(item, ["itemType", "type", "marketType", "stockType", "etfType"])
        price = pick_first(item, ["closePrice", "nowVal", "currentPrice", "price", "nv"])
        rate = pick_first(item, ["fluctuationsRatio", "changeRate", "rat", "prdyCtrt", "cr"])

        if not code or code in seen:
          continue
        if is_etf_or_etn(name, itype, code):
          continue

        seen.add(code)
        results.append((name, code, price, rate))
        if len(results) >= 20:
          return results
    except Exception:
      continue
  return []


def fetch_from_npay_top_page() -> list[tuple[str, str, str, str]]:
  urls = [
      "https://stock.naver.com/market/stock/kr/stocklist/top",
      "https://finance.naver.com/sise/lastsearch2.naver",
  ]
  for url in urls:
    try:
      res = requests.get(url, headers=HEADERS, timeout=15)
      if res.status_code != 200:
        continue
      raw = res.content
      html = ""
      for enc in ("utf-8", "euc-kr", "cp949"):
        try:
          html = raw.decode(enc)
          break
        except UnicodeDecodeError:
          continue
      if not html:
        html = raw.decode("utf-8", errors="replace")

      soup = BeautifulSoup(html, "html.parser")
      results = []
      seen = set()

      # 1) HTML 내 <script> JSON 데이터 탐색 (__NEXT_DATA__ 등)
      for script in soup.select("script"):
        txt = script.string or script.get_text() or ""
        if "stockName" in txt or "itemCode" in txt:
          # JSON 객체 추출 시도
          m_json = re.search(r"(\{.*\})", txt.strip(), re.DOTALL)
          if m_json:
            try:
              parsed = json.loads(m_json.group(1))
              found_dicts = []
              walk_find_stock_dicts(parsed, found_dicts)
              for item in found_dicts:
                name = pick_first(item, ["stockName", "itemName", "name", "reutersName"])
                code = pick_first(item, ["itemCode", "stockCode", "code", "symbolCode"])
                itype = pick_first(item, ["itemType", "type", "marketType", "stockType"])
                price = pick_first(item, ["closePrice", "nowVal", "currentPrice", "price"])
                rate = pick_first(item, ["fluctuationsRatio", "changeRate", "rat", "prdyCtrt"])
                if code and code not in seen and not is_etf_or_etn(name, itype, code):
                  seen.add(code)
                  results.append((name, code, price, rate))
                  if len(results) >= 20:
                    return results
            except Exception:
              pass

      # 2) 테이블 행 / 종목 링크 기반 파싱
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
        if code in seen or is_etf_or_etn(name, "", code):
          continue

        cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
        rate_list = [c for c in cells if "%" in c or re.fullmatch(r"[+-]?\d+\.\d+%?", c)]
        num_list = [c for c in cells if re.fullmatch(r"[0-9,]{2,}", c)]
        rate = rate_list[0] if rate_list else ""
        price = num_list[1] if len(num_list) >= 2 else (num_list[0] if num_list else "")

        seen.add(code)
        results.append((name, code, price, rate))
        if len(results) >= 20:
          return results

      if results:
        return results
    except Exception:
      continue

  return []


def get_popular_top20_message() -> str:
  items = fetch_from_json_endpoints()
  if not items:
    items = fetch_from_npay_top_page()

  if not items:
    raise RuntimeError(
        "실시간 인기검색어 순위 데이터를 추출하지 못했습니다. "
        "(Npay 증권 페이지가 클라이언트 렌더링 전용으로 변경되었을 수 있습니다.)"
    )

  lines = ["📊 *한국장 인기검색어 순위 TOP 20 (ETF·ETN 제외)*\n"]
  for idx, (name, code, price, rate) in enumerate(items[:20], 1):
    price_part = f"{price}원" if price and not str(price).endswith("원") else (str(price) if price else "-")
    rate_part = f" ({rate})" if rate else ""
    lines.append(f"{idx}. *{name}* ({code}) : {price_part}{rate_part}")

  return "\n".join(lines)


def send_telegram(text: str):
  if not TELEGRAM_TOKEN or not CHAT_ID:
    raise ValueError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 환경변수가 비어 있습니다.")

  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
  resp = requests.post(url, json=payload, timeout=15)
  resp.raise_for_status()


if __name__ == "__main__":
  msg = get_popular_top20_message()
  send_telegram(msg)
  print("전송 완료")
