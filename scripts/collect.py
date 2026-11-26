"""
Market-Brief-Korea data collector.
Usage:
  python scripts/collect.py morning   # US close brief (run ~06:30 KST)
  python scripts/collect.py close     # KR close brief (run ~16:10 KST)
  python scripts/collect.py morning --sample   # offline sample data
Output: data/morning.json or data/close.json
Optional secret: GEMINI_API_KEY (free tier) -> Korean headline summary + opinion draft.
"""
import json, os, re, sys, datetime as dt
from email.utils import parsedate_to_datetime

KST = dt.timezone(dt.timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------- tickers ----------------
US_INDEX = [("나스닥", "^IXIC"), ("S&P500", "^GSPC"), ("필라델피아 반도체", "^SOX")]
US_MACRO = [("달러 인덱스", "DX-Y.NYB"), ("금", "GC=F"), ("WTI", "CL=F")]
US_SECTOR = [("기술", "XLK"), ("커뮤니케이션", "XLC"), ("경기소비재", "XLY"), ("필수소비재", "XLP"),
             ("에너지", "XLE"), ("금융", "XLF"), ("헬스케어", "XLV"), ("산업재", "XLI"),
             ("소재", "XLB"), ("부동산", "XLRE"), ("유틸리티", "XLU"), ("반도체(SMH)", "SMH")]
ASIA_INDEX = [("코스피", "^KS11"), ("코스닥", "^KQ11"), ("닛케이225", "^N225"),
              ("대만 가권", "^TWII"), ("상해종합", "000001.SS")]
FX = [("원/달러", "KRW=X"), ("원/100엔", "JPYKRW=X")]
FIXED_STOCK = [("삼성전자", "005930.KS"), ("SK하이닉스", "000660.KS")]
# KR sector proxies = sector ETFs (codes to be confirmed on first run; failed ones are skipped)
KR_SECTOR = [("반도체", "091160.KS"), ("은행", "091170.KS"), ("자동차", "091180.KS"),
             ("2차전지", "305720.KS"), ("헬스케어", "266420.KS"), ("철강", "117680.KS"),
             ("건설", "117700.KS"), ("증권", "102970.KS"), ("운송", "140710.KS")]
NQ_FUT = ("나스닥100 선물", "NQ=F")

# US sector -> KR read-through (used for news price-check and opinion draft)
US_TO_KR = {
    "반도체(SMH)": "반도체(삼성전자·SK하이닉스)", "기술": "IT·소프트웨어", "에너지": "정유·에너지",
    "금융": "은행·증권", "헬스케어": "바이오·헬스케어", "산업재": "조선·기계·방산",
    "유틸리티": "전력기기·유틸리티", "경기소비재": "자동차·유통", "소재": "화학·철강",
    "커뮤니케이션": "인터넷·게임", "필수소비재": "음식료", "부동산": "리츠·건설",
}

# ---------------- news sources ----------------
FEEDS = {
    "morning": [
        ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("CNBC 투자", "https://www.cnbc.com/id/15839069/device/rss/rss.html"),
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
        ("인베스팅닷컴", "https://kr.investing.com/rss/news_285.rss"),
    ],
    "close": [
        ("한국경제", "https://www.hankyung.com/feed/finance"),
        ("아시아경제", "https://view.asiae.co.kr/rss/stock.htm"),
        ("인베스팅닷컴", "https://kr.investing.com/rss/news_14.rss"),
        ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
    ],
}
# result-type headlines (market moved -> article) are excluded from top-3
RESULT_PAT = re.compile(
    r"(마감|장 ?중|출발|하락세|상승세|급등 ?마감|급락 ?마감|코스피[ ,]*\d|코스닥[ ,]*\d|"
    r"stocks? (close|end|finish|rise|fall|slip|rally)|dow (rises|falls|jumps|drops)|"
    r"s&p 500 (rises|falls|closes|ends)|stock market today|futures (rise|fall|slip))", re.I)

# keyword -> related sector (for price-reaction check)
TOPIC_SECTOR = [
    (r"nvidia|엔비디아|chip|semiconductor|반도체|hbm|tsmc|micron|하이닉스|삼성전자", "반도체"),
    (r"oil|crude|opec|유가|원유|정유", "에너지"),
    (r"fed|fomc|rate|금리|powell|파월|treasury|국채|cpi|inflation|물가|고용|jobs|payroll", "금리"),
    (r"bank|은행|금융|jpmorgan|증권", "금융"),
    (r"tesla|테슬라|ev |전기차|battery|배터리|2차전지|자동차|auto", "자동차·2차전지"),
    (r"drug|pharma|biotech|바이오|제약|헬스케어|fda", "헬스케어"),
    (r"power|utility|전력|원전|nuclear|grid", "전력·유틸리티"),
    (r"tariff|관세|trade|무역|china|중국", "무역·관세"),
    (r"ai |artificial intelligence|인공지능|openai|data center|데이터센터", "AI"),
    (r"defense|방산|조선|shipbuild", "방산·조선"),
]
SECTOR_TICKERS = {"반도체": ["SMH", "091160.KS"], "에너지": ["XLE"], "금융": ["XLF", "091170.KS"],
                  "자동차·2차전지": ["XLY", "091180.KS", "305720.KS"], "헬스케어": ["XLV", "266420.KS"],
                  "전력·유틸리티": ["XLU"], "AI": ["XLK", "SMH"], "방산·조선": ["XLI"]}


def pct(a, b):
    return None if not a or not b else round((a / b - 1) * 100, 2)


def quotes(pairs):
    """[(name,ticker)] -> list of dict(name,ticker,price,chg,date). Uses last 2 daily closes."""
    import yfinance as yf
    out = []
    tickers = [t for _, t in pairs]
    try:
        df = yf.download(tickers, period="7d", interval="1d", progress=False,
                         auto_adjust=False, group_by="ticker", threads=True)
    except Exception as e:
        print("download fail", e); df = None
    for name, t in pairs:
        try:
            s = df[t]["Close"].dropna() if len(tickers) > 1 else df["Close"].dropna()
            if len(s) < 2:
                raise ValueError("short")
            out.append(dict(name=name, ticker=t, price=round(float(s.iloc[-1]), 2),
                            chg=pct(float(s.iloc[-1]), float(s.iloc[-2])),
                            date=str(s.index[-1].date())))
        except Exception as e:
            print("skip", t, e)
    return out


def live_quote(name, t):
    """intraday last vs previous close (for Nasdaq night futures at send time)."""
    import yfinance as yf
    try:
        h = yf.Ticker(t).history(period="5d", interval="15m")["Close"].dropna()
        d = yf.Ticker(t).history(period="5d", interval="1d")["Close"].dropna()
        last, prev = float(h.iloc[-1]), float(d.iloc[-2])
        return dict(name=name, ticker=t, price=round(last, 2), chg=pct(last, prev),
                    time=h.index[-1].tz_convert(KST).strftime("%m/%d %H:%M"))
    except Exception as e:
        print("skip", t, e); return None


def fetch_news(kind):
    import feedparser
    items, now = [], dt.datetime.now(dt.timezone.utc)
    for src, url in FEEDS[kind]:
        try:
            f = feedparser.parse(url, agent="Mozilla/5.0 Market-Brief-Korea")
            for e in f.entries[:40]:
                try:
                    pub = parsedate_to_datetime(e.get("published", ""))
                except Exception:
                    pub = now
                if (now - pub).total_seconds() > 30 * 3600:
                    continue
                items.append(dict(source=src, title=e.title.strip(), link=e.link,
                                  summary=re.sub("<[^>]+>", "", e.get("summary", ""))[:300]))
        except Exception as ex:
            print("feed fail", src, ex)
    return items


def tokens(text):
    w = re.findall(r"[A-Za-z]{4,}|[가-힣]{2,}", text.lower())
    stop = {"that", "with", "from", "this", "will", "after", "says", "said", "stock", "stocks",
            "market", "markets", "shares", "news", "today", "investors", "주식", "증시", "시장", "투자"}
    return {x for x in w if x not in stop}


def topic_of(text):
    for pat, sec in TOPIC_SECTOR:
        if re.search(pat, text, re.I):
            return sec
    return None


def pick_top3(items, price_map):
    """cause-type news only; score = distinct sources sharing keywords + price reaction."""
    cand = [n for n in items if not RESULT_PAT.search(n["title"])]
    excluded = len(items) - len(cand)
    for n in cand:
        n["tok"] = tokens(n["title"] + " " + n["summary"])
        n["topic"] = topic_of(n["title"] + " " + n["summary"])
    for n in cand:
        srcs = {m["source"] for m in cand
                if m is not n and (len(n["tok"] & m["tok"]) >= 3 or
                                   (n["topic"] and n["topic"] == m["topic"]))}
        srcs.add(n["source"])
        n["sources"] = sorted(srcs)
        moves = [price_map[t] for t in SECTOR_TICKERS.get(n["topic"], []) if t in price_map]
        n["move"] = max(moves, key=abs) if moves else None
        n["score"] = len(srcs) * 2 + (min(abs(n["move"]), 4) if n["move"] is not None else 0)
        n["verified"] = len(srcs) >= 2 and (n["move"] is None or abs(n["move"]) >= 0.5)
    cand.sort(key=lambda x: x["score"], reverse=True)
    top, seen = [], set()
    for n in cand:  # one article per topic
        key = n["topic"] or n["title"][:25]
        if key in seen:
            continue
        seen.add(key); top.append(n)
        if len(top) == 6:
            break
    clean = [{k: v for k, v in n.items() if k not in ("tok", "summary")} | {"desc": n["summary"][:160]}
             for n in top]
    return clean[:3], clean[3:], excluded


def gemini(prompt):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    import requests
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=60)
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print("gemini fail", e); return None


