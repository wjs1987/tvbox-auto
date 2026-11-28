#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox/OK影视 源自动采集 + 检测 + 生成工具 v2

流程（--auto 全自动模式）：
  1. 爬取 sources_pool.json 里的聚合配置站（含多仓递归展开）
  2. 提取所有 点播源(sites) 和 直播源(lives)，按 (type, api) 去重
  3. 并发检测：存活 + 延迟 + 质量评分（返回片单数据量）
  4. 生成 latest.json（只含合格源，按速度排序）+ report.md 检测报告

其他模式：
  python check_sources.py <文件> --check     # 旧模式：只检测单个配置文件
  python check_sources.py --auto --crawl-only# 只爬取统计，不联网测源
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

UA_BROWSER = "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
UA_OKHTTP = "okhttp/3.12.0"  # 聚合配置站大多只认 okhttp UA
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
# 强制直连：测源必须反映真实网络。若本机设了 HTTP_PROXY，urllib 默认走代理，
# 会把"家宽直连可达"的源误判为失效（实测 43333 图片域被代理返回 502）。
# GitHub Actions 上本来就没有代理，行为不变。
OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=CTX))


# ---------- 网络基础 ----------
def ascii_url(u: str) -> str:
    """中文域名/路径转 ASCII，urllib 才能处理（如 肥猫.com）。"""
    try:
        p = urllib.parse.urlsplit(u.strip())
        host = p.hostname or ""
        try:
            host = host.encode("idna").decode()
        except Exception:
            pass
        netloc = host if p.port is None else f"{host}:{p.port}"
        return urllib.parse.urlunsplit((p.scheme, netloc,
                                        urllib.parse.quote(p.path or ""),
                                        urllib.parse.quote(p.query, safe="=&?/:+"), ""))
    except Exception:
        return u


def fetch(url: str, timeout: float = 8, ua: str = UA_OKHTTP, max_bytes: int = 3_000_000,
          headers: dict = None):
    """抓文本。headers 用于携带站点自带请求头（如超级源的 eo_token Cookie）。"""
    try:
        h = {"User-Agent": ua}
        if headers:
            h.update(headers)
        req = urllib.request.Request(ascii_url(url), headers=h)
        with OPENER.open(req, timeout=timeout) as r:
            return r.read(max_bytes).decode("utf-8", errors="replace")
    except Exception:
        return None


def fetch_bytes(url: str, timeout: float = 8, headers: dict = None,
                max_bytes: int = 3_000_000):
    """抓原始字节（jar/zip 校验用）。返回 (bytes|None, 耗时ms)，避免 decode 后字节数失真。"""
    h = {"User-Agent": UA_OKHTTP}
    if headers:
        h.update(headers)
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(ascii_url(url), headers=h)
        with OPENER.open(req, timeout=timeout) as r:
            return r.read(max_bytes), int((time.perf_counter() - t0) * 1000)
    except Exception:
        return None, int((time.perf_counter() - t0) * 1000)


def parse_config_text(text: str):
    """容错解析配置 JSON（跳过 # 注释行、容忍尾逗号——TVBox 配置常见写法）。"""
    import re as _re
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    body = "\n".join(lines)
    body = _re.sub(r",\s*([}\]])", r"\1", body)  # 去除尾逗号
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ---------- 爬取聚合配置 ----------
def parse_spider(sp) -> str:
    """spider 字段可能是 'url;md5;...' 格式，取第一段 url。"""
    if not isinstance(sp, str):
        return ""
    first = sp.split(";")[0].strip()
    # 很多仓把 jar 伪装成 .png/.jpg/.txt 等链接，只要求 http 开头即可，
    # 真伪由后续下载验证（大小 >= 10KB 且 ZIP 头 PK）判定
    return first if first.startswith("http") else ""


def norm_api(api: str) -> str:
    """规范化 API：去 query、去尾斜杠、域名小写。

    用途一：去重。很多仓里同一个后台会写成
    "https://x.com/api.php/provide/vod" 和 "https://x.com/api.php/provide/vod/?ac=list"
    两种形式，原来按原始字符串去重会当成两个源，白占名额。
    """
    raw = (api or "").strip()
    if raw.startswith("csp_"):
        return raw.lower()
    try:
        p = urllib.parse.urlsplit(raw)
        host = (p.hostname or "").lower()
        if not host:
            return raw.rstrip("/").lower()
        port = f":{p.port}" if p.port else ""
        return f"{p.scheme.lower()}://{host}{port}{(p.path or '').rstrip('/')}"
    except Exception:
        return raw.rstrip("/").lower()


def site_key(site: dict) -> str:
    """稳定 key：同一个 API 永远得到同一个 key。

    原来 key 用 src1、src2……按提取顺序递增，池子一变全部重排，
    客户端里收藏的影片/播放进度会全部失效。改成 API 的短哈希后就固定了。
    """
    import hashlib
    base = norm_api(str(site.get("api") or "")) or str(site.get("api") or "")
    return "s" + hashlib.md5(base.encode("utf-8")).hexdigest()[:8]


def clean_ads(ads) -> list:
    """广告域名黑名单去重，并剔除明显是正常前端公共库的域名（避免误伤播放页）。"""
    if not isinstance(ads, list):
        return []
    whitelist = {"cdn.bootcss.com", "cdnjs.cloudflare.com", "unpkg.com", "cdn.jsdelivr.net"}
    out, seen = [], set()
    for a in ads:
        if not isinstance(a, str):
            continue
        a = a.strip().lower()
        if not a or a in seen or a in whitelist:
            continue
        seen.add(a)
        out.append(a)
    return out


