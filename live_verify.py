"""把 live.txt 里的伪直播（序列不动）剔掉，只留真直播。手动跑，不进 Actions。"""
import re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "okhttp/4.9.3"
SRC = sys.argv[1] if len(sys.argv) > 1 else "live.txt"
OUT = sys.argv[2] if len(sys.argv) > 2 else "live_verified.txt"
GAP = 8


def fetch(u, t=10):
    r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=t)
    with r:
        return r.read(300000).decode("utf-8", "ignore")


def probe(rec):
    grp, name, url = rec
    try:
        t1 = fetch(url)
        if "#EXTM3U" not in t1[:100]:
            return rec, "非m3u8"
        if "#EXT-X-ENDLIST" in t1:
            return rec, "点播"
        s1 = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", t1)
        if not s1:
            return rec, "无序列"
        time.sleep(GAP)
        t2 = fetch(url)
        s2 = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", t2)
        d = int(s2.group(1)) - int(s1.group(1)) if s2 else 0
        return rec, ("真直播" if d > 0 else "序列不动")
    except Exception as e:
        return rec, type(e).__name__


recs, grp = [], "其他"
for line in open(SRC, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    if "#genre#" in line:
        grp = line.split("#genre#")[0].strip().rstrip(",").strip()
    elif "," in line and "http" in line:
        i = line.index(",")
        recs.append((grp, line[:i].strip(), line[i + 1:].strip().split(";")[0].strip()))

print(f"待测 {len(recs)}")
with ThreadPoolExecutor(max_workers=60) as p:
    res = list(p.map(probe, recs))

keep = [r for r, v in res if v == "真直播"]
print(f"真直播 {len(keep)}")
lines, cur = [], None
for grp, name, url in keep:
    if grp != cur:
        lines.append(f"{grp},#genre#")
        cur = grp
    lines.append(f"{name},{url}")
open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