def ko_headlines(news):
    """English headline -> one-line Korean (Gemini). Falls back to original."""
    eng = [n for n in news if re.search(r"[A-Za-z]{5,}", n["title"]) and not re.search(r"[가-힣]", n["title"])]
    if not eng:
        return
    lines = "\n".join(f"{i+1}. {n['title']} :: {n['desc']}" for i, n in enumerate(eng))
    res = gemini("다음 미국 증시 뉴스 제목을 각각 한국어 한 줄(40자 이내) 요약으로 번역해. "
                 "번호. 요약 형식으로만 답해.\n" + lines)
    if not res:
        return
    for line in res.splitlines():
        m = re.match(r"\s*(\d+)\.\s*(.+)", line)
        if m and int(m.group(1)) <= len(eng):
            eng[int(m.group(1)) - 1]["ko"] = m.group(2).strip()


def fmt(v):
    return "-" if v is None else f"{v:+.2f}%"


def opinion_morning(idx, sectors):
    """rule-based draft in PB voice (Gemini refines if key exists)."""
    up = [s for s in sectors if s["chg"] is not None and s["chg"] > 0][:3]
    dn = [s for s in sorted(sectors, key=lambda x: x["chg"] or 0)[:3] if (s["chg"] or 0) < 0]
    nas = next((x for x in idx if x["name"] == "나스닥"), None)
    sox = next((x for x in idx if "반도체" in x["name"]), None)
    tone = "강세" if nas and nas["chg"] > 0.3 else "약세" if nas and nas["chg"] < -0.3 else "보합권"
    s = [f"간밤 미국은 나스닥 {fmt(nas and nas['chg'])}로 {tone} 흐름이었습니다."]
    if sox:
        s.append(f"반도체 지수가 {fmt(sox['chg'])} 움직인 만큼 오늘 삼성전자·SK하이닉스 시초가에 "
                 f"{'우호적' if sox['chg'] > 0 else '부담'}으로 작용할 가능성이 있습니다.")
    if up:
        s.append("미국 강세 업종(" + ", ".join(x["name"] for x in up) + ")과 연결되는 국내 "
                 + ", ".join(US_TO_KR.get(x["name"], x["name"]) for x in up) + " 쪽 관심이 유효해 보입니다.")
    if dn:
        s.append("반면 " + ", ".join(US_TO_KR.get(x["name"], x["name"]) for x in dn)
                 + " 업종은 보수적으로 접근하는 게 좋겠습니다.")
    draft = " ".join(s)
    better = gemini("너는 한국 증권사 PB다. 아래 데이터와 초안을 바탕으로 고객 카톡용 '오늘의 관전 포인트'를 "
                    "3~4문장, 존댓말, 과장·확정 표현 없이 다시 써. 개별 종목은 대표주만 언급.\n"
                    f"데이터: {json.dumps(idx + sectors, ensure_ascii=False)}\n초안: {draft}")
    return better or draft


