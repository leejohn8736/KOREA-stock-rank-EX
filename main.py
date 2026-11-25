import os
import re
from bs4 import BeautifulSoup
import requests

# TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 우선 사용 (없으면 기존 이름도 보조 확인)
TELEGRAM_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or ""
).strip()
CHAT_ID = (
    os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID") or ""
).strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def decode_html(res: requests.Response) -> str:
  raw = res.content
  for enc in ("euc-kr", "cp949", "utf-8"):
    try:
      text = raw.decode(enc)
      if "인기검색" in text or "종목명" in text or "item/main.naver" in text:
        return text
    except UnicodeDecodeError:
      continue
  return raw.decode("utf-8", errors="replace")


def fetch_from_naver_finance_popular():
  url = "https://finance.naver.com/sise/lastsearch2.naver"
  res = requests.get(url, headers=HEADERS, timeout=15)
  res.raise_for_status()

  html = decode_html(res)
  soup = BeautifulSoup(html, "html.parser")

  items = []
  seen_codes = set()

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
    seen_codes.add(code)

    cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
    price = ""
    rate = ""

    rate_match = [c for c in cells if "%" in c or re.fullmatch(r"[+-]?\d+\.\d+%?", c)]
    num_match = [c for c in cells if re.fullmatch(r"[0-9,]{2,}", c)]

    if rate_match:
      rate = rate_match[0]
    if len(num_match) >= 2:
      price = num_match[1]
    elif len(num_match) == 1:
      price = num_match[0]

    items.append((name, code, price, rate))
    if len(items) >= 15:
      break

  if not items:
    title = soup.title.get_text(strip=True) if soup.title else "제목없음"
    sample = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:250]
    raise RuntimeError(
        f"인기검색종목 파싱 실패 (응답 길이: {len(html)}, title: {title}, "
        f"본문 일부: {sample})"
    )

  lines = ["📊 *한국장 인기검색 랭킹 TOP 15*\n"]
  for idx, (name, code, price, rate) in enumerate(items, 1):
    price_str = f"{price}원" if price and not str(price).endswith("원") else str(price)
    rate_str = f" ({rate})" if rate else ""
    lines.append(f"{idx}. *{name}* ({code}) : {price_str}{rate_str}")

  return "\n".join(lines)


def send_telegram(text):
  if not TELEGRAM_TOKEN or not CHAT_ID:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 환경변수가 비어 있습니다."
    )

  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
  resp = requests.post(url, json=payload, timeout=15)
  resp.raise_for_status()


if __name__ == "__main__":
  msg = fetch_from_naver_finance_popular()
  send_telegram(msg)
  print("전송 완료")
