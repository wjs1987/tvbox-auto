# -*- coding: utf-8 -*-
"""
直播源整理流水线（Live Config Builder）
- 抓取多个公开 IPTV 聚合源
- 频道名归一化 + 分组（央视/卫视/地方/港澳台/影视/少儿/体育/国际/其他）
- 同一频道多线路合并去重，按实测速度排序
- 输出 FongMi LiveConfig（live.json）+ 通用 TXT（live.txt）+ 统计
用法:
  python build_live.py                # 仅整理，不测速
  python build_live.py --test         # 整理 + 线路可用性/速度实测
  python build_live.py --test --limit-urls 3
"""
import argparse
import json
import os
import re
import sys
import time
import socket
import ssl
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

SOURCES = [
    # name, url, 权重（越小越优先）
    # 2026-11-28 家宽实测：vbskycn/iptv 的 iptv4 列表央视/卫视可用率 8/10，其余公开列表大面积 404/超时，
    # 因此直播只保留这一条经过国内验证的列表。
    ("vbskycn", "https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.txt", 1),
]


# 饭太硬同款“一起看”轮播列表，作为独立直播源挂载（不参与合并测速）
EXTRA_LIVES = [
    {"name": "虎牙一起看", "url": "https://sub.ottiptv.cc/huyayqk.m3u",
     "ua": "okHttp/Mod-1.5.0.0", "timeout": 10},
    {"name": "斗鱼一起看", "url": "https://sub.ottiptv.cc/douyuyqk.m3u",
     "ua": "okHttp/Mod-1.5.0.0", "timeout": 10},
    {"name": "YY轮播", "url": "https://sub.ottiptv.cc/yylunbo.m3u",
     "ua": "okHttp/Mod-1.5.0.0", "timeout": 10},
]

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# 测速必须绕过本机代理：直播是盒子在家庭宽带上直接拉的，走代理测出来的结果没有意义
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                     urllib.request.HTTPSHandler(context=CTX))


def fetch(url, timeout=25, retries=2):
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "*/*", "Referer": "https://github.com/"})
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                data = r.read()
                return data.decode("utf-8", "ignore")
        except Exception as e:
            if i == retries:
                print(f"  [warn] fetch failed {url[:70]} -> {type(e).__name__}")
                return ""
            time.sleep(1.5)


# ---------------- 解析 ----------------
def parse_m3u(text):
    out = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    cur = None
    for l in lines:
        if l.startswith("#EXTINF"):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', l))
            name = l.rsplit(",", 1)[-1].strip() if "," in l else ""
            cur = {
                "raw_name": name or attrs.get("tvg-name", ""),
                "tvg_name": attrs.get("tvg-name", ""),
                "logo": attrs.get("tvg-logo", ""),
                "group": attrs.get("group-title", ""),
                "tvg_id": attrs.get("tvg-id", ""),
            }
        elif l.startswith("#"):
            continue
        elif cur is not None:
            cur["url"] = l
            out.append(cur)
            cur = None
    return out


def parse_txt(text):
    """解析 `频道名,http://...` 或带 `#genre#` 分组的 txt 直播表。"""
    out = []
    for l in text.splitlines():
        l = l.strip()
        if not l or "#genre#" in l:
            continue
        if "," in l and "http" in l:
            i = l.index(",")
            name, url = l[:i].strip(), l[i + 1:].strip()
            if url.startswith("http"):
                out.append({"raw_name": name, "tvg_name": "", "logo": "",
                            "group": "", "tvg_id": "", "url": url})
    return out


# ---------------- 归一化 ----------------
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13,
          "十四": 14, "十五": 15, "十六": 16, "十七": 17}

CCTV_SUB = {
    1: "综合", 2: "财经", 3: "综艺", 4: "中文国际", 5: "体育", 6: "电影",
    7: "国防军事", 8: "电视剧", 9: "纪录", 10: "科教", 11: "戏曲", 12: "社会与法",
    13: "新闻", 14: "少儿", 15: "音乐", 16: "奥林匹克", 17: "农业农村",
}