def crawl_pool(pool_urls: list, timeout: float):
    """BFS 爬取多仓配置（深度 2），返回 (configs列表, 失败列表, 直播源引用列表)。"""
    configs, failed, live_refs = [], [], []
    jar_candidates = set()
    visited = set()
    queue = [(u, 0, "pool") for u in pool_urls]
    while queue:
        url, depth, tag = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        text = fetch(url, timeout=timeout)
        if text is None:
            failed.append(f"{tag}: {url[:60]}")
            continue
        cfg = parse_config_text(text)
        if cfg is None:
            # 不是 JSON → 尝试识别为直播列表文件 (m3u / txt genre 格式)
            head = text[:500].lstrip()
            if head.startswith("#EXTM3U") or "#genre#" in head:
                live_refs.append({"name": f"直播-{tag}", "type": 0, "url": url})
            else:
                failed.append(f"解析失败: {url[:60]}")
            continue
        # 多仓格式1: {"urls": ["...", ...]}
        if isinstance(cfg.get("urls"), list) and depth < 2:
            for sub in cfg["urls"]:
                if isinstance(sub, str) and sub.startswith("http"):
                    queue.append((sub, depth + 1, tag + "/sub"))
            continue
        # 多仓格式2: {"storeHouse": [{"sourceUrl": ...}]}
        if isinstance(cfg.get("storeHouse"), list) and depth < 2:
            for item in cfg["storeHouse"]:
                su = (item or {}).get("sourceUrl", "")
                if isinstance(su, str) and su.startswith("http"):
                    queue.append((su, depth + 1, tag + "/house"))
            continue
        if cfg.get("sites"):
            configs.append((tag, url, cfg))
            # 收集该仓自带的 spider jar 候选（type 3 源靠它才能工作）
            sp = parse_spider(cfg.get("spider"))
            if sp:
                jar_candidates.add(sp)
        # JSON 配置里的直播引用（{"name":..., "type":0, "url":"...txt"}）
        for lv in cfg.get("lives", []):
            if isinstance(lv, dict) and lv.get("type") == 0 and isinstance(lv.get("url"), str) \
                    and lv["url"].startswith("http"):
                live_refs.append({"name": lv.get("name") or f"直播-{tag}", "type": 0, "url": lv["url"]})
    return configs, failed, live_refs, jar_candidates


def extract_sites(configs: list) -> list:
    """提取并标准化所有点播源（按 api 去重）。"""
    sites, seen = [], set()
    n = 0
    for tag, src_url, cfg in configs:
        for s in cfg.get("sites", []):
            if not isinstance(s, dict):
                continue
            api = (s.get("api") or "").strip()
            if not api.startswith("http") and not api.startswith("csp_"):
                continue
            key = (s.get("type", 0), norm_api(api))
            if key in seen:
                continue
            seen.add(key)
            n += 1
            site = dict(s)
            site["key"] = site_key(site)
            site["_src"] = tag
            sp = parse_spider(cfg.get("spider"))
            if sp:
                site["_spider"] = sp  # 记录该源所属仓的 jar，便于配对复活 type 3
            sites.append(site)
    return sites


def extract_lives(configs: list) -> list:
    """提取直播频道：[{name, urls:[...]}]，按地址去重。"""
    channels, seen = [], set()
    for tag, src_url, cfg in configs:
        for group in cfg.get("lives", []):
            if not isinstance(group, dict):
                continue
            # 两种字段名都兼容：老配置用 channels，build_live.py 输出用 channel
            for ch in (group.get("channel") or group.get("channels") or []):
                if not isinstance(ch, dict):
                    continue
                urls = ch.get("urls") or []
                u0 = urls[0] if urls else None
                if not u0 or u0 in seen:
                    continue
                seen.add(u0)
                channels.append({"name": ch.get("name", "未命名"), "urls": urls})
    return channels


# ---------- 检测 ----------
def clean_name(name: str) -> str:
    """清理源名称：去 emoji、装饰符号、来源后缀，便于去重和展示。"""
    import re
    n = name or ""
    n = re.sub(r"[┃│|｜/\\◆★☆⚡→←➕·•●○]+", " ", n)
    n = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE00-\uFE0F\u2B00-\u2BFF]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n or (name or "").strip()