def opinion_close(asia, stocks, ksec, nq):
    k = next((x for x in asia if x["name"] == "코스피"), None)
    q = next((x for x in asia if x["name"] == "코스닥"), None)
    s = [f"오늘 코스피 {fmt(k and k['chg'])}, 코스닥 {fmt(q and q['chg'])}로 마감했습니다."]
    if stocks:
        s.append(" / ".join(f"{x['name']} {fmt(x['chg'])}" for x in stocks) + ".")
    good = [x for x in ksec if (x["chg"] or 0) > 0][:2]
    bad = [x for x in sorted(ksec, key=lambda x: x["chg"] or 0)[:2] if (x["chg"] or 0) < 0]
    if good:
        s.append(", ".join(x["name"] for x in good) + " 업종이 지수를 받쳤고")
    if bad:
        s.append(", ".join(x["name"] for x in bad) + " 업종은 약했습니다.")
    if nq:
        s.append(f"현재 나스닥 선물은 {fmt(nq['chg'])}로 오늘 밤 미국장 분위기를 가늠해 볼 수 있습니다.")
    draft = " ".join(s).replace("받쳤고 ", "받쳤고, ")
    better = gemini("너는 한국 증권사 PB다. 아래 데이터와 초안으로 고객 카톡용 '오늘의 시장 한 줄 평'을 "
                    "2~3문장, 존댓말, 하루 움직임 정리 위주로 다시 써. 미국장 예측은 하지 마.\n"
                    f"데이터: {json.dumps(asia + stocks + ksec, ensure_ascii=False)}\n초안: {draft}")
    return better or draft


