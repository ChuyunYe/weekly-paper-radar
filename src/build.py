#!/usr/bin/env python3
"""Collect, rank, summarise and render the weekly paper radar.

Only Python's standard library is required.  Network calls are deliberately kept
behind --refresh so the site can be built locally from a saved data snapshot.
"""
import argparse, datetime as dt, html, json, math, os, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA, SITE = ROOT / "data", ROOT / "site"

def read_json(path):
    with open(path, encoding="utf-8") as f: return json.load(f)

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def clean(text): return re.sub(r"\s+", " ", (text or "")).strip()
def norm_title(title): return re.sub(r"[^a-z0-9]", "", clean(title).lower())
def iso_week(day): return "%04d-W%02d" % day.isocalendar()[:2]

def request_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "weekly-paper-radar/1.0 (academic discovery)"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)

def fetch_arxiv(keywords, since):
    query = " OR ".join('all:"%s"' % keyword for keyword in keywords[:8])
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"search_query": query, "start": 0, "max_results": 40, "sortBy": "submittedDate", "sortOrder": "descending"})
    root = ET.fromstring(urllib.request.urlopen(url, timeout=30).read())
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    papers = []
    for entry in root.findall("a:entry", ns):
        published = clean(entry.findtext("a:published", default="", namespaces=ns))[:10]
        if not published or published < since.isoformat(): continue
        ident = entry.findtext("a:id", default="", namespaces=ns).rsplit("/", 1)[-1]
        author_details = []
        for author in entry.findall("a:author", ns):
            author_details.append({"name": clean(author.findtext("a:name", default="", namespaces=ns)), "affiliations": [clean(x.text) for x in author.findall("arxiv:affiliation", ns) if clean(x.text)]})
        papers.append({"title": clean(entry.findtext("a:title", default="", namespaces=ns)), "authors": [x["name"] for x in author_details], "author_details": author_details, "keywords": [clean(x.get("term")) for x in entry.findall("a:category", ns) if clean(x.get("term"))], "journal": clean(entry.findtext("arxiv:journal_ref", default="", namespaces=ns)) or None, "published": published, "abstract": clean(entry.findtext("a:summary", default="", namespaces=ns)), "url": "https://arxiv.org/abs/" + ident, "arxiv_id": ident, "doi": None, "sources": ["arXiv"], "cited_by_count": 0})
    return papers

def fetch_openalex(keywords, since):
    search = " OR ".join(keywords[:8])
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode({"search": search, "filter": "from_publication_date:" + since.isoformat(), "per-page": 50, "select": "title,authorships,publication_date,abstract_inverted_index,doi,primary_location,keywords,cited_by_count"})
    works = request_json(url).get("results", [])
    papers = []
    for work in works:
        inverted = work.get("abstract_inverted_index") or {}
        words = [None] * (max((i for positions in inverted.values() for i in positions), default=-1) + 1)
        for word, positions in inverted.items():
            for i in positions: words[i] = word
        location = work.get("primary_location") or {}
        author_details = [{"name": clean((a.get("author") or {}).get("display_name")), "affiliations": [clean((i.get("institution") or i).get("display_name")) for i in a.get("institutions", []) if clean((i.get("institution") or i).get("display_name"))]} for a in work.get("authorships", [])]
        source = location.get("source") or {}
        papers.append({"title": clean(work.get("title")), "authors": [x["name"] for x in author_details], "author_details": author_details, "keywords": [clean(x.get("display_name")) for x in work.get("keywords", []) if clean(x.get("display_name"))], "journal": clean(source.get("display_name")) or None, "published": work.get("publication_date") or "", "abstract": " ".join(x for x in words if x), "url": location.get("landing_page_url") or work.get("doi") or "", "arxiv_id": None, "doi": work.get("doi"), "sources": ["OpenAlex"], "cited_by_count": work.get("cited_by_count") or 0})
    return papers

def deduplicate(papers):
    merged = {}
    for paper in papers:
        key = (paper.get("doi") or paper.get("arxiv_id") or norm_title(paper["title"])) .lower()
        if key not in merged: merged[key] = paper
        else:
            old = merged[key]
            old["sources"] = sorted(set(old["sources"] + paper["sources"]))
            old["cited_by_count"] = max(old["cited_by_count"], paper["cited_by_count"])
            if paper.get("doi") and not old.get("doi"):
                old.update({k: v for k, v in paper.items() if v})
    return list(merged.values())