def play_check(list_url: str, t: int, timeout: float, headers: dict = None):
    """真实可播性测试：优先从列表响应直接找播放链接（多数采集站列表自带），
    找不到再取首部影片详情验证。True=可播 / False=不可播 / None=无法判断。"""
    import re as _re
    try:
        body = fetch(list_url, timeout=timeout, max_bytes=3_000_000, headers=headers)
        if not body:
            return None
        if t == 1:  # JSON
            data = json.loads(body)
            lst = data.get("list") or data.get("vod_list") or []
            if not lst:
                return None
            # 1) 列表自带播放链接
            p = str(lst[0].get("vod_play_url") or "")
            if p and "$" in p:
                return True
            # 2) 请求首部影片详情
            vid = lst[0].get("vod_id") or lst[0].get("id")
            if vid is None:
                return None
            base = list_url.split("?")[0]
            sep = "&" if "?" in list_url else "?"
            for q in (f"ac=detail&ids={vid}", f"ac=videolist&ids={vid}"):
                d = fetch(base + "?" + q, timeout=timeout, max_bytes=3_000_000, headers=headers)
                if not d:
                    continue
                dl = json.loads(d).get("list") or []
                if dl:
                    return bool(str(dl[0].get("vod_play_url") or "") and "$" in dl[0]["vod_play_url"])
            return None
        else:  # XML (type 0)
            # 1) 列表自带播放节点
            if "<dd" in body and "$" in body:
                return True
            # 2) 请求首部影片详情
            m = _re.search(r"<id>([^<]+)</id>", body)
            if not m:
                return None
            base = list_url.split("?")[0]
            sep = "&" if "?" in list_url else "?"
            d = fetch(base + "?" + f"ac=videolist&ids={m.group(1)}", timeout=timeout,
                      max_bytes=3_000_000, headers=headers)
            if not d:
                return None
            return ("<dd" in d and "$" in d)
    except Exception:
        return None


def get_first_play_url(list_url: str, t: int, timeout: float, headers: dict = None):
    """取第一个真实播放直链（m3u8/mp4）。失败返回 None。"""
    import re as _re
    try:
        body = fetch(list_url, timeout=timeout, max_bytes=3_000_000, headers=headers)
        if not body:
            return None
        pu = None
        if t == 1:
            data = json.loads(body)
            lst = data.get("list") or data.get("vod_list") or []
            if lst:
                pu = str(lst[0].get("vod_play_url") or "")
            if not (pu and "$" in pu):
                vid = lst[0].get("vod_id") if lst else None
                if vid is None:
                    return None
                base = list_url.split("?")[0]
                sep = "&" if "?" in list_url else "?"
                d = fetch(base + "?" + f"ac=detail&ids={vid}", timeout=timeout,
                          max_bytes=3_000_000, headers=headers)
                if d:
                    dl = json.loads(d).get("list") or []
                    if dl:
                        pu = str(dl[0].get("vod_play_url") or "")
        else:  # XML
            m = _re.search(r"<dd[^>]*>([^<]+)</dd>", body) or _re.search(r"<play[^>]*>([^<]+)</play>", body)
            pu = m.group(1) if m else ""
            if not (pu and "$" in pu):
                return None
        if not pu or "$" not in pu:
            return None
        first = pu.split("#")[0]
        url = first.split("$", 1)[-1].strip()
        low = url.lower()
        if url.startswith("http") and not any(
                low.startswith(p) for p in ("magnet:", "ed2k:", "thunder:", "115://", "p3p://")):
            return url
        return None
    except Exception:
        return None


def cdn_speed_check(list_url: str, t: int, timeout: float, headers: dict = None):
    """真实播放测速：下载 m3u8 索引 + 第一个视频分片（限512KB），
    返回 MB/s；无法测出返回 None。"""
    try:
        play = get_first_play_url(list_url, t, timeout, headers)
        if not play:
            return None
        segurl = play
        if ".m3u8" in play.lower():
            m3u8 = fetch(play, timeout=timeout, max_bytes=200_000, headers=headers)
            if not m3u8:
                return None
            seg = next((l.strip() for l in m3u8.splitlines()
                        if l.strip().endswith(".ts") and not l.startswith("#")), None)
            if not seg:
                # 索引里可能还有一层 m3u8（master → media playlist）
                sub = next((l.strip() for l in m3u8.splitlines()
                            if l.strip().endswith(".m3u8") and not l.startswith("#")), None)
                if sub:
                    base = play.rsplit("/", 1)[0]
                    if not sub.startswith("http"):
                        sub = urllib.parse.urljoin(play, sub)
                    m3u8 = fetch(sub, timeout=timeout, max_bytes=200_000, headers=headers) or ""
                    seg = next((l.strip() for l in m3u8.splitlines()
                                if l.strip().endswith(".ts") and not l.startswith("#")), None)
                    segurl = sub  # 相对分片路径要基于二级索引解析
            if not seg:
                return None
            segurl = urllib.parse.urljoin(segurl, seg)
        t0 = time.perf_counter()
        body = fetch(segurl, timeout=timeout + 5, max_bytes=512_000, headers=headers)
        if not body:
            return None
        dt = time.perf_counter() - t0
        if dt <= 0.05:
            dt = 0.05
        return round(len(body.encode("utf-8", errors="ignore")) / dt / 1024 / 1024, 2)
    except Exception:
        return None