def flow_close(news):
    """market-flow explanation from result-type articles (reference only)."""
    res = gemini("아래 오늘 국내 증시 기사 제목들로 오전·오후 장 흐름과 원인을 2~3문장으로 요약해. "
                 "기사에 없는 내용은 쓰지 마.\n" + "\n".join(n["title"] for n in news[:25]))
    return res


def build(kind, sample=False):
    now = dt.datetime.now(KST)
    if sample:
        return sample_data(kind, now)
    if kind == "morning":
        idx, macro, sec = quotes(US_INDEX), quotes(US_MACRO), quotes(US_SECTOR)
        sec.sort(key=lambda x: x["chg"] or 0, reverse=True)
        pm = {x["ticker"]: x["chg"] for x in idx + sec}
        news = fetch_news("morning")
        top, alt, excl = pick_top3(news, pm)
        ko_headlines(top + alt)
        return dict(kind=kind, generated=now.isoformat(timespec="minutes"), indices=idx, macro=macro,
                    sectors=sec, news=top, alternates=alt, news_total=len(news), news_excluded=excl,
                    opinion=opinion_morning(idx, sec), ai=bool(os.environ.get("GEMINI_API_KEY")))
    asia, fx, stocks, ksec = quotes(ASIA_INDEX), quotes(FX), quotes(FIXED_STOCK), quotes(KR_SECTOR)
    for x in fx:  # JPYKRW=X is per 1 yen -> show per 100 yen
        if x["ticker"] == "JPYKRW=X":
            x["price"] = round(x["price"] * 100, 2)
    ksec.sort(key=lambda x: x["chg"] or 0, reverse=True)
    nq = live_quote(*NQ_FUT)
    pm = {x["ticker"]: x["chg"] for x in stocks + ksec}
    news = fetch_news("close")
    top, alt, excl = pick_top3(news, pm)
    return dict(kind=kind, generated=now.isoformat(timespec="minutes"), indices=asia, fx=fx,
                stocks=stocks, sectors=ksec, nq=nq, flow=flow_close(news), news=top, alternates=alt,
                news_total=len(news), news_excluded=excl,
                opinion=opinion_close(asia, stocks, ksec, nq), ai=bool(os.environ.get("GEMINI_API_KEY")))


