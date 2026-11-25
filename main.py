import requests

# 텔레그램 설정
TELEGRAM_TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"


def get_naver_top_stocks():
    # 네이버 증권 인기 검색 종목 JSON API (KOSPI/KOSDAQ 통합 상위)
    url = "https://m.stock.naver.com/api/mystock/popular"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://m.stock.naver.com/",
    }
    params = {"page": 1, "pageSize": 20}  # 상위 20개

    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json()

    lines = ["📊 *네이버 증권 인기 검색 상위 종목*\n"]
    # API 응답 구조에 따라 키값(stocks 또는 result 등) 확인 필요
    stocks = data.get("stocks", []) if isinstance(data, dict) else data

    for idx, item in enumerate(stocks[:15], 1):
        name = item.get("stockName", "")
        code = item.get("itemCode", "")
        price = item.get("closePrice", "")
        rate = item.get("fluctuationsRatio", "")
        lines.append(f"{idx}. *{name}* ({code}) : {price}원 ({rate}%)")

    return "\n".join(lines)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload, timeout=10)


if __name__ == "__main__":
    try:
        msg = get_naver_top_stocks()
        send_telegram(msg)
        print("전송 완료")
    except Exception as e:
        print(f"오류 발생: {e}")