def test_site(site: dict, timeout: float) -> dict:
    """存活 + 延迟 + 质量评分(片单数量) + 真实可播性。
    type 3 只测 ext 规则文件可达性。"""
    t = site.get("type", 0)
    api = (site.get("api") or "").strip()
    headers = site.get("header") if isinstance(site.get("header"), dict) else None
    name = site.get("name") or site.get("key", "?")
    r = {"site": site, "ok": False, "ms": None, "count": 0, "play": None,
         "cdn_speed": None, "note": ""}
    if t == 3:
        ext = site.get("ext")
        if isinstance(ext, dict):  # 内联规则对象，无需外部文件
            r.update(ok=True, ms=0, note="内联规则")
            return r
        if api.startswith("csp_"):  # 内核内置 CSP 爬虫（云盘搜索/SeedHub 等），无 ext 也有效
            r.update(ok=True, ms=0, note="内置 CSP")
            return r
        url = (ext if isinstance(ext, str) else "").strip()
        if not url or not url.startswith("http"):
            r["note"] = "type3 无 ext"
            return r
        t0 = time.perf_counter()
        body = fetch(url, timeout=timeout, ua=UA_BROWSER)
        if body and len(body) > 20:
            r.update(ok=True, ms=int((time.perf_counter() - t0) * 1000),
                     note="规则文件可达")
        else:
            r["note"] = "ext 不可达"
        return r
    if not api.startswith("http"):
        r["note"] = "api 非法"
        return r
    sep = "&" if "?" in api else "?"
    if t == 1:
        url = api + sep + "ac=list"
    else:
        url = api if "ac=" in api else api + sep + "ac=videolist"
    t0 = time.perf_counter()
    body = fetch(url, timeout=timeout, ua=UA_OKHTTP, max_bytes=500_000, headers=headers)
    if body is None:
        r["note"] = "超时/连接失败"
        return r
    r["ms"] = int((time.perf_counter() - t0) * 1000)
    txt = body.strip()
    low = txt[:3000].lower()
    if "<video" in low or "<list" in low:
        r.update(ok=True, count=txt.count("<video"), note="XML")
    else:
        try:
            data = json.loads(txt)
            lst = data.get("list") if isinstance(data, dict) else data
            if isinstance(data, dict) and data.get("vod_list"):
                lst = data["vod_list"]
            if isinstance(lst, list) and lst:
                r.update(ok=True, count=len(lst), note="JSON")
            else:
                r["note"] = "JSON 无片单"
        except Exception:
            r["note"] = "内容非 XML/JSON"
    if r["ok"] and t in (0, 1):
        r["play"] = play_check(url, t, timeout, headers)  # 真实可播性
        if r["play"] is False:
            r["note"] += " + 播放链接无效"
            r["ok"] = False  # 有片单但播不了 = 判定失效
    if r["ok"] and r["play"] is True:
        r["cdn_speed"] = cdn_speed_check(url, t, timeout, headers)  # 真实视频CDN下载测速
    return r


def test_channel(ch: dict, timeout: float) -> dict:
    url = (ch.get("urls") or [""])[0]
    ok, ms = False, None
    if url:
        t0 = time.perf_counter()
        body = fetch(url, timeout=timeout, ua=UA_BROWSER, max_bytes=60_000)
        ms = int((time.perf_counter() - t0) * 1000)
        if body and ("#EXTM3U" in body[:200] or "<" in body[:200]):
            ok = True
    return {"ch": ch, "ok": ok, "ms": ms}


