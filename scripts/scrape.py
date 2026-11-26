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
        title = next((l.strip() for l in body.splitlines() if l.strip()), docs[0]["name"] if docs else "(사진)")
        posts.append(dict(id=pid, url=f"https://t.me/{CHANNEL}/{pid}",
                          time=when.isoformat(timespec="minutes") if when else None,
                          title=title[:120], text=body, links=links[:10], docs=docs, photo=photo,
                          cat=category(body)))
    return posts


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
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(dict(channel=CHANNEL, updated=dt.datetime.now(KST).isoformat(timespec="minutes"),
                   posts=posts), open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("posts", len(posts), "new", new)


if __name__ == "__main__":
    if "--test" in sys.argv:
        print(json.dumps(parse(open(sys.argv[2], encoding="utf-8").read()), ensure_ascii=False, indent=1))
    else:
        main()
