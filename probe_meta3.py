import json, re, time, urllib.request, urllib.parse, concurrent.futures

UA = "okhttp/4.9.3"

def get(url, timeout=10, tries=3):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(0.8)
    raise last

def class_url(api):
    sep = "&" if "?" in api else "?"
    return api + sep + "ac=list"

def parse_cats(txt):
    cats = []
    try:
        d = json.loads(txt)
        for c in d.get("class", []) or []:
            tn = c.get("type_name")
            if tn: cats.append(str(tn).strip())
        if cats: return cats
    except Exception:
        pass
    for m in re.findall(r'<ty[^>]*>\s*(.*?)\s*</ty>', txt, re.S):
        cats.append(m.strip())
    if cats: return cats
    return re.findall(r'"type_name"\s*:\s*"([^"]+)"', txt)

def probe(s):
    api, name = s.get("api") or "", s.get("name")
    if not api.startswith("http"):
        return {"name": name, "api": api, "categories": [], "err": "non-http"}
    try:
        cats = parse_cats(get(class_url(api)))
        return {"name": name, "api": api, "categories": cats, "err": "" if cats else "no-class"}
    except Exception as e:
        return {"name": name, "api": api, "categories": [], "err": str(e)[:70]}

STRONG = ['无码','有码','番号','禁漫','里番','乱伦','探花','黑料','av','国产精品','主播',
          '自拍','偷拍','巨乳','淫','色情','大奶','萝莉','嫩模','三级','传媒','换脸','外流','人妻',
          '口爆','颜射','调教','群交','美尻','丝袜','制服诱惑','素人','名优','swag','cosplay','门事件']
HARD = ['无码','有码','番号','禁漫','里番','av','乱伦','探花','黑料','色情','淫','大奶','杏吧']

def hits_of(cats):
    return [w for w in STRONG if any(w in c.lower() for c in cats)]

def main():
    cfg = json.load(open("latest.json", encoding="utf-8"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        recs = list(ex.map(probe, cfg["sites"]))
    for r in recs:
        r["hits"] = hits_of(r["categories"])
        hard = [w for w in HARD if w in r["hits"]]
        r["adult"] = bool(hard) or len(r["hits"]) >= 3
    json.dump(recs, open("site_meta2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    blocked, failed = {}, []
    for r in recs:
        host = urllib.parse.urlsplit(r["api"]).netloc if r["api"].startswith("http") else r["api"]
        if r["adult"]: blocked.setdefault(host, []).append(r["name"])
        if r["err"]: failed.append((r["name"], host, r["err"]))
    json.dump({"tested_at": "2026-11-28", "note": "kids.json 屏蔽域名清单（家宽实测分类判定）",
               "hosts": sorted(blocked)}, open("kids_block_hosts.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("blocked hosts:", len(blocked), "| adult sites:", sum(1 for r in recs if r["adult"]))
    for h, v in sorted(blocked.items()): print(" ", h, "|", ",".join(v))
    print("failed probes:", len(failed))
    for n, h, e in failed: print("  !", n, h, e)

main()