# ---------- 生成 ----------
def build_latest(seed_cfg: dict, selected_sites: list, alive_channels: list, live_refs: list, spider_url: str) -> dict:
    sites = []
    used_names = set()
    pool_keys = set()
    for s in selected_sites:
        cn = clean_name(s.get("name", ""))
        if cn in used_names:  # 清理后同名 → 只留排名靠前的
            continue
        used_names.add(cn)
        s = dict(s)
        s["name"] = cn
        t = s.get("type", 0)
        s.pop("_play", None)
        s.pop("_src", None)
        s.pop("_spider", None)
        # 稳定 key：同一 API 永远同名，重新生成配置不会让收藏/播放进度失效
        s["key"] = site_key(s)
        pool_keys.add(s["key"])
        s.setdefault("timeout", 15)  # 显式超时，避免 FongMi 默认值过短误杀慢源
        s.setdefault("searchable", 1)
        # 采集站(is cms)天然支持分类筛选；type3 规则源没写 filterable 就置 0，避免点进去空白
        if "filterable" not in s:
            s["filterable"] = 1 if t in (0, 1) else 0
        # 先全部关掉，最后按实测延迟只给最快的十几个开 quickSearch（见下方收尾）
        s["quickSearch"] = 0
        sites.append(s)
    # ---------- 固定精选：网盘/磁力爬虫源 ----------
    # 【自托管 + 换成能下载的链路】jar 都在本仓库 libs/，不依赖任何第三方图床。
    # 2026-11-27 上海家宽实测：raw.githubusercontent 直连 2.7MB 的 jar，142 秒后被重置失败；
    # 同一文件走 gh-proxy 17 秒可下完；jsDelivr 对 .jar 直接 403，但对 .png 秒开。
    # 所以把同一份包再存一份 .png 后缀（客户端只看字节和 md5，不看后缀），走 jsDelivr。
    # 时间多出来的黑屏等待就是这么来的：魔屏要等 jar 下完才能进站点。
    RAW = "https://gh-proxy.com/https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/"
    CDN = "https://cdn.jsdelivr.net/gh/WJS1987/tvbox-auto@main/libs/"
    PAN_JAR = CDN + "pan_spider.png"
    # 饭太硬同款网盘爬虫包（与官方 spider 字节级一致：ftyguard_v7/v8.so +
    # ftyshinidie.guard 加密代码 + classes.dex 壳，含全套 csp_*Guard 网盘类）。
    # 私有加壳包、无源码，故只能整体镜像；按站点单独挂，不进全局 spider
    #（全局 jar 决定其他 type3 站点兼容性，换掉会连累它们）。
    FTY_JAR = (CDN + "fty_spider.png"
               + ";md5;5302302a73a9e27cfaa65f3873b727ab")
    # 真实机制（2026-11-27 用户纠正 + 全网核实）：不是手动填 Cookie。
    # 点"我的云盘"站点 → 客户端弹二维码 → 手机网盘APP扫码登录 →
    # token 由客户端自动写进本地 tvfan/Cloud-drive.txt；
    # 下列共用该账号文件的站点随后才能把网盘分享解析成可播直链。
    CD = {"Cloud-drive": "tvfan/Cloud-drive.txt"}
    # 爬来的同名站点没挂 jar（全局 jar 不含这些 Guard 类，是摆设），先剔除避免重复
    FTY_APIS = {"csp_MyDriveGuard", "csp_WoGGGuard", "csp_YpanSoGuard",
                "csp_BpanSoGuard", "csp_KkSsGuard", "csp_UuSsGuard"}
    sites = [s for s in sites if s.get("api") not in FTY_APIS]
    sites.extend([
        {"key": "pan_search", "name": "云盘搜索", "type": 3, "api": "csp_PanSearch",
         "jar": PAN_JAR, "searchable": 1, "quickSearch": 1, "filterable": 0, "timeout": 15},
        {"key": "seedhub", "name": "SeedHub磁力", "type": 3, "api": "csp_SeedHub",
         "jar": PAN_JAR, "searchable": 1, "quickSearch": 1, "filterable": 1, "timeout": 15},
        {"key": "new6v", "name": "New6V磁力", "type": 3, "api": "csp_New6v", "jar": PAN_JAR,
         "ext": "https://www.xb6v.com", "searchable": 1, "quickSearch": 1, "filterable": 1, "timeout": 15},
        # ① 登录入口：点进去 →「登入自己网盘」→ 弹二维码 → 手机APP扫码
        {"key": "mydrive", "name": "我的云盘 扫码登录", "type": 3, "api": "csp_MyDriveGuard",
         "jar": FTY_JAR, "changeable": 0, "indexs": 0, "searchable": 1,
         "style": {"type": "oval"}, "ext": CD, "timeout": 20},
        # ② 扫码后自动能播的免费网盘片源（照搬饭太硬官方配置，共用 Cloud-drive.txt）
        {"key": "wogg", "name": "玩偶哥哥4K", "type": 3, "api": "csp_WoGGGuard",
         "jar": FTY_JAR, "changeable": 0, "searchable": 1, "quickSearch": 1,
         "ext": CD, "timeout": 30},
        {"key": "ypanso", "name": "盘她夸父", "type": 3, "api": "csp_YpanSoGuard",
         "jar": FTY_JAR, "changeable": 0, "searchable": 1, "quickSearch": 1,
         "ext": CD, "timeout": 20},
        {"key": "bpanso", "name": "盘他嘟嘟", "type": 3, "api": "csp_BpanSoGuard",
         "jar": FTY_JAR, "changeable": 0, "searchable": 1, "quickSearch": 1,
         "ext": CD, "timeout": 20},
        {"key": "kkss", "name": "抠抠搜搜", "type": 3, "api": "csp_KkSsGuard",
         "jar": FTY_JAR, "changeable": 0, "searchable": 1, "quickSearch": 1,
         "ext": CD, "timeout": 20},
        {"key": "uuss", "name": "优汐搜搜", "type": 3, "api": "csp_UuSsGuard",
         "jar": FTY_JAR, "changeable": 0, "searchable": 1, "quickSearch": 1,
         "ext": CD, "timeout": 20},
    ])
    # 收尾统一：补默认值 + 按 key 去重
    # （同一后台被写成不同 URL 时，前面按名字去重拦不住，这里按稳定 key 再兜一层，
    #   否则配置里会出现两个 key 相同的站点，客户端会当成同一个源重复加载）
    fixed, seen_keys = [], set()
    for s in sites:
        k = s.get("key") or site_key(s)
        if k in seen_keys:
            continue
        seen_keys.add(k)
        s["key"] = k
        s.setdefault("searchable", 1)
        s.setdefault("quickSearch", 0)
        s.setdefault("filterable", 0)
        s.setdefault("timeout", 15)
        fixed.append(s)
    sites = fixed
    # quickSearch 名额有限：一次全库搜索时，客户端会并发请求所有开着 quickSearch 的站点。
    # 实测魔屏（1GB 内存盒子）并发 20 个搜索请求会长时间空白，
    # 因此只让"纯 CMS 采集站"里最快的 6 个参与全库搜索：
    # 网盘/磁力/直播/需要 jar 的站点一律不抢名额（它们仍可单独点进去搜）。
    QS_LIMIT = 6
    for s in sites:
        if s.get("jar") or s.get("ext") or s.get("type") != 1:
            s["quickSearch"] = 0
    ranked = sorted((s for s in sites
                     if s.get("type") == 1 and not s.get("jar") and not s.get("ext")),
                    key=lambda x: (x.get("_ms") is None, x.get("_ms") or 99999))
    for s in ranked[:QS_LIMIT]:
        s["quickSearch"] = 1
    for s in sites:
        s.pop("_ms", None)
    lives = list(live_refs)
    if alive_channels:
        lives.append({"group": "精选直播", "channels": alive_channels})
    # 全局 spider 也换成本仓库镜像：libs/spider.png 与原第三方图床包字节完全一致（md5 相同），
    # 但图床一旦清理或开防盗链，所有 type3 站点会一起报废。
    own_spider = CDN + "spider.png"
    out = {
        "spider": own_spider,
        "sites": sites,
        "lives": lives,
        "parses": [{"name": "Json轮询", "type": 2, "url": "Sequence"}],
        "flags": ["youku", "qq", "iqiyi", "qiyi", "letv", "sohu", "tudou", "pptv", "mgtv", "wasu", "bilibili"],
        "ads": clean_ads(seed_cfg.get("ads", [])),
    }
    return out


