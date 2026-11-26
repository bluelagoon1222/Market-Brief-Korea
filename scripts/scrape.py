"""
Scrape public Telegram channel preview (t.me/s/<channel>) -> data/posts.json
Keeps the latest MAX_KEEP posts, newest first. Run every ~10 min by GitHub Actions.
"""
import json, os, re, sys, datetime as dt
import requests
from bs4 import BeautifulSoup

CHANNEL = os.environ.get("TG_CHANNEL", "globalmktinsight")
MAX_KEEP = 300
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "posts.json")
KST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"}

CATS = [  # (category, pattern on first lines)
    ("미국", r"글로벌\s*마켓\s*브리핑|미국\s*증시|뉴욕|월가|월스트리트"),
    ("한국", r"한국|코스피|코스닥|국내\s*증시"),
    ("중국", r"중국|홍콩|상해|항셍|china"),
    ("일본·대만", r"일본|닛케이|대만|가권"),
]


def text_of(node):
    if node is None:
        return ""
    for br in node.find_all("br"):
        br.replace_with("\n")
    for a in node.find_all("a"):  # keep real URL when link text is shortened
        href = a.get("href", "")
        t = a.get_text()
        if href.startswith("http") and t and t not in href and ("…" in t or t.startswith("http")):
            a.replace_with(href)
    return re.sub(r"\n{3,}", "\n\n", node.get_text()).strip()


def category(text):
    head = "\n".join(text.splitlines()[:3])
    if "클로징" in head and re.search(r"한국|중국", head):
        return "한국·중국"
    for cat, pat in CATS:
        if re.search(pat, head, re.I):
            return cat
    if re.match(r"^\s*https?://", text) or len(text) < 120:
        return "뉴스링크"
    return "기타"


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for m in soup.select("div.tgme_widget_message[data-post]"):
        pid = int(m["data-post"].split("/")[-1])
        tm = m.select_one("a.tgme_widget_message_date time")
        when = dt.datetime.fromisoformat(tm["datetime"]).astimezone(KST) if tm else None
        body = text_of(m.select_one("div.tgme_widget_message_text"))
        docs = [dict(name=d.get_text(strip=True), url=None)
                for d in m.select(".tgme_widget_message_document_title")]
        links = [a["href"] for a in m.select("div.tgme_widget_message_text a[href^=http]")]
        photo = None
        ph = m.select_one("a.tgme_widget_message_photo_wrap")
        if ph and ph.get("style"):
            mm = re.search(r"url\('([^']+)'\)", ph["style"])
            photo = mm.group(1) if mm else None
        if not body and not docs and not photo:
            continue
        if "service_message" in m.get("class", []) or re.search(r"\bpinned (a|the|«)", body):
            continue  # pin / service notices
        title = next((l.strip() for l in body.splitlines() if l.strip()), docs[0]["name"] if docs else "(사진)")
        posts.append(dict(id=pid, url=f"https://t.me/{CHANNEL}/{pid}",
                          time=when.isoformat(timespec="minutes") if when else None,
                          title=title[:120], text=body, links=links[:10], docs=docs, photo=photo,
                          cat=category(body)))
    return posts


# ---------------- opinion draft ----------------
def pct(text, name):
    m = re.search(re.escape(name) + r"[^\n]{0,40}?([+-]\d+(?:\.\d+)?)%", text)
    return float(m.group(1)) if m else None


def f(v):
    return f"{v:+.2f}%"


US2KR = {"정보기술": "IT·반도체", "소재": "화학·철강", "에너지": "정유", "금융": "은행·증권",
         "헬스케어": "바이오", "산업재": "조선·기계·방산", "유틸리티": "전력기기", "경기소비재": "자동차·유통",
         "커뮤니케이션": "인터넷·게임", "부동산": "리츠·건설", "필수소비재": "음식료"}