DROP_RE = re.compile(r"(测试|试看|失效|无效|已挂|广告|更新时间|^\s*$)")
# 成人 / 赌博类频道一律不收录
BLOCK_RE = re.compile(r"(成人|色情|情色|蜜桃|午夜|一级片|三级片|18\+|A片|AV女|博彩|赌博|彩票|六合彩|赌场|棋牌|裸|艳|福利视频)")


def clean_name(n):
    n = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF]", "", n)  # emoji
    n = n.replace("　", " ").strip()
    n = re.sub(r"\((?:[^)]*\d{3,4}[pi][^)]*|Geo-blocked|Not 24/7|Only [^)]*)\)", "", n, flags=re.I)
    n = re.sub(r"\[[^\]]*\]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n.strip()


def normalize(name, group=""):
    """返回 (标准频道名, 大分组) 或 (None, None) 表示丢弃"""
    g0 = clean_name(group)
    # Guovin 的“更新时间”分组直接丢弃（内容是时间戳）
    if re.search(r"更新时间|^来源", g0) or re.match(r"^\d{4}-\d{2}-\d{2}", clean_name(name)):
        return None, None
    n = clean_name(name)
    if not n or DROP_RE.search(n) or BLOCK_RE.search(n):
        return None, None
    # 去掉画质/源标识后缀
    n = re.sub(r"(超高清|高清|标清|蓝光|4K|8K|1080P|720P|HD|SD|IPV6|IPv6|ipv6|备用\d*|线路\d*|测试\d*)$", "", n).strip(" -_|")

    # 央视
    m = re.search(r"CCTV[\s\-_]*(\d{1,2})\s*(\+)?", n, re.I)
    if not m:
        m2 = re.search(r"央视([一二三四五六七八九十]+)套?", n)
        if m2 and m2.group(1) in CN_NUM:
            num = CN_NUM[m2.group(1)]
            full = f"CCTV-{num}"
            sub = CCTV_SUB.get(num)
            return (f"{full} {sub}" if sub else full), "央视频道"
    if m:
        num = int(m.group(1))
        plus = "+" if m.group(2) else ""
        if 1 <= num <= 17:
            full = f"CCTV-{num}{plus}"
            sub = CCTV_SUB.get(num)
            return (f"{full} {sub}" if sub else full), "央视频道"
        return f"CCTV-{num}{plus}", "央视频道"

    # CGTN
    if re.search(r"CGTN|中国国际电视台", n, re.I):
        return "CGTN", "国际频道"

    # 卫视
    prov = ("北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|"
            "河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|内蒙古|广西|西藏|宁夏|新疆")
    m = re.search(rf"^({prov})(卫视|电视台?卫星|卫视国际)", n)
    if m:
        return f"{m.group(1)}卫视", "卫视频道"
    m = re.search(rf"({prov})卫视", n)
    if m:
        return f"{m.group(1)}卫视", "卫视频道"

    # 港澳台
    if re.search(r"凤凰|翡翠|明珠|TVB|无线|澳视|澳门|香港|台湾|中天|东森|三立|民视|台视|中视|华视|寰宇", n, re.I):
        return n, "港澳台"

    # 少儿
    if re.search(r"卡酷|优漫|嘉佳|金鹰卡通|哈哈炫动|动漫|少儿|卡通", n):
        return n, "少儿动漫"

    # 体育
    if re.search(r"体育|足球|篮球|NBA|英超|西甲|中超|赛事|竞技", n):
        return n, "体育"

    # 影视轮播
    if re.search(r"影院|剧场|电影|电视剧|轮播|剧场|影迷|华语|高清电影|动作|喜剧|科幻|武侠", n):
        return n, "影视轮播"

    # 地方台（用 m3u 自带分组辅助）
    g = clean_name(group)
    if re.search(r"(频道|电视台)$", n) or re.search(r"频道$", g):
        gname = re.sub(r"^(☘️|📺|🕘️|🔵|🟢|🟡|🟠|🔴)", "", g)
        if re.search(r"卫视", gname):
            return n, "卫视频道"
        if re.search(r"央视", gname):
            return n, "央视频道"
        return n, "地方频道"

    if re.search(r"(新闻|综合|公共|都市|经济|生活|影视|文旅|法制|科教)$", n):
        return n, "地方频道"

    # 国际
    if re.search(r"^[A-Za-z0-9 .&'()\-]+$", n):
        return n, "国际频道"

    return n, "其他"


GROUP_ORDER = ["央视频道", "卫视频道", "地方频道", "港澳台", "影视轮播", "少儿动漫", "体育", "国际频道", "其他"]

# 运营商特征（决定同一路源在你家宽带能不能连）：IPv6 段 / 域名关键字
ISP_HINT = [
    ("mobile", re.compile(r"chinamobile|cmcc|2409:|39\.134\.|39\.135\.|otttv", re.I)),
    ("telecom", re.compile(r"chinatelecom|189\.cn|240e:|61\.139\.|61\.128\.", re.I)),
    ("unicom", re.compile(r"chinaunicom|unicom|2408:", re.I)),
]


def isp_of(url):
    for name, rx in ISP_HINT:
        if rx.search(url):
            return name
    return "any"


# ---------------- 测速 ----------------
def _get(url, nbytes, timeout, ua=UA):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
    with DIRECT.open(req, timeout=timeout) as r:
        return r.read(nbytes), r.headers.get("Content-Type", "")


def test_url(url, timeout=8, seg_bytes=400 * 1024):
    """返回 (ok, m3u8_ms, kbps)。kbps 由首个 ts/fmp4 分片实测得出"""
    url = url.split("$", 1)[0]
    if not url.startswith(("http://", "https://")):
        return False, 9999, 0
    t0 = time.time()
    try:
        body, ctype = _get(url, 64 * 1024, timeout)
    except Exception:
        return False, 9999, 0
    ms = int((time.time() - t0) * 1000) or 1
    txt = body.decode("utf-8", "ignore")
    is_m3u = txt.lstrip().startswith("#EXTM3U") or "mpegurl" in ctype.lower()
    if not is_m3u:
        # 直链 ts/flv
        return True, ms, int(len(body) * 8 / ms)
    # 取第一个分片
    seg = None
    for line in txt.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            seg = line
            break
    if not seg:
        return False, ms, 0
    if not seg.startswith("http"):
        from urllib.parse import urljoin
        seg = urljoin(url, seg)
    t1 = time.time()
    try:
        data, _ = _get(seg, seg_bytes, timeout)
        dt = (time.time() - t1) or 0.001
        if not data:
            return True, ms, 0
        return True, ms, int(len(data) * 8 / dt)
    except Exception:
        # 分片拉不到（可能是鉴权/防盗链），但 m3u8 有效 → 保留为低优先
        return True, ms, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="实测线路可用性与速度")
    ap.add_argument("--limit-urls", type=int, default=3, help="每频道最多保留线路数")
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--test-urls", type=int, default=5, help="每个频道最多测几条线路")
    ap.add_argument("--isp", default="", choices=["", "mobile", "telecom", "unicom"],
                    help="你家宽带运营商，同运营商线路优先")
    args = ap.parse_args()

    def isp_rank(u):
        if not args.isp:
            return 0
        v = isp_of(u)
        return 0 if v == args.isp else (1 if v == "any" else 2)

    raw = []
    for name, url, prio in SOURCES:
        print(f"[源] {name} ...")
        txt = fetch(url)
        items = parse_m3u(txt) if "#EXTM3U" in txt[:2000] or "#EXTINF" in txt             else parse_txt(txt)
        print(f"     {len(items)} 条")
        for it in items:
            it["src"], it["prio"] = name, prio
        raw += items

    channels = {}   # name -> {group, urls: [(url, src, prio)], logo}
    dropped = 0
    for it in raw:
        std, grp = normalize(it["raw_name"] or it["tvg_name"], it.get("group", ""))
        if not std:
            dropped += 1
            continue
        c = channels.setdefault(std, {"name": std, "group": grp,
                                      "urls": [], "logo": it.get("logo", "")})
        u = it["url"]
        if any(x[0] == u for x in c["urls"]):
            continue
        c["urls"].append((u, it["src"], it["prio"]))
        if not c["logo"] and it.get("logo"):
            c["logo"] = it["logo"]

    print(f"\n[整理] 原始 {len(raw)} 条 → 频道 {len(channels)} 个（丢弃 {dropped}）")

    # 每个频道内按源权重排序
    for c in channels.values():
        c["urls"].sort(key=lambda x: (isp_rank(x[0]), x[2]))

    if args.test:
        tasks = []
        for c in channels.values():
            for i, (u, s, p) in enumerate(c["urls"][: args.test_urls]):
                tasks.append((c["name"], u))
        print(f"[测速] {len(tasks)} 条线路，并发 {args.workers} ...")
        result = {}
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(test_url, u): u for _, u in tasks}
            for f in as_completed(futs):
                u = futs[f]
                try:
                    result[u] = f.result()
                except Exception:
                    result[u] = (False, 9999, 0)
                done += 1
                if done % 100 == 0:
                    print(f"     {done}/{len(tasks)}")

        alive_ch = 0
        for c in channels.values():
            scored = []
            for (u, s, p) in c["urls"]:
                ok, ms, kbps = result.get(u, (None, 0, 0))
                if ok is True:
                    scored.append((u, s, p, ms, kbps))
            scored.sort(key=lambda x: (isp_rank(x[0]), -x[4], x[3]))
            c["urls"] = [(u, s, p) for (u, s, p, ms, kbps) in scored]
            c["_speed"] = {u: (ms, kbps) for (u, s, p, ms, kbps) in scored}
            if scored:
                alive_ch += 1
        print(f"[测速] 可播放频道 {alive_ch}/{len(channels)}")
        channels = {k: v for k, v in channels.items() if v["urls"]}

    # 输出
    groups = {g: [] for g in GROUP_ORDER}
    for c in channels.values():
        g = c["group"] if c["group"] in groups else "其他"
        line = {"name": c["name"], "urls": [u for (u, s, p) in c["urls"][: args.limit_urls]]}
        if c.get("logo"):
            line["logo"] = c["logo"]
        groups[g].append(line)

    main_live = {
        "name": "自建直播",
        "epg": "https://live.fanmingming.cn/e.xml",
        "ua": UA,
        "timeout": 10,
        "groups": [{"name": g, "channel": sorted(v, key=lambda x: x["name"])}
                   for g, v in groups.items() if v],
    }

    # 只输出通用 TXT：魔屏配置里直播走 url 引用 live.txt，live.json 已不再被任何地方消费。
    _ = main_live  # 保留结构定义，便于以后要恢复 JSON 格式时直接用

    # TXT（通用格式，取每频道最优线路）
    with open("live.txt", "w", encoding="utf-8") as f:
        for g, v in groups.items():
            if not v:
                continue
            f.write(f"{g},#genre#\n")
            for ch in v:
                f.write(f"{ch['name']},{ch['urls'][0]}\n")
            f.write("\n")

    stat = {g: len(v) for g, v in groups.items() if v}
    print("\n[统计] 分组频道数:", json.dumps(stat, ensure_ascii=False))
    print("[统计] 线路总数:", sum(len(c['urls']) for c in channels.values()))
    with open("live_stats.json", "w", encoding="utf-8") as f:
        json.dump({"groups": stat, "channels": len(channels),
                   "urls": sum(len(c["urls"]) for c in channels.values())},
                  f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