def matches_topic(paper, topic):
    text = (paper["title"] + " " + paper.get("abstract", "")).lower()
    if any(term.lower() in text for term in topic.get("exclude", [])): return False
    return any(term.lower() in text for term in topic["keywords"])

def fallback_summary(paper):
    abstract = clean(paper.get("abstract"))
    return {"question": "This paper examines: " + paper["title"], "method": abstract[:240] or "The source did not provide an abstract.", "contribution": "Check the full paper for its identification assumptions, setting, and empirical details.", "caveat": "This is a conservative fallback generated from the title and source abstract.", "why_read": "It matches one of the configured research topics and is worth triaging this week."}

def ai_summary(paper):
    key = os.getenv("OPENAI_API_KEY")
    if not key: return fallback_summary(paper)
    prompt = "Return an English JSON object with keys question, method, contribution, caveat, why_read. Keep each value under 70 words. Rely only on the provided title and abstract; do not invent findings.\nTitle: %s\nAbstract: %s" % (paper["title"], paper.get("abstract", ""))
    body = json.dumps({"model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), "input": prompt, "text": {"format": {"type": "json_object"}}}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/responses", data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response: payload = json.load(response)
        return json.loads(payload["output_text"])
    except Exception as exc:
        print("OpenAI summary fallback: " + str(exc), file=sys.stderr)
        return fallback_summary(paper)

def render_authors(paper):
    records = paper.get("author_details") or [{"name": name, "affiliations": []} for name in paper.get("authors", [])]
    return "<br>".join("%s%s" % (html.escape(x.get("name") or "Unknown author"), (" <span class=\"affiliation\">— %s</span>" % html.escape("; ".join(x.get("affiliations") or []))) if x.get("affiliations") else "") for x in records)

def render(week, grouped, site_name, refresh_url):
    cards = []
    for topic, papers in grouped:
        cards.append('<section class="topic" data-topic="%s"><h2>%s <span>%d papers</span></h2>' % (html.escape(topic["id"]), html.escape(topic["name"]), len(papers)))
        for p in papers:
            s = p["summary"]; authors = ", ".join(p["authors"][:4]) + (" et al." if len(p["authors"]) > 4 else "")
            featured = '<p class="featured">Editor\'s pick</p>' if p.get("featured") else ""
            paper_id = p.get("doi") or p.get("arxiv_id") or norm_title(p["title"])
            payload = html.escape(json.dumps({"id":paper_id,"title":p["title"],"url":p["url"],"authors":authors,"published":p["published"],"topic":topic["name"]}), quote=True)
            journal = ('<p><b>Journal:</b> %s</p>' % html.escape(p["journal"])) if p.get("journal") else ""
            keywords_html = ", ".join(html.escape(x) for x in p.get("keywords", [])) or "Not provided by source"
            cards.append('<article class="paper">%s<button class="save-paper" data-paper="%s" type="button">Save paper</button><h3><a href="%s" target="_blank" rel="noreferrer">%s</a></h3><p class="meta">%s · %s · %s</p><p><b>Authors & affiliations:</b><br>%s</p><p><b>Keywords:</b> %s</p>%s<details><summary>Abstract</summary><p>%s</p></details></article>' % (featured,payload,html.escape(p["url"],quote=True),html.escape(p["title"]),html.escape(authors),html.escape(p["published"]),html.escape(" / ".join(p["sources"])),render_authors(p),keywords_html,journal,html.escape(p.get("abstract") or "The source did not provide an abstract.")))
        cards.append("</section>")
    nav = "".join('<button data-filter="%s">%s</button>' % (html.escape(t["id"]), html.escape(t["name"])) for t, _ in grouped)
    script = """const K='weekly-paper-radar:favourites',read=()=>JSON.parse(localStorage.getItem(K)||'[]'),write=x=>localStorage.setItem(K,JSON.stringify(x));function draw(){let x=read(),l=document.querySelector('#favourites-list');document.querySelector('#favourites-count').textContent=x.length;l.innerHTML=x.length?x.map(p=>`<li><a href="${p.url}" target="_blank" rel="noreferrer">${p.title}</a><small>${p.topic} · ${p.published}</small><button data-remove="${p.id}">Remove</button></li>`).join(''):'<li class="empty">No saved papers yet.</li>';document.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{write(read().filter(p=>p.id!==b.dataset.remove));draw()})}document.querySelectorAll('.save-paper').forEach(b=>{let p=JSON.parse(b.dataset.paper),saved=read().some(x=>x.id===p.id);b.textContent=saved?'Saved':'Save paper';b.onclick=()=>{let x=read(),yes=x.some(q=>q.id===p.id);write(yes?x.filter(q=>q.id!==p.id):[p,...x]);b.textContent=yes?'Save paper':'Saved';draw()}});document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>document.querySelectorAll('.topic').forEach(s=>s.hidden=b.dataset.filter!='all'&&s.dataset.topic!=b.dataset.filter));document.querySelector('#toggle-favourites').onclick=()=>document.querySelector('#favourites').hidden=!document.querySelector('#favourites').hidden;document.querySelector('#export-favourites').onclick=()=>{let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(read(),null,2)],{type:'application/json'}));a.download='weekly-paper-radar-favourites.json';a.click();URL.revokeObjectURL(a.href)};draw();"""
    return """<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>%s · %s</title><link rel=\"stylesheet\" href=\"style.css\"><body><header><div class=\"header-row\"><div><p class=\"eyebrow\">WEEKLY RESEARCH BRIEF</p><h1>%s</h1><p>Week %s · automatic collection, editor's picks first</p></div><a class=\"refresh\" href=\"%s\" target=\"_blank\" rel=\"noreferrer\">Refresh now ↗</a></div><nav><button data-filter=\"all\">All papers</button><button id=\"toggle-favourites\" type=\"button\">Saved (<span id=\"favourites-count\">0</span>)</button>%s</nav></header><main><aside id=\"favourites\" hidden><div class=\"favourites-head\"><h2>Saved papers</h2><button id=\"export-favourites\" type=\"button\">Export JSON</button></div><ul id=\"favourites-list\"></ul><p class=\"local-note\">Saved only in this browser.</p></aside>%s</main><footer>Summaries are generated automatically. Always verify claims against the original paper.</footer><script>%s</script></body></html>""" % (html.escape(site_name),week,html.escape(site_name),week,html.escape(refresh_url,quote=True),nav,"".join(cards),script)

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--refresh", action="store_true"); parser.add_argument("--offline", action="store_true"); parser.add_argument("--week")
    args = parser.parse_args(); config = read_json(ROOT / "config/topics.json"); today = dt.date.today(); since = today - dt.timedelta(days=7); week = args.week or iso_week(today)
    snapshot = DATA / "raw" / (week + ".json")
    if args.refresh:
        raw = []
        for topic in config["topics"]:
            raw += fetch_arxiv(topic["keywords"], since) + fetch_openalex(topic["keywords"], since)
        write_json(snapshot, deduplicate(raw))
    elif args.offline:
        raw = read_json(DATA / "sample_papers.json")
    elif snapshot.exists(): raw = read_json(snapshot)
    else: raise SystemExit("No snapshot found. Run with --refresh or --offline.")
    featured_keys = {x.lower() for x in read_json(ROOT / "config/featured.json").get("papers", [])}
    grouped = []
    for topic in config["topics"]:
        selected = []
        for paper in deduplicate(raw):
            if matches_topic(paper, topic):
                paper = dict(paper); paper["score"] = round(math.log1p(paper.get("cited_by_count", 0)) + (1 if topic["name"].lower() in paper["title"].lower() else 0), 3); paper["featured"] = (paper.get("doi") or paper.get("arxiv_id") or "").lower() in featured_keys; paper["summary"] = ai_summary(paper); selected.append(paper)
        grouped.append((topic, sorted(selected, key=lambda x: (x["featured"], x["score"], x["published"]), reverse=True)[:topic.get("max_papers", config["default_max_papers"])]))
    archive = {"week": week, "generated_at": dt.datetime.utcnow().isoformat() + "Z", "topics": [{"id": t["id"], "name": t["name"], "papers": ps} for t, ps in grouped]}
    write_json(DATA / "weeks" / (week + ".json"), archive); SITE.mkdir(exist_ok=True); (SITE / "index.html").write_text(render(week, grouped, config["site_name"], config["manual_refresh_url"]), encoding="utf-8"); write_json(SITE / "data" / (week + ".json"), archive)
    print("Built %s with %d papers" % (week, sum(len(papers) for _, papers in grouped)))
if __name__ == "__main__": main()
