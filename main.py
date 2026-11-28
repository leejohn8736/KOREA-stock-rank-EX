# ------------------------------------------------------------------ 텔레그램
def send_telegram(text: str, use_html: bool = False):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise ValueError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 환경변수가 비어 있습니다.")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}
    if use_html:
        payload["parse_mode"] = "HTML"
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()


def send_sections(sections: list[str]):
    full = "\n\n\n".join(sections)
    if len(full) <= 4000:
        send_telegram(full)
        return
    for s in sections:
        send_telegram(s)


# ------------------------------------------------------------ 상태 저장/불러오기
def dedupe(picked: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in picked:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        out.append({"name": r["name"], "code": r["code"], "rate": r.get("rate", 0)})
    return out


def save_state(picked: list[dict]):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(dedupe(picked), f, ensure_ascii=False, indent=1)


def load_state() -> list[dict]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# ------------------------------------------------- 뉴스 / 공시 / 실적 수집
def _news_link(href: str) -> str:
    oid = re.search(r"office_id=(\d+)", href)
    aid = re.search(r"article_id=(\d+)", href)
    if oid and aid:
        return f"https://n.news.naver.com/mnews/article/{oid.group(1)}/{aid.group(1)}"
    if href.startswith("/"):
        return "https://finance.naver.com" + href
    return href


def _fetch_list(path: str, code: str, limit: int) -> list[dict]:
    res = requests.get(
        f"https://finance.naver.com/item/{path}",
        params={"code": code, "page": 1},
        headers={**HEADERS, "Referer": f"https://finance.naver.com/item/news.naver?code={code}"},
        timeout=15,
    )
    res.raise_for_status()
    soup = BeautifulSoup(res.content.decode("euc-kr", errors="ignore"), "html.parser")
    out, seen = [], set()
    for tr in soup.select("tr"):
        if "relation_lst" in (tr.get("class") or []):
            continue
        a = tr.select_one("td.title a")
        if a is None:
            continue
        title = a.get_text(strip=True)
        if not title or title in seen:
            continue
        seen.add(title)
        info = tr.select_one("td.info")
        date = tr.select_one("td.date")
        out.append({
            "title": title,
            "url": _news_link(a.get("href", "")),
            "src": info.get_text(strip=True) if info else "",
            "date": date.get_text(strip=True) if date else "",
        })
        if len(out) >= limit:
            break
    return out


def fetch_news(code: str) -> list[dict]:
    return _fetch_list("news_news.naver", code, NEWS_PER_STOCK)


def fetch_notices(code: str) -> list[dict]:
    return _fetch_list("news_notice.naver", code, NOTICE_PER_STOCK)


def fetch_earnings(code: str) -> str:
    """최근 분기 매출/영업이익 (억원). 응답 구조가 다르면 빈 문자열."""
    res = requests.get(
        f"https://m.stock.naver.com/api/stock/{code}/finance/quarter",
        headers=HEADERS, timeout=15,
    )
    res.raise_for_status()
    info = res.json().get("financeInfo", {})
    titles = [t for t in info.get("trTitleList", []) if t.get("isConsensus") != "Y"]
    if not titles:
        return ""
    rows = {r.get("title"): r.get("columns", {}) for r in info.get("rowList", [])}

    def val(row_name, key):
        return (rows.get(row_name, {}).get(key, {}) or {}).get("value", "-")

    last = titles[-1]
    return (
        f"{last.get('title', '')} 매출 {val('매출액', last['key'])}억 · "
        f"영업이익 {val('영업이익', last['key'])}억"
    )


def theme_tags(texts: list[str]) -> list[str]:
    blob = " ".join(texts)
    return [tag for tag, kws in THEME_KEYWORDS.items() if any(k in blob for k in kws)]


def a_tag(item: dict) -> str:
    t = item["title"]
    t = t if len(t) <= 55 else t[:54] + "…"
    meta = " · ".join(x for x in (item["src"], item["date"]) if x)
    return f'• <a href="{html.escape(item["url"], quote=True)}">{html.escape(t)}</a>' + (
        f" <i>({html.escape(meta)})</i>" if meta else ""
    )


def build_stock_block(rank: int, s: dict) -> str:
    code, name = s["code"], s["name"]
    news, notices, earn = [], [], ""
    try:
        news = fetch_news(code)
    except Exception as e:
        print(f"[안내] 뉴스 실패 {name}: {e}")
    try:
        notices = fetch_notices(code)
    except Exception as e:
        print(f"[안내] 공시 실패 {name}: {e}")
    try:
        earn = fetch_earnings(code)
    except Exception as e:
        print(f"[안내] 실적 실패 {name}: {e}")

    lines = [f"<b>{rank}. {html.escape(name)}</b> ({code})"]
    tags = theme_tags([n["title"] for n in news + notices])
    if tags:
        lines.append("🏷 " + " / ".join(tags))
    if earn:
        lines.append(f"📈 {html.escape(earn)}")
    if news:
        lines.append("📰 뉴스")
        lines += [a_tag(n) for n in news]
    if notices:
        lines.append("📑 공시")
        lines += [a_tag(n) for n in notices]
    if not (news or notices or earn):
        lines.append("(수집된 재료 없음)")
    lines.append(f'🔗 <a href="https://finance.naver.com/item/main.naver?code={code}">종목 페이지</a>')
    return "\n".join(lines)


def run_news():
    targets = load_state()
    note = "직전 순위 종목 기준"
    if not targets:
        _, picked = build_sections()
        targets = dedupe(picked)
        note = "저장된 순위가 없어 현재 순위 기준"
    targets = targets[:NEWS_MAX_STOCKS]

    kst = datetime.utcnow() + timedelta(hours=9)
    header = f"🗞️ <b>종목 재료 리포트</b>\n🕗 {kst:%Y-%m-%d %H:%M} KST · {note}"

    chunks, cur = [], header
    for i, s in enumerate(targets, 1):
        block = build_stock_block(i, s)
        if len(cur) + len(block) + 2 > 3800:
            chunks.append(cur)
            cur = block
        else:
            cur += "\n\n" + block
        time.sleep(0.3)
    chunks.append(cur)
    for c in chunks:
        send_telegram(c, use_html=True)


def run_rank():
    secs, picked = build_sections()
    send_sections(secs)
    save_state(picked)


def pick_mode() -> str:
    m = os.environ.get("RUN_MODE", "auto").strip().lower()
    if m in ("rank", "news"):
        return m
    kst = datetime.utcnow() + timedelta(hours=9)
    return "rank" if kst.hour % 2 == 1 else "news"   # 홀수 시=순위, 짝수 시=재료


if __name__ == "__main__":
    mode = pick_mode()
    print(f"모드: {mode}")
    try:
        run_rank() if mode == "rank" else run_news()
    except Exception as e:
        msg = f"⚠️ {mode} 실패\n{type(e).__name__}: {e}"
        print(msg)
        send_telegram(msg)
        raise SystemExit(1)
    print("전송 완료")