def sample_data(kind, now):
    """layout-test data only. Every number here is fake."""
    q = lambda n, p, c: dict(name=n, ticker="", price=p, chg=c, date="2026-11-24")
    nw = lambda t, s, src, mv, v: dict(title=t, ko=None, link="https://example.com", source=src,
                                       sources=s, topic=None, move=mv, score=0, verified=v, desc="")
    if kind == "morning":
        idx = [q("나스닥", 20000.0, 1.0), q("S&P500", 6000.0, 0.5), q("필라델피아 반도체", 5000.0, 2.0)]
        sec = [q(n, 0, c) for n, c in [("반도체(SMH)", 2.0), ("기술", 1.2), ("유틸리티", 0.8),
                                       ("산업재", 0.3), ("금융", -0.2), ("헬스케어", -0.6), ("에너지", -1.4)]]
        return dict(kind=kind, sample=True, generated=now.isoformat(timespec="minutes"), indices=idx,
                    macro=[q("달러 인덱스", 100.0, -0.2), q("금", 2500.0, 0.4), q("WTI", 70.0, -1.5)],
                    sectors=sec,
                    news=[nw("[샘플] 반도체 관련 원인형 뉴스 제목", ["CNBC", "MarketWatch", "Yahoo Finance"], "CNBC", 2.0, True),
                          nw("[샘플] 금리·지표 관련 뉴스 제목", ["CNBC", "MarketWatch"], "MarketWatch", None, True),
                          nw("[샘플] 단독 보도 뉴스 제목", ["Yahoo Finance"], "Yahoo Finance", -0.1, False)],
                    alternates=[nw("[샘플] 교체 후보 뉴스", ["CNBC"], "CNBC", None, False)],
                    news_total=0, news_excluded=0, opinion=opinion_morning(idx, sec), ai=False)
    asia = [q("코스피", 2500.0, 0.5), q("코스닥", 700.0, -0.3), q("닛케이225", 38000.0, 0.2),
            q("대만 가권", 22000.0, 0.9), q("상해종합", 3300.0, -0.1)]
    st = [q("삼성전자", 60000, 1.0), q("SK하이닉스", 200000, 2.0)]
    ks = [q(n, 0, c) for n, c in [("반도체", 1.5), ("증권", 0.9), ("은행", 0.2), ("자동차", -0.4), ("2차전지", -1.8)]]
    nq = dict(name="나스닥100 선물", price=21000.0, chg=0.3, time=now.strftime("%m/%d %H:%M"))
    return dict(kind=kind, sample=True, generated=now.isoformat(timespec="minutes"), indices=asia,
                fx=[q("원/달러", 1400.0, 0.2), q("원/100엔", 900.0, -0.1)], stocks=st, sectors=ks, nq=nq,
                flow="[샘플] 오전에는 반도체 중심 매수세로 상승 출발했고, 오후에는 2차전지 약세로 상승폭을 일부 반납했습니다.",
                news=[nw("[샘플] 국내 원인형 뉴스 1", ["한국경제", "아시아경제"], "한국경제", 1.5, True),
                      nw("[샘플] 국내 원인형 뉴스 2", ["한국경제", "연합뉴스"], "연합뉴스", None, True),
                      nw("[샘플] 국내 원인형 뉴스 3", ["아시아경제"], "아시아경제", -1.8, False)],
                alternates=[], news_total=0, news_excluded=0,
                opinion=opinion_close(asia, st, ks, nq), ai=False)


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "morning"
    data = build(kind, sample="--sample" in sys.argv)
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", f"{kind}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("saved", kind, len(data.get("news", [])), "news")
