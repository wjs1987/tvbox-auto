#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
device_test.py — 在家庭网络下对 latest.json 做端到端实测（给魔屏/OK影视用）。

GitHub Actions 机房在海外，测出来的存活/速度对国内家宽没有意义。
这个脚本在用户家里的电脑上运行（与魔屏同一个出口网络），测出来的数据
才代表魔屏实际体验。全部使用 Python 标准库，无需 pip install。

用法：
    python device_test.py                     # 测仓库目录下的 latest.json
    python device_test.py --quick             # 每条链路只测一次，速度更快
    python device_test.py --url <配置地址>     # 额外测试自定义配置地址
    python device_test.py --live-limit 100    # 多抽一些直播频道
"""
import argparse
import json
import ssl
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
# 强制直连：魔屏没有系统代理，若脚本走了本机 HTTP_PROXY，测出来的存活/速度全是假象
# （实测曾把 43333 端口的图片域误判为"502 代理失败"，实际家宽直连是通的）
OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=CTX))
UA = "okhttp/3.12.0"
REPO = "WJS1987/tvbox-auto"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/latest.json"
LINKS = {
    "jsdelivr": f"https://cdn.jsdelivr.net/gh/{REPO}@main/latest.json",
    "raw": RAW,
    "gh-proxy": f"https://gh-proxy.com/{RAW}",
}


def fetch_bytes(url, timeout=12, headers=None, max_bytes=2_000_000):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers=h)
        with OPENER.open(req, timeout=timeout) as r:
            return r.read(max_bytes), int((time.perf_counter() - t0) * 1000)
    except Exception:
        return None, int((time.perf_counter() - t0) * 1000)


def time_config(n=2, extra_url=""):
    links = dict(LINKS)
    if extra_url:
        links["自定义"] = extra_url
    out = []
    cb = f"?t={int(time.time())}"  # 穿透 CDN 缓存，确保拉到刚推送的版本
    for label, base in links.items():
        times, size, ok = [], None, 0
        for _ in range(n):
            b, ms = fetch_bytes(base + cb, timeout=15, max_bytes=500_000)
            times.append(ms)
            if b:
                ok += 1
                size = len(b)
        out.append((label, min(times), sorted(times)[len(times)//2], size, ok))
    return out, n


def check_spider(url):
    if not url:
        return None
    b, ms = fetch_bytes(url, timeout=15, max_bytes=2_000_000)
    if not b:
        return {"ok": False, "ms": ms, "note": "下载失败"}
    return {"ok": b[:4] == b"PK\x03\x04" and len(b) > 10000, "ms": ms,
            "size": len(b), "head": b[:4].hex()}


def check_super(site):
    api = site.get("api", "")
    hdr = site.get("header") or {}
    results = []
    for q in ("ac=list&pg=1", "ac=list&pg=1&t=c_movie", "ac=detail&ids={id}"):
        sep = "&" if "?" in api else "?"
        query = q
        if "{id}" in q:
            b0, _ = fetch_bytes(api + sep + "ac=list", timeout=15, headers=hdr, max_bytes=500_000)
            vid = ""
            if b0:
                try:
                    lst = json.loads(b0.decode("utf-8", "replace")).get("list") or []
                    vid = str(lst[0].get("vod_id", "")) if lst else ""
                except Exception:
                    pass
            if not vid:
                continue
            query = q.replace("{id}", vid)
        b, ms = fetch_bytes(api + sep + query, timeout=18, headers=hdr, max_bytes=1_000_000)
        ok, count = False, 0
        if b:
            try:
                d = json.loads(b.decode("utf-8", "replace"))
                ok = isinstance(d, dict) and ("list" in d or "class" in d)
                count = len(d.get("list") or [])
            except Exception:
                pass
        results.append({"q": query, "ok": ok, "ms": ms, "count": count})
    return results


def check_live(channels, limit):
    def one(ch):
        url = (ch.get("urls") or [""])[0]
        if not url:
            return None
        b, ms = fetch_bytes(url, timeout=10, max_bytes=40_000)
        txt = b[:300].decode("utf-8", "replace") if b else ""
        return {"name": ch.get("name", ""), "ok": bool(b and (
            "#EXTM3U" in txt or "<" in txt)), "ms": ms}
    key = {"央视": 0, "卫视": 1}
    picked = sorted(channels, key=lambda c: next((v for k, v in key.items()
                    if k in (c.get("name") or "")), 9))[:limit]
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(one, c) for c in picked]
        return [f.result() for f in as_completed(futs) if f.result()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="latest.json")
    ap.add_argument("--url", default="", help="额外测试的自定义配置地址")
    ap.add_argument("--quick", action="store_true", help="每条链路只测一次")
    ap.add_argument("--out", default="device_report.md")
    ap.add_argument("--live-limit", type=int, default=60)
    a = ap.parse_args()

    rep = ["# 魔屏端到端实测报告", "",
           f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
           "- 网络：家庭宽带（与魔屏同一出口），脚本 `device_test.py`", ""]

    # 1. 配置下载链路
    rows, n = time_config(1 if a.quick else 2, a.url)
    rep += ["## 一、配置下载（latest.json，带时间戳穿透缓存）", "",
            f"| 链路 | 最快 | 中位 | 大小 | 成功/{n} |", "|---|---|---|---|---|"]
    for label, lo, mid, size, ok in rows:
        rep.append(f"| {label} | {lo}ms | {mid}ms | {size or '-'}B | {ok}/{n} |")
    rep.append("")

    p = Path(a.file)
    data = json.loads(p.read_text(encoding="utf-8"))
    sites = data.get("sites", [])
    rep += [f"配置体积：{p.stat().st_size // 1024}KB，站点 {len(sites)} 个，直播源 {len(data.get('lives', []))} 个", ""]

    # 2. 站点检测
    rep += ["## 二、点播站点（家庭网络实测，复用 check_sources.py）", "",
            "| 源 | 类型 | 列表 | 可播 | 延迟 | 说明 |", "|---|---|---|---|---|---|"]
    try:
        import check_sources as cs
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(lambda s: cs.test_site(s, 10), sites))
    except Exception as e:
        rep.append(f"检测失败：{e}")
        results = []
    for s, r in zip(sites, results):
        rep.append(f"| {s.get('name', '')[:18]} | {s.get('type')} | "
                   f"{'OK' if r['ok'] else '×'} | {r.get('play')} | {r.get('ms') or '-'}ms | {r.get('note', '')} |")
    rep.append("")

    # 3. 超级源与爬虫包
    rep += ["## 三、超级源与爬虫包", ""]
    for s in sites:
        if s.get("key") == "super":
            for r in check_super(s):
                rep.append(f"- `{r['q']}` → {'OK' if r['ok'] else '×'} {r['ms']}ms list={r['count']}")
    sp = check_spider(data.get("spider", ""))
    rep.append(f"- spider jar：{'OK' if sp and sp['ok'] else '×'} "
               f"{(sp or {}).get('ms', '-')}ms {(sp or {}).get('size', '-')}B "
               f"头={(sp or {}).get('head', '-')}")
    rep.append("")

    # 4. 直播频道抽样（兼容 channel / channels 两种字段名）
    channels = []
    for lv in data.get("lives", []):
        for g in lv.get("groups", []):
            channels += (g.get("channel") or g.get("channels") or [])
    if channels:
        rs = check_live(channels, a.live_limit)
        ok = sum(1 for r in rs if r["ok"])
        rep += ["## 四、直播频道抽样", "",
                f"- 抽样 {len(rs)} 个频道，列表可拉通 {ok} 个（{ok*100//max(1,len(rs))}%）",
                "", "| 频道 | 结果 | 耗时 |", "|---|---|---|"]
        for r in sorted(rs, key=lambda x: x["ms"])[:20]:
            rep.append(f"| {r['name'][:24]} | {'OK' if r['ok'] else '×'} | {r['ms']}ms |")
        rep.append("")
        rep.append("- 说明：只测列表可达，**不代表画面能解码播出**（HLS 需魔屏实播验证）")
        rep.append("")

    Path(a.out).write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep[:16]))
    print(f"\n[输出] {a.out}")


if __name__ == "__main__":
    main()