def draft_us(t):
    nas, spx, sox = pct(t, "나스닥 종합"), pct(t, "S&P500:"), pct(t, "필라델피아 반도체")
    out = []
    if nas is not None:
        out.append(f"간밤 미국증시는 나스닥 {f(nas)}" + (f", S&P500 {f(spx)}" if spx is not None else "") + "로 마감했습니다.")
    if sox is not None:
        if sox >= 0.3:
            out.append(f"필라델피아 반도체 지수가 {f(sox)} 올라 오늘 국내 반도체 대형주는 우호적인 출발이 기대됩니다.")
        elif sox <= -0.3:
            out.append(f"필라델피아 반도체 지수가 {f(sox)} 내려 오늘 국내 반도체 대형주는 시초가 부담이 있을 수 있습니다.")
        else:
            out.append(f"필라델피아 반도체 지수는 {f(sox)}로 방향성이 크지 않아 수급을 확인하며 대응하시는 게 좋겠습니다.")
    secs = re.findall(r"([가-힣]+)\s*([+-]\d+\.\d+)%", (re.search(r"업종지수\n([^\n]+)", t) or [None, ""])[1])
    if secs:
        secs = sorted(((n, float(v)) for n, v in secs), key=lambda x: -x[1])
        up = [n for n, v in secs[:3] if v > 0]
        dn = [n for n, v in secs[::-1][:2] if v < 0]
        if up:
            out.append("업종별로는 " + "·".join(up) + " 업종이 강했고" + (", " + "·".join(dn) + " 업종은 약했습니다." if dn else "습니다."))
            out.append("국내에서도 " + ", ".join(US2KR.get(n, n) for n in up[:2]) + " 업종 흐름을 함께 보시면 좋겠습니다.")
    m = re.search(r"Key Takeaways #1\n([^\n]+)", t)
    if m:
        out.append(f"특히 '{m.group(1).strip()}' 이슈는 오늘 시장에서도 주목할 변수입니다.")
    return " ".join(out) or None


def themes(block):
    res = []
    for m in re.finditer(r"·\s*([^\n:]{1,20})\n-([^\n]*)\n-?([^\n]*)", block):
        name, line = m.group(1).strip(), m.group(2) + " " + m.group(3)
        v = re.search(r"\(([+-]\d+\.\d+)%\)", line)
        if v:
            res.append((name, float(v.group(1))))
    return res


def draft_kr(t, closing):
    k, q = pct(t, "코스피"), pct(t, "코스닥")
    sub = None
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    if closing and len(lines) > 1:
        sub = lines[1]
    m = re.search(r"Summary:\s*([^\n]+)", t)
    if m:
        sub = m.group(1).strip()
    if sub:
        sub = re.sub(r"[‘’'\"“”]|\.{2,}$", "", sub).strip()
    out = []
    if k is not None:
        lead = "오늘 국내 증시는" if closing else "오늘 오전 국내 증시는"
        out.append(f"{lead} " + (f"'{sub}' 흐름으로 " if sub else "") + f"코스피 {f(k)}" + (f", 코스닥 {f(q)}" if q is not None else "")
                   + ("로 마감했습니다." if closing else "를 기록하고 있습니다."))
    th = themes(t.split("# 중국")[0])
    up = [n for n, v in th if v > 0][:3]
    dn = [n for n, v in th if v < 0][:2]
    if dn and up:
        out.append("·".join(dn) + " 쪽은 약했고, " + "·".join(up) + " 쪽으로 매수세가 몰렸습니다.")
    elif up:
        out.append("테마로는 " + "·".join(up) + " 쪽이 강세입니다.")
    elif dn:
        out.append("·".join(dn) + " 쪽 약세가 두드러졌습니다.")
    sh = pct(t, "상해종합")
    if closing and sh is not None:
        out.append(f"중국 상해종합지수는 {f(sh)}였습니다.")
    if closing:
        out.append("내일 장도 오늘 수급 흐름이 이어지는지 확인하며 대응하시는 게 좋겠습니다.")
    return " ".join(out) or None


AI_DEADLINE = [None]


def gemini(prompt):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    import time
    if AI_DEADLINE[0] is None:
        AI_DEADLINE[0] = time.time() + 240  # max 4 min of AI work per run
    if time.time() > AI_DEADLINE[0]:
        return None
    models = [os.environ.get("GEMINI_MODEL")] if os.environ.get("GEMINI_MODEL") else \
        ["gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash"]
    for model in models:
        try:
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                              json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=45)
            j = r.json()
            if "candidates" in j:
                return j["candidates"][0]["content"]["parts"][0]["text"].strip()
            print("gemini", model, r.status_code, str(j)[:200])
            if r.status_code != 404:
                return None  # quota/key error: don't try other models
        except Exception as e:
            print("gemini fail", model, e)
            return None
    return None