def grade(ms):
    if ms is None:
        return "?"
    if ms < 800:
        return "极快"
    if ms < 2000:
        return "快"
    if ms < 5000:
        return "一般"
    return "慢"


# 家庭网络实测稳定失效的源（GitHub 海外机房测出来是活的，但国内连不上，点进去一直转圈）。
# 2026-11-27 家里网络两轮探测均为 0 成功。按完整 API 地址匹配，避免误杀同域名的正常源。
# 源若日后恢复，从本列表删除对应行即可重新入选。
HOME_DEAD_APIS = [
    "https://ikunzyapi.com/api.php/provide/vod",
    "https://apilj.com/api.php/provide/vod/at/json/",
    "https://shayuapi.com/api.php/provide/vod/",
    "https://lbapi9.com/api.php/provide/vod/at/xml",
    "https://jszyapi.com/api.php/provide/vod/",
    "https://suoniapi.com/api.php/provide/vod/",
    "https://suoniapi.com/api.php/provide/vod/?ac=list",
    "http://api.xinlangapi.com/xinlangapi.php/provide/vod/",
    "http://caiji.dyttzyapi.com/api.php/provide/vod/from/dyttm3u8/at/m3u8/",
    "http://caiji.dyttzyapi.com/api.php/provide/vod",
]


def is_home_dead(site: dict) -> bool:
    """站点 API 是否在家庭网络实测失效名单里（去尾部斜杠后精确匹配）。"""
    api = str(site.get("api") or "").strip().rstrip("/")
    return any(api == str(d).strip().rstrip("/") for d in HOME_DEAD_APIS)


def norm_name_py(s: str) -> str:
    """对齐 JS normName: toLowerCase + 去除空白/标点/符号。"""
    import unicodedata
    s = str(s or "").lower()
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if ch.isspace() or cat.startswith(("P", "S")):
            continue
        out.append(ch)
    return "".join(out)


def js_hash_id(s: str) -> str:
    """对齐 JS hashId: uint32 h*131+charCodeAt, base36, 前缀 m。charCodeAt 按 UTF-16 码元。"""
    units = s.encode("utf-16-le")
    h = 0
    for i in range(0, len(units), 2):
        code = units[i] | (units[i + 1] << 8)
        h = (h * 131 + code) & 0xFFFFFFFF
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if h == 0:
        b36 = "0"
    else:
        b36 = ""
        while h:
            b36 = digits[h % 36] + b36
            h //= 36
    return "m" + b36


def write_report(path, alive_sites, dead_sites, alive_ch, dead_ch, crawl_failed, crawl_stats, selected_sites):
    alive_sites.sort(key=lambda r: (r["play"] is not True, -min(r["count"], 50), -(r.get("cdn_speed") or 0), r["ms"] or 99999))
    selected_keys = {id(s) for s in selected_sites}
    L = ["# 源自动采集检测报告", "",
         "> 注意：本报告延迟由 GitHub 海外机房测得，**不代表国内家庭网络的真实速度**。",
         "> 国内实测请在家里电脑运行 `python device_test.py`，生成 `device_report.md`。", "",
         f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
         f"- 爬取聚合配置：{crawl_stats}",
         f"- 点播源：采集去重 {len(alive_sites) + len(dead_sites)} → 存活 {len(alive_sites)} / 失效 {len(dead_sites)}"
         f" → **精选保留 {len(selected_sites)} 个**（可播性+片单量+速度综合排名）",
         f"- 直播频道：去重 {len(alive_ch) + len(dead_ch)} → 存活 {len(alive_ch)}"
         "（海外机房探测，国内 IPTV 连不上属正常，不代表失效；直播以设备实测为准）",
         f"- 配置站失效：{len(crawl_failed)} 个", "",
         "## 点播源（综合排名，✔=已确认可播）", "",
         "| 精选 | 可播 | 速度 | 延迟 | 片单数 | 名称 |", "|---|---|---|---|---|---|"]
    for r in alive_sites[:80]:
        star = "⭐" if id(r["site"]) in selected_keys else ""
        play = {True: "✔", False: "✘"}.get(r.get("play"), "?")
        L.append(f"| {star} | {play} | {grade(r['ms'])} | {r['ms']}ms | {r['count']} | {r['site'].get('name','')} |")
    L += ["", "## 失效点播源（前40）", "", "| 名称 | 原因 |", "|---|---|"]
    for r in dead_sites[:40]:
        L.append(f"| {r['site'].get('name','')} | {r['note']} |")
    Path(path).write_text("\n".join(L), encoding="utf-8")


