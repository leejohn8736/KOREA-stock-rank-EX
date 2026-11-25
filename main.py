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
    "Referer": "https://finance.naver.com/sise/",
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


def is_etf_or_etn(name: str) -> bool:
  n = (name or "").strip()
  n_upper = n.upper()
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
  for enc in ("euc-kr", "cp949", "utf-8"):
    try:
      text = raw.decode(enc)
      if "인기검색" in text or "종목명" in text or "code=" in text:
        return text
    except UnicodeDecodeError:
      continue
  return raw.decode("utf-8", errors="replace")


def get_popular_top20_message() -> str:
  url = "https://finance.naver.com/sise/lastsearch2.naver"
  res = requests.get(url, headers=HEADERS, timeout=15)
  res.raise_for_status()

  html = decode_response(res)
  soup = BeautifulSoup(html, "html.parser")

  items = []
  seen_codes = set()

  # 인기검색어 표의 모든 행을 순회하며 종목 링크(code=6자리) 기준으로 추출
  for tr in soup.select("table tr"):
    link = tr.select_one('a[href*="code="]')
    if not link:
      continue

    name = link.get_text(" ", strip=True)
    href = link.get("href", "")
    m = re.search(r"code=([0-9A-Za-z]{6})", href)
    if not m or not name:
      continue

    code = m.group(1)
    if code in seen_codes:
      continue
    if is_etf_or_etn(name):
      continue

    cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
    # 네이버 인기검색 표 컬럼: [순위, 종목명, 검색비율, 현재가, 전일비, 등락률, ...]
    price = cells[3] if len(cells) > 3 else ""
    rate = cells[5] if len(cells) > 5 else ""

    seen_codes.add(code)
    items.append((name, code, price, rate))

    if len(items) >= 20:
      break

  if not items:
    title = soup.title.get_text(strip=True) if soup.title else "제목없음"
    raise RuntimeError(
        f"인기검색어 순위 추출 실패 (응답 길이: {len(html)}, title: {title})"
    )

  lines = ["📊 한국장 인기검색어 순위 TOP 20 (ETF·ETN 제외)\n"]
  for rank, (name, code, price, rate) in enumerate(items, 1):
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
