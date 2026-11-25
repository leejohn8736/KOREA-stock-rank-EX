import os
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()


def get_naver_top_stocks():
    url = "https://m.stock.naver.com/api/mystock/popular"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://m.stock.naver.com/",
    }
    params = {"page": 1, "pageSize": 20}

    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    lines = ["📊 *한국장 랭킹 순위*\n"]
    stocks = data.get("stocks", []) if isinstance(data, dict) else data

    for idx, item in enumerate(stocks[:15], 1):
        name = item.get("stockName", "")
        code = item.get("itemCode", "")
        price = item.get("closePrice", "")
        rate = item.get("fluctuationsRatio", "")
        lines.append(f"{idx}. *{name}* ({code}) : {price}원 ({rate}%)")

    return "\n".join(lines)


def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise ValueError("TELEGRAM_TOKEN 또는 CHAT_ID 환경변수가 비어 있습니다.")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


if __name__ == "__main__":
    try:
        msg = get_naver_top_stocks()
        send_telegram(msg)
        print("전송 완료")
    except Exception as e:
        print(f"오류 발생: {e}")
        raise
