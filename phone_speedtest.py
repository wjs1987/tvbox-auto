#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phone_speedtest.py — 手机 Termux 上跑的「家宽真实播放测速」

原理：
  在手机（连家里 WiFi，与电视同一出口公网 IP）上，对每个采集源取第一条
  真实播放直链，按【单连接】下载视频分片计时，得到 KB/s。
  单连接测速才对应 OK影视/魔屏 的实际播放方式（播放器默认单连接顺序拉流）。
  测出的排名代表你家宽的真实体验，和 GitHub 海外机房测的完全不同。

全部使用 Python 标准库，Termux 免 pip install。

用法：
  python phone_speedtest.py                    # 测 latest.json 全部源，输出排名
  python phone_speedtest.py --top 10           # 只测前 10 个源（先试跑）
  python phone_speedtest.py --workers 6        # 并发数（手机建议 4~8，省电）
  python phone_speedtest.py --cap 1048576      # 每个源最多下载字节（默认 1MB）
  python phone_speedtest.py --push             # 排序后把 latest.json 推到 GitHub
                                               # 需先设环境变量 GITHUB_TOKEN

不改动任何现有文件；--push 也只更新仓库里的 latest.json 内容，配置地址不变。
"""

import argparse
import base64
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
# 强制直连：不走任何系统代理，否则测出来的速度是假的
OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=CTX))

UA_OKHTTP = "okhttp/3.12.0"
UA_BROWSER = ("Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120 Mobile Safari/537.36")

RAW_CONFIG = "https://gh-proxy.com/https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/latest.json"
GH_API = "https://api.github.com"
REPO = "WJS1987/tvbox-auto"
CONFIG_PATH = "latest.json"
BRANCH = "main"


def fetch(url, timeout=12, headers=None, max_bytes=3_000_000, ua=UA_OKHTTP):
    """返回下载到的文本；失败返回 None。"""
    h = {"User-Agent": ua}
    if headers:
        h.update(headers)
    try:
        req = urllib.request.Request(url, headers=h)
        with OPENER.open(req, timeout=timeout) as r:
            return r.read(max_bytes).decode("utf-8", "replace")
    except Exception:
        return None


def get_first_play_url(api, t, timeout, headers):
    """从采集站 API 取第一条播放直链（m3u8/mp4）。失败返回 None。"""
    try:
        sep = "&" if "?" in api else "?"
        if t == 1:
            url = api + sep + "ac=list"
        else:
            url = api if "ac=" in api else api + sep + "ac=videolist"
        body = fetch(url, timeout=timeout, max_bytes=3_000_000, headers=headers)
        if not body:
            return None
        pu = None
        if t == 1:
            try:
                data = json.loads(body)
                lst = data.get("list") or data.get("vod_list") or []
                if lst:
                    pu = str(lst[0].get("vod_play_url") or "")
                if not (pu and "$" in pu) and lst:
                    vid = lst[0].get("vod_id")
                    base = api.split("?")[0]
                    d = fetch(base + "?ac=detail&ids=" + str(vid),
                              timeout=timeout, max_bytes=3_000_000, headers=headers)
                    if d:
                        dl = json.loads(d).get("list") or []
                        if dl:
                            pu = str(dl[0].get("vod_play_url") or "")
            except Exception:
                return None
        else:
            m = re.search(r"<dd[^>]*>([^<]+)</dd>", body) or \
                re.search(r"<play[^>]*>([^<]+)</play>", body)
            pu = m.group(1) if m else ""
        if not pu or "$" not in pu:
            return None
        first = pu.split("#")[0]
        url = first.split("$", 1)[-1].strip()
        low = url.lower()
        if url.startswith("http") and not low.startswith(
                ("magnet:", "ed2k:", "thunder:", "115://")):
            return url
        return None
    except Exception:
        return None


def speed_of_segment(url, headers, timeout, cap_bytes):
    """单连接下载分片计时，返回 (KB/s, 下载字节数, 毫秒)。"""
    h = {"User-Agent": UA_OKHTTP, "Range": "bytes=0-"}
    if headers:
        h.update(headers)
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers=h)
        with OPENER.open(req, timeout=timeout) as r:
            data = r.read(cap_bytes)
        ms = int((time.perf_counter() - t0) * 1000)
        n = len(data)
        if n < 1024:
            return None, n, ms
        return round(n / 1024 / (ms / 1000.0), 1), n, ms
    except Exception:
        return None, 0, int((time.perf_counter() - t0) * 1000)


def measure_site(site, timeout, cap_bytes):
    """测一个源的真实单连接播放速度。"""
    r = {"name": site.get("name") or site.get("key", "?"),
         "key": site.get("key", ""), "api": (site.get("api") or "").strip(),
         "speed": None, "note": ""}
    t = site.get("type", 0)
    api = (site.get("api") or "").strip()
    headers = site.get("header") if isinstance(site.get("header"), dict) else None
    if t == 3 or not api.startswith("http"):
        r["note"] = "非直连环/无效API，跳过"
        return r
    play = get_first_play_url(api, t, timeout, headers)
    if not play:
        r["note"] = "取不到播放直链"
        return r
    segurl = play
    if ".m3u8" in play.lower():
        m3u8 = fetch(play, timeout=timeout, max_bytes=200_000, headers=headers)
        if not m3u8:
            r["note"] = "m3u8 索引失败"
            return r
        lines = [x.strip() for x in m3u8.splitlines()
                 if x.strip() and not x.startswith("#")]
        # m3u8 标准：非 # 开头的行就是媒体分片 URI，不一定带 .ts 后缀
        sub = next((x for x in lines if ".m3u8" in x.lower()), None)
        if sub:
            segurl = urllib.parse.urljoin(play, sub)
            m3u8 = fetch(segurl, timeout=timeout, max_bytes=200_000,
                         headers=headers) or ""
            lines = [x.strip() for x in m3u8.splitlines()
                     if x.strip() and not x.startswith("#")]
        seg = next((x for x in lines if not x.startswith("#")), None)
        if not seg:
            r["note"] = "索引里无媒体分片"
            return r
        segurl = urllib.parse.urljoin(segurl, seg)
    kb, n, ms = speed_of_segment(segurl, headers, timeout + 5, cap_bytes)
    r["speed"] = kb
    r["note"] = "%dKB/%dms" % (n // 1024, ms)
    return r


def load_config(args):
    if args.file and os.path.exists(args.file):
        with open(args.file, encoding="utf-8") as f:
            return json.load(f)
    txt = fetch(RAW_CONFIG, timeout=20, max_bytes=5_000_000, ua=UA_BROWSER)
    if not txt:
        print("[错误] 配置拉取失败，请用 --file 指定本地 latest.json")
        sys.exit(1)
    return json.loads(txt)


def push_file(path, content, token, msg=None):
    """用 GitHub Contents API 更新仓库里任意文件。"""
    import urllib.error
    url = "%s/repos/%s/contents/%s?ref=%s" % (GH_API, REPO, path, BRANCH)
    sha = None
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "phone-speedtest", "Authorization": "token " + token,
            "Accept": "application/vnd.github+json"})
        with OPENER.open(req, timeout=20) as r:
            sha = json.loads(r.read().decode()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    payload = {
        "message": msg or ("chore: update %s (%s)" %
                           (path, time.strftime("%Y-%m-%d %H:%M"))),
        "content": base64.b64encode(content.encode()).decode(),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    req = urllib.request.Request(
        "%s/repos/%s/contents/%s" % (GH_API, REPO, path),
        data=json.dumps(payload).encode(), method="PUT",
        headers={"User-Agent": "phone-speedtest",
                 "Authorization": "token " + token,
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    with OPENER.open(req, timeout=30) as r:
        return r.status


def push_config(token, content):
    """兼容旧调用：更新 latest.json。"""
    return push_file(CONFIG_PATH, content, token,
                     "chore: reorder sites by home broadband speed (%s)" %
                     time.strftime("%Y-%m-%d %H:%M"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="", help="本地 latest.json 路径（默认从 GitHub 拉）")
    ap.add_argument("--top", type=int, default=0, help="只测前 N 个源（0=全部）")
    ap.add_argument("--workers", type=int, default=6, help="并发数（手机建议 4~8）")
    ap.add_argument("--cap", type=int, default=1_048_576, help="每源最多下载字节")
    ap.add_argument("--timeout", type=float, default=12, help="单次超时秒数")
    ap.add_argument("--out", default="speed_rank.json", help="排名结果输出文件")
    ap.add_argument("--push", action="store_true", help="排序后推送 latest.json 到 GitHub")
    a = ap.parse_args()

    cfg = load_config(a)
    sites = cfg.get("sites", [])
    if a.top:
        sites = sites[:a.top]
    print("[测速] 共 %d 个源，单连接，每个上限 %dKB，并发 %d" %
          (len(sites), a.cap // 1024, a.workers))
    print("[测速] 出口 IP 必须与电视相同（手机连家里 WiFi）\n")

    results = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(measure_site, s, a.timeout, a.cap): s for s in sites}
        done = 0
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            done += 1
            print("[%2d/%d] %-22s %s" %
                  (done, len(sites), r["name"][:22],
                   ("%6.1f KB/s" % r["speed"]) if r["speed"] else "× " + r["note"]))

    ok = [r for r in results if r["speed"]]
    ok.sort(key=lambda r: -r["speed"])
    print("\n===== 家宽真实播放速度排名（单连接，KB/s）=====")
    for i, r in enumerate(ok, 1):
        print("%2d. %-24s %7.1f KB/s" % (i, r["name"][:24], r["speed"]))
    print("\n可测速源：%d / %d" % (len(ok), len(results)))

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "unit": "KB/s", "rank": ok, "all": results},
                  f, ensure_ascii=False, indent=2)
    print("[输出] 排名已写入 " + a.out)

    if a.push:
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            # 手机端：token 存在脚本同目录的 token.txt
            tf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")
            if os.path.exists(tf):
                token = open(tf, encoding="utf-8").read().strip()
        if not token:
            print("[错误] --push 需要环境变量 GITHUB_TOKEN 或同目录 token.txt")
            sys.exit(2)
        # 三组排序：快源(>=10KB/s)按速度降序 → 未测源保持原位 → 慢源(<10KB/s)沉底
        order = {r["key"]: r["speed"] for r in results}
        old_idx = {s.get("key"): i for i, s in enumerate(cfg["sites"])}
        def rank(s):
            sp = order.get(s.get("key"))
            if sp is None:
                return (1, -old_idx.get(s.get("key"), 0), 0)   # 未测：居中，保持原位
            if sp >= 10:
                return (0, -sp, 0)                              # 快源：置顶，速度降序
            return (2, sp, old_idx.get(s.get("key"), 0))       # 慢源：沉底
        cfg["sites"].sort(key=rank)
        content = json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))
        code = push_config(token, content)
        print("[推送] latest.json 已按速度重排并推送，HTTP %s" % code)
        # 同时提交家宽速度排名（含 key+speed），供 GitHub Actions 重建配置后套用
        speed_rank = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "unit": "KB/s",
            "source": "home broadband, measured by phone (single connection)",
            "rank": [{"key": r["key"], "name": r["name"], "api": r.get("api", ""),
                      "speed": r["speed"]}
                     for r in results if r.get("speed")],
        }
        code2 = push_file("home_speed_rank.json",
                          json.dumps(speed_rank, ensure_ascii=False, indent=2),
                          token, "chore: update home broadband speed rank (%s)" %
                          time.strftime("%Y-%m-%d %H:%M"))
        print("[推送] home_speed_rank.json 速度排名已更新，HTTP %s" % code2)
        print("[提示] CDN 可能有缓存，电视端几小时后生效；配置地址不变")


if __name__ == "__main__":
    main()