PROMPT_US = """너는 한국 증권사 PB다. 아래는 미래에셋증권 리서치의 미국 증시 브리핑 원문이다.
이를 분석해 고객 카톡용 '오늘의 관전포인트'를 4~5문장으로 써라.
순서: (1) 간밤 미국 시장을 움직인 핵심 원인 1~2개와 그 의미 (2) 그 원인이 오늘 한국 증시에 미칠 영향
(3) 주목할 국내 업종·대표주와 조심할 업종 (4) 오늘 확인할 변수.
규칙: 존댓말, 원문에 없는 숫자·사실은 쓰지 말 것, 숫자를 쓰면 원문 그대로, 과장·단정·매수 권유 표현 금지,
제목·머리표·이모지 없이 문장만 출력."""

PROMPT_KR = """너는 한국 증권사 PB다. 아래는 미래에셋증권 리서치의 시황 원문이다(장중 또는 장 마감 후 글일 수 있다).
이를 분석해 고객 카톡용 '투자 체크포인트'를 3~4문장으로 써라.
순서: (1) 시장이 움직인 핵심 원인과 그 의미 (2) 강했던·약했던 업종이 주는 시사점 (3) 앞으로 확인할 변수나 대응 관점.
규칙: 존댓말, 원문에 없는 숫자·사실은 쓰지 말 것, 숫자를 쓰면 원문 그대로, '어제·마감' 같은 시점 단정은 원문에 있을 때만,
과장·단정·매수 권유 표현 금지, 제목·머리표·이모지 없이 문장만 출력."""


def make_draft(p, use_ai=True):
    """returns (draft, source) ; source = 'ai' | 'rule' | None"""
    t, title = p["text"], p["title"]
    if "글로벌 마켓 브리핑" in title:
        prompt, rule = PROMPT_US, draft_us(t)
    elif "마켓 클로징" in title:
        prompt, rule = PROMPT_KR, draft_kr(t, True)
    elif "마켓 뷰" in title:
        prompt, rule = PROMPT_KR, draft_kr(t, False)
    elif p.get("cat") in ("한국", "중국", "한국·중국", "일본·대만", "미국") and len(t) >= 300:
        prompt, rule = PROMPT_KR, None
    else:
        return None, None
    ai = gemini(prompt + "\n\n[원문]\n" + t[:7000]) if use_ai else None
    if ai:
        ai = re.sub(r"^[#*\-\s]+", "", ai).strip()
        return ai, "ai"
    return (rule, "rule") if rule else (None, None)


def fetch(before=None):
    url = f"https://t.me/s/{CHANNEL}" + (f"?before={before}" if before else "")
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return parse(r.text)


def main():
    old = []
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8")).get("posts", [])
        except Exception:
            old = []
    pages = 1 if old else 4  # first run: backfill a few pages
    got, before = [], None
    for _ in range(pages):
        batch = fetch(before)
        if not batch:
            break
        got += batch
        before = min(p["id"] for p in batch)
    byid = {p["id"]: p for p in old}
    new = len({p["id"] for p in got} - set(byid))
    for p in got:
        byid[p["id"]] = p  # newer scrape wins (edited posts)
    posts = sorted(byid.values(), key=lambda p: p["id"], reverse=True)[:MAX_KEEP]
    prev = {p["id"]: p for p in old}
    has_key = bool(os.environ.get("GEMINI_API_KEY"))
    budget = 6  # AI calls per run
    for p in posts:
        o = prev.get(p["id"], {})
        if o.get("draft_src") == "ai" or (o.get("draft_src") and not has_key):
            p["draft"], p["draft_src"] = o["draft"], o["draft_src"]
            continue
        use_ai = has_key and budget > 0
        d, src = make_draft(p, use_ai)
        if use_ai and src in ("ai", "rule"):
            budget -= 1
        p["draft"], p["draft_src"] = d, src
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(dict(channel=CHANNEL, updated=dt.datetime.now(KST).isoformat(timespec="minutes"),
                   posts=posts), open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("posts", len(posts), "new", new)


if __name__ == "__main__":
    if "--test" in sys.argv:
        print(json.dumps(parse(open(sys.argv[2], encoding="utf-8").read()), ensure_ascii=False, indent=1))
    else:
        main()