# ---------- 旧模式：检测单个文件 ----------
def legacy_mode(args):
    p = Path(args.legacy)
    raw = p.read_text(encoding="utf-8-sig")
    lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith("#")]
    cfg = json.loads("\n".join(lines))
    sites = cfg.get("sites", [])
    seen, deduped = set(), []
    for s in sites:
        key = (s.get("type", 0), norm_api(str(s.get("api") or "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    print(f"[解析] {len(sites)} → 去重 {len(deduped)}")
    if not args.check:
        return
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(test_site, s, args.timeout) for s in deduped]
        for i, f in enumerate(as_completed(futs), 1):
            results.append(f.result())
            if i % 20 == 0 or i == len(futs):
                print(f"[进度] {i}/{len(futs)}")
    alive = [r for r in results if r["ok"]]
    alive.sort(key=lambda r: (r["count"] == 0, r["ms"] or 99999))
    out_cfg = dict(cfg)
    out_cfg["sites"] = [r["site"] for r in alive]
    Path(str(p.with_name(p.stem + "_clean.json"))).write_text(
        json.dumps(out_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[结果] 存活 {len(alive)} / 失效 {len(results) - len(alive)}")


# ---------- 全自动模式 ----------
def auto_mode(args):
    pool = json.loads(Path(args.pool).read_text(encoding="utf-8"))
    urls = [u for u in (pool if isinstance(pool, list) else pool.get("urls", []))
            if isinstance(u, str) and u.startswith("http")]
    print(f"[爬取] 聚合配置站 {len(urls)} 个 ...")
    configs, failed, live_refs, jar_candidates = crawl_pool(urls, args.timeout)
    crawl_stats = f"成功 {len(configs)} / 失败 {len(failed)}"
    print(f"[爬取] {crawl_stats}")

    sites = extract_sites(configs)
    channels = extract_lives(configs)
    # 用户的种子配置也参与（视为一个来源）
    if args.seed and Path(args.seed).exists():
        try:
            seed = parse_config_text(Path(args.seed).read_text(encoding="utf-8-sig"))
            if seed:
                sites += extract_sites([("seed", "local", seed)])
                channels += extract_lives([("seed", "local", seed)])
        except Exception:
            pass

    # 兜底去重
    seen, deduped = set(), []
    for s in sites:
        key = (s.get("type", 0), norm_api(str(s.get("api") or "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    seen_u, ch_deduped = set(), []
    for c in channels:
        u0 = (c.get("urls") or [""])[0]
        if u0 and u0 not in seen_u:
            seen_u.add(u0)
            ch_deduped.append(c)
    # 直播源引用（txt/m3u 文件整体引用）按 url 去重
    seen_ref, ref_deduped = set(), []
    for ref in live_refs:
        if ref["url"] not in seen_ref:
            seen_ref.add(ref["url"])
            ref_deduped.append(ref)

    print(f"[采集] 点播源 {len(deduped)} 个，直播频道 {len(ch_deduped)} 个，直播源引用 {len(ref_deduped)} 个（已去重）")
    if args.crawl_only:
        print("[结束] crawl-only 模式，不联网测源。")
        return

    if args.limit:
        deduped = deduped[:args.limit]
        ch_deduped = ch_deduped[:min(args.limit, 50)]
        print(f"[限制] 本次仅测前 {args.limit} 个")

    print(f"[检测] 点播源并发测试（{args.workers} 线程）...")
    site_results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(test_site, s, args.timeout) for s in deduped]
        for i, f in enumerate(as_completed(futs), 1):
            site_results.append(f.result())
            if i % 25 == 0 or i == len(futs):
                print(f"  {i}/{len(futs)}")
    alive_sites = [r for r in site_results if r["ok"]]
    dead_sites = [r for r in site_results if not r["ok"]]

    # ===== 点播源分拣：可播性 → 片单量 → CDN 速度 → 响应延迟，只留前 top 个 =====
    # 原先还有一道"国内节点可达性"过滤（EdgeOne 边缘函数回传国内探测），该链路随
    # 超级源项目被删而失效，2026-11-28 起移除；国内打不开的源由 HOME_DEAD_APIS 黑名单兜底。
    good = [r for r in alive_sites if r["play"] is not False]  # play False 已在检测时标记失效
    good.sort(key=lambda r: (r["play"] is not True, -min(r["count"], 50), -(r.get("cdn_speed") or 0), r["ms"] or 99999))

    selected_sites = []
    seen_cn = set()
    # 有 jar 候选时给 type 3 高级源预留 3 个名额，避免普通源占满后高级源无位可进
    reserve = min(3, len(jar_candidates)) if jar_candidates else 0
    cap = max(1, args.top - reserve)
    for r in good:
        if r["site"].get("type", 0) != 0 and r["site"].get("type", 0) != 1:
            continue  # type 3 后面按 jar 配对单独处理
        if is_home_dead(r["site"]):
            continue  # 家庭网络实测打不开的源，剔除（否则点进去一直转圈）
        cn = clean_name(r["site"].get("name", ""))
        if cn in seen_cn:
            continue
        seen_cn.add(cn)
        # 把实测延迟带下去，build_latest 用它决定 quickSearch（快的才参与全库并发搜索）
        r["site"]["_ms"] = r["ms"]
        r["site"]["_play"] = r["play"]
        selected_sites.append(r["site"])
        if len(selected_sites) >= cap:
            break

    # ===== spider jar 测试：让 type 3 高质量源复活 =====
    spider_url = ""
    if jar_candidates:
        print(f"[jar] 测试 {len(jar_candidates)} 个爬虫包 ...")
        jar_results = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(fetch_bytes, j, args.timeout, None, 3_000_000): j
                    for j in jar_candidates}
            for f in as_completed(futs):
                body, _ms = f.result()
                # jar 是 ZIP 包（PK\x03\x04 头），按字节校验：decode 后字节数会失真，
                # 用 str 比较 "PK" 也可能把 HTML 错误页误判为可用包
                if body and len(body) >= 10_000 and body[:4] == b"PK\x03\x04":
                    jar_results.append(futs[f])
        if jar_results:
            # 选出能配对最多 type 3 存活源的 jar（并列时取源响应最快的）
            from collections import Counter as _C
            alive_t3 = [r for r in good if r["site"].get("type", 0) == 3]
            jar2cnt = _C(r["site"].get("_spider", "") for r in alive_t3
                         if r["site"].get("_spider") in jar_results)
            spider_url = max(jar_results, key=lambda j: jar2cnt.get(j, 0)) if jar2cnt \
                else jar_results[0]
            print(f"[jar] 可用 {len(jar_results)} 个，选用: {spider_url[:60]}"
                  f"（配对 {jar2cnt.get(spider_url, 0)} 个 type3 存活源）")
            # 加入与该 jar 配对的 type 3 源（如 玩偶/哔哩/Emby 等高级源）
            t3 = [r for r in good if r["site"].get("type", 0) == 3
                  and r["site"].get("_spider") == spider_url]
            t3.sort(key=lambda r: r["ms"] or 99999)
            for r in t3:
                if len(selected_sites) >= args.top:
                    break
                cn = clean_name(r["site"].get("name", ""))
                if cn in seen_cn or not cn:
                    continue
                seen_cn.add(cn)
                r["site"]["_ms"] = r["ms"]
                r["site"]["_play"] = r["play"]
                selected_sites.append(r["site"])
            print(f"[jar] 复活 type 3 源 {len(selected_sites)} 个总数中包含的 type3: "
                  f"{sum(1 for s in selected_sites if s.get('type', 0) == 3)}")
        else:
            print("[jar] 无可用爬虫包，type 3 源全部排除")

    # 种子配置里的 flags/ads 等参数沿用
    seed_cfg = {}
    if args.seed and Path(args.seed).exists():
        try:
            seed_cfg = parse_config_text(Path(args.seed).read_text(encoding="utf-8-sig")) or {}
        except Exception:
            pass
    latest = build_latest(seed_cfg, selected_sites, alive_ch, alive_refs, spider_url)
    # 全网汇总（超级源）已于 2026-11-28 正式归档：EdgeOne 项目被误删且新项目部署始终失败，
    # 该功能彻底下线，配置里不再注入任何 super 条目。代码与目录保留在 archive/super-source/ 备查。

    # 直播来源：种子配置里内联的频道组（config/tv.json），下面统一瘦身成 live.txt 的外链引用。
    # 配置瘦身（无论直播来自哪里都要做）：内联几千个频道会让 latest.json 从 ~50KB 涨到 200~400KB，
    # 魔屏这类盒子光是拉取+解析就要多花十几秒。这里把内联频道组换成对 live.txt 的 url 引用，
    # 频道只在用户打开"直播"页时才去拉，主配置只剩站点列表。
    LIVE_TXT_URL = ("https://gh-proxy.com/https://raw.githubusercontent.com/"
                    "WJS1987/tvbox-auto/main/live.txt")
    slim = []
    for lv in latest.get("lives", []):
        if not isinstance(lv, dict):
            continue
        if lv.get("url"):
            slim.append(lv)
        elif any(k in lv for k in ("groups", "channel", "channels")):
            slim.append({"name": lv.get("name") or "自建直播",
                         "url": LIVE_TXT_URL,
                         "ua": lv.get("ua") or "okHttp/Mod-1.5.0.0",
                         "timeout": 10})
        else:
            slim.append(lv)
    if slim:
        latest["lives"] = slim

    Path(args.out).write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(args.report, alive_sites, dead_sites, alive_ch, dead_ch, failed, crawl_stats, selected_sites)

    alive_sites.sort(key=lambda r: (r["play"] is not True, -min(r["count"], 50), -(r.get("cdn_speed") or 0), r["ms"] or 99999))
    fastest = alive_sites[0] if alive_sites else None
    print(f"\n[结果] 点播存活 {len(alive_sites)}"
          + (f"（最快: {fastest['ms']}ms {fastest['site'].get('name','')[:20]}）" if fastest else "")
          + f" / 精选保留 {len(selected_sites)} 个 / 直播源 {len(alive_refs)} 个")
    print(f"[输出] {args.out} + {args.report}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("legacy", nargs="?", help="旧模式：单个配置文件路径")
    ap.add_argument("--auto", action="store_true", help="全自动：爬取+检测+生成")
    ap.add_argument("--crawl-only", action="store_true", help="只爬取统计，不测源")
    ap.add_argument("--pool", default="sources_pool.json")
    ap.add_argument("--seed", default="config/tv.json", help="种子配置（参数沿用+兜底源）")
    ap.add_argument("--workers", type=int, default=30)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--limit", type=int, default=0, help="限制本次测试数量（调试用）")
    ap.add_argument("--top", type=int, default=40, help="最终精选保留多少个点播源")
    ap.add_argument("--top-lives", type=int, default=3, help="精选保留多少个直播源引用")
    ap.add_argument("--out", default="latest.json")
    ap.add_argument("--report", default="report.md")
    ap.add_argument("--check", action="store_true", help="旧模式参数：联网检测")
    args = ap.parse_args()

    if args.legacy:
        legacy_mode(args)
    elif args.auto:
        auto_mode(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    sys.exit(main())
