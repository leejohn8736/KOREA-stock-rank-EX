import os
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

# 1단계 개발자 도구(Network -> Fetch/XHR)에서 확인한 실제 API URL로 교체하세요.
API_URL = "https://stock.naver.com/api/..."


def pick_first(d: dict, keys: list[str], default=""):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def extract_items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("stocks", "items", "list", "result", "data", "stockList"):
            val = data.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                for inner_key in ("stocks", "items", "list", "stockList"):
                    if isinstance(val.get(inner_key), list):
                        return val[inner_key]
    return []


def get_naver_top_stocks():
    if "api/..." in API_URL:
        raise ValueError("API_URL을 실제 네트워크 탭에서 확인한 엔드포인트로 변경해 주세요.")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://stock.naver.com/market/stock/kr/stocklist/top",
        "Accept": "application/json, text/plain, */*",
    }

    res = requests.get(API_URL, headers=headers, timeout=15)
    if res.status_code != 200:
        raise RuntimeError(f"HTTP {res.status_code} 응답: {res.text[:300]}")

    data = res.json()
    stocks = extract_items(data)
    if not stocks:
        raise ValueError(f"종목 리스트를 찾지 못했습니다. 응답 일부: {str(data)[:300]}")

    lines = ["📊 *한국장 랭킹 순위*\n"]
    for idx, item in enumerate(stocks[:15], 1):
        name = pick_first(item, ["stockName", "name", "itemName", "reutersName"])
        code = pick_first(item, ["itemCode", "code", "stockCode", "symbolCode"])
        price = pick_first(item, ["closePrice", "nowVal", "currentPrice", "price"])
        rate = pick_first(item, ["fluctuationsRatio", "changeRate", "rat", "prdyCtrt"])
        lines.append(f"{idx}. *{name}* ({code}) : {price}원 ({rate}%)")

    return "\n".join(lines)


def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise ValueError("TELEGRAM_TOKEN 또는 CHAT_ID 환경변수가 비어 있습니다.")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()


if __name__ == "__main__":
    msg = get_naver_top_stocks()
    send_telegram(msg)
    print("전송 완료")
