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


def fetch(url: str, timeout: float = 8, ua: str = UA_OKHTTP, max_bytes: int = 3_000_000):
    try:
        req = urllib.request.Request(ascii_url(url), headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.read(max_bytes).decode("utf-8", errors="replace")
    except Exception:
        return None


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
            key = (s.get("type", 0), api.strip().rstrip("/").lower())
            if key in seen:
                continue
            seen.add(key)
            n += 1
            site = dict(s)
            site["key"] = f"src{n}"
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
            for ch in group.get("channels", []):
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


def play_check(list_url: str, t: int, timeout: float):
    """真实可播性测试：优先从列表响应直接找播放链接（多数采集站列表自带），
    找不到再取首部影片详情验证。True=可播 / False=不可播 / None=无法判断。"""
    import re as _re
    try:
        body = fetch(list_url, timeout=timeout, max_bytes=3_000_000)
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
                d = fetch(base + "?" + q, timeout=timeout, max_bytes=3_000_000)
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
            d = fetch(base + "?" + f"ac=videolist&ids={m.group(1)}", timeout=timeout, max_bytes=3_000_000)
            if not d:
                return None
            return ("<dd" in d and "$" in d)
    except Exception:
        return None
    except Exception:
        return None


def get_first_play_url(list_url: str, t: int, timeout: float):
    """取第一个真实播放直链（m3u8/mp4）。失败返回 None。"""
    import re as _re
    try:
        body = fetch(list_url, timeout=timeout, max_bytes=3_000_000)
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
                d = fetch(base + "?" + f"ac=detail&ids={vid}", timeout=timeout, max_bytes=3_000_000)
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


def cdn_speed_check(list_url: str, t: int, timeout: float):
    """真实播放测速：下载 m3u8 索引 + 第一个视频分片（限512KB），
    返回 MB/s；无法测出返回 None。"""
    try:
        play = get_first_play_url(list_url, t, timeout)
        if not play:
            return None
        segurl = play
        if ".m3u8" in play.lower():
            m3u8 = fetch(play, timeout=timeout, max_bytes=200_000)
            if not m3u8:
                return None
            seg = next((l.strip() for l in m3u8.splitlines()
                        if l.strip().endswith(".ts") and not l.startswith("#")), None)
            if not seg:
                # 索引里可能还有一层 m3u8
                sub = next((l.strip() for l in m3u8.splitlines()
                            if l.strip().endswith(".m3u8") and not l.startswith("#")), None)
                if sub:
                    base = play.rsplit("/", 1)[0]
                    sub_url = sub if sub.startswith("http") else f"{base}/{sub}"
                    m3u8 = fetch(sub_url, timeout=timeout, max_bytes=200_000) or ""
                    seg = next((l.strip() for l in m3u8.splitlines()
                                if l.strip().endswith(".ts") and not l.startswith("#")), None)
                    segurl = sub_url if seg else segurl.rsplit("/", 1)[0] + "/" + (seg or "")
                if not seg:
                    return None
            base = segurl.rsplit("/", 1)[0]
            segurl = seg if seg.startswith("http") else f"{base}/{seg}"
        t0 = time.perf_counter()
        body = fetch(segurl, timeout=timeout + 5, max_bytes=512_000)
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
    name = site.get("name") or site.get("key", "?")
    r = {"site": site, "ok": False, "ms": None, "count": 0, "play": None,
         "cdn_speed": None, "note": ""}
    if t == 3:
        ext = site.get("ext")
        if isinstance(ext, dict):  # 内联规则对象，无需外部文件
            r.update(ok=True, ms=0, note="内联规则")
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
    body = fetch(url, timeout=timeout, ua=UA_OKHTTP, max_bytes=500_000)
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
        r["play"] = play_check(url, t, timeout)  # 真实可播性
        if r["play"] is False:
            r["note"] += " + 播放链接无效"
            r["ok"] = False  # 有片单但播不了 = 判定失效
    if r["ok"] and r["play"] is True:
        r["cdn_speed"] = cdn_speed_check(url, t, timeout)  # 真实视频CDN下载测速
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
    for s in selected_sites:
        cn = clean_name(s.get("name", ""))
        if cn in used_names:  # 清理后同名 → 只留排名靠前的
            continue
        used_names.add(cn)
        s = dict(s)
        s["name"] = cn
        s.pop("_src", None)
        s.pop("_spider", None)
        sites.append(s)
    # ---------- 固定精选：网盘/磁力爬虫源 ----------
    # OK影视(FongMi)原生迅雷引擎可直接播磁力/ed2k；云盘搜索聚合夸克/UC/阿里等公开分享
    # jar 来自 regtostudy/TVBox_warehouse（PizazzGY 原仓库已失效，此 fork 保持同步）
    PAN_JAR = ("https://gh-proxy.com/https://raw.githubusercontent.com/"
               "regtostudy/TVBox_warehouse/main/spider.jar")
    sites.extend([
        {"key": "pan_search", "name": "云盘搜索", "type": 3, "api": "csp_PanSearch",
         "jar": PAN_JAR, "searchable": 1, "quickSearch": 1, "filterable": 0},
        {"key": "seedhub", "name": "SeedHub磁力", "type": 3, "api": "csp_SeedHub",
         "jar": PAN_JAR, "searchable": 1, "quickSearch": 1, "filterable": 1},
        {"key": "new6v", "name": "New6V磁力", "type": 3, "api": "csp_New6v", "jar": PAN_JAR,
         "ext": "https://www.xb6v.com", "searchable": 1, "quickSearch": 1, "filterable": 1},
    ])
    lives = list(live_refs)
    if alive_channels:
        lives.append({"group": "精选直播", "channels": alive_channels})
    out = {
        "spider": spider_url or "",
        "sites": sites,
        "lives": lives,
        "parses": [{"name": "Json轮询", "type": 2, "url": "Sequence"}],
        "flags": ["youku", "qq", "iqiyi", "qiyi", "letv", "sohu", "tudou", "pptv", "mgtv", "wasu", "bilibili"],
        "ads": seed_cfg.get("ads", []),
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


# ---------- 超级源（全网汇总）置顶 ----------
SUPER_SOURCE_DOMAIN = os.environ.get("SUPER_SOURCE_DOMAIN", "tvbox-juxu-spegz3xv.edgeone.cool")
SUPER_SOURCE_PROJECT = os.environ.get("SUPER_SOURCE_PROJECT", "makers-lxb2wlvf61qz")
SUPER_SOURCE_NAME = "全网汇总"


def mint_super_token(api_token: str):
    """调用 EdgeOne API 为超级源域名签发新的预览访问 token（有效期约 3 小时）。"""
    body = json.dumps({
        "Action": "DescribePagesEncipherToken",
        "Region": "ap-guangzhou",
        "ProjectId": SUPER_SOURCE_PROJECT,
        "Text": SUPER_SOURCE_DOMAIN,
    }).encode()
    req = urllib.request.Request(
        "https://pages-api.cloud.tencent.com/v1", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_token})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode())
    resp = d.get("Data", {}).get("Response", d)
    tok = resp.get("Token")
    ts = resp.get("Timestamp")
    if not tok or not ts:
        raise RuntimeError(f"token 签发失败: {str(d)[:200]}")
    return tok, ts


def upsert_super_source(latest: dict, api_token: str) -> bool:
    """在 latest.json 的 sites 列表置顶插入"全网汇总"超级源（带新鲜 token）。"""
    try:
        tok, ts = mint_super_token(api_token)
    except Exception as e:
        print(f"[超级源] token 签发失败，本轮跳过: {e}")
        return False
    # 实测：URL 带 eo_token 会触发 EdgeOne 的 302 换 cookie 流程（FongMi 无 CookieJar 会卡死）；
    # 只有 Cookie 请求头 + 干净 URL 才直连 200。token 只放 header，不放 URL。
    url = f"https://{SUPER_SOURCE_DOMAIN}/api"
    site = {"key": "super", "name": SUPER_SOURCE_NAME, "type": 1, "api": url,
            # FongMi/TV 无 CookieJar，无法跟随 EdgeOne 的 302 换 cookie 流程，
            # 必须把 token 直接放进请求头，才能通过 EdgeOne 域名验证
            "header": {"Cookie": f"eo_token={tok}; eo_time={ts}"},
            # 官方规范字段：style 控制海报卡片样式（竖版海报3:4），timeout 延长汇总源响应时间
            "searchable": 1, "quickSearch": 1, "filterable": 1,
            "style": {"type": "rect", "ratio": 0.75}, "timeout": 20}
    latest["sites"] = [s for s in latest.get("sites", []) if s.get("key") != "super"]
    latest["sites"].insert(0, site)
    print(f"[超级源] 已置顶 {SUPER_SOURCE_NAME}（token 有效期约 3 小时）")
    return True


# ==================== 超级源目录生成（super_catalog.json） ====================
# 将"聚合片单"这一重活从边缘函数挪到流水线：Actions 深挖生成静态目录，
# 边缘函数只做秒级加载+内存检索，根治 KV 未批期间冷启动 9 秒重建的卡顿。

FIXED_CLASSES_PY = [
    {"type_id": "c_all", "type_name": "全部"},
    {"type_id": "c_movie", "type_name": "电影"},
    {"type_id": "c_tv", "type_name": "剧集"},
    {"type_id": "c_variety", "type_name": "综艺"},
    {"type_id": "c_anime", "type_name": "动漫"},
    {"type_id": "c_doc", "type_name": "纪录"},
]
CAT_RULES_PY = [
    (re.compile(r"电影|院线|动作|喜剧|爱情|科幻|恐怖|剧情|战争|犯罪|奇幻|冒险|灾难"), "c_movie"),
    (re.compile(r"剧|连续|国产|港台|韩|美|海外"), "c_tv"),
    (re.compile(r"综艺|娱乐|真人秀|脱口"), "c_variety"),
    (re.compile(r"动漫|动画|番剧|漫画"), "c_anime"),
    (re.compile(r"纪录|纪实|解说"), "c_doc"),
]
MAIN_CAT_PY = re.compile(r"电影|剧集|电视剧|综艺|动漫|动画|片|连续")

# 成人内容过滤：站点名/片名命中即整站/整条排除（91md/麻豆/扣扣传媒等曾混入排名）
ADULT_RE = re.compile(
    r"麻豆|扣扣|91md|91fans|91传媒|天美|果冻传媒|星空传媒|乌鸦传媒|蜜桃|精东|杏吧|"
    r"swag|女优|无码|有码|人妻|巨乳|淫|兽性|肉欲|情色|风月|mdx|"
    r"做爱|性爱|嫖|妓|菠萝传媒|兔子传媒|爱娘|翰丝传媒",
    re.IGNORECASE)


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


def classify_py(name: str, cat: str, remarks: str) -> str:
    """对齐 JS 分类规则：第N期优先判综艺，再按关键词顺序匹配。"""
    if re.search(r"第\d+期", remarks or ""):
        return "c_variety"
    for rx, cid in CAT_RULES_PY:
        if rx.search(cat or "") or rx.search(name or ""):
            return cid
    return "c_all"


def build_super_catalog(selected_sites: list, timeout: float = 8,
                        latest_pages: int = 10, cat_pages: int = 6,
                        max_cats: int = 10, max_items: int = 15000,
                        max_lines: int = 4) -> dict:
    """深挖各上游源生成汇总目录（去重+多源线路+分类），输出超级源静态目录。"""
    upstreams = [s for s in selected_sites if s.get("type", 0) in (0, 1) and s.get("api")]
    # 成人站整站排除
    clean_ups = [s for s in upstreams if not ADULT_RE.search(s.get("name") or "")]
    skipped = len(upstreams) - len(clean_ups)
    if skipped:
        print(f"[目录] 排除成人源 {skipped} 个")
    upstreams = clean_ups
    if not upstreams:
        return {"updated": "", "upstreams": [], "classes": FIXED_CLASSES_PY, "items": []}
    print(f"[目录] 深挖 {len(upstreams)} 个上游（最新{latest_pages}页 + 主流分类{cat_pages}页）...")

    def sep(u):
        return "&" if "?" in u else "?"

    def pjson(u):
        # 部分采集站对并发突发限流：失败退避重试2次（0.5s/1.5s）
        for delay in (0, 0.5, 1.5):
            if delay:
                time.sleep(delay)
            try:
                return json.loads(fetch(u, timeout=timeout, ua=UA_OKHTTP))
            except Exception:
                continue
        return None

    jobs = []  # (src_name, api, v)
    for site in upstreams:
        api = site["api"]
        base = f"{api}{sep(api)}"
        # 1) 分类表
        cls = pjson(api) or {}
        cat_list = [(str(c.get("type_id")), str(c.get("type_name") or ""))
                    for c in (cls.get("class") or []) if c.get("type_id") is not None]
        # 2) 探测 t 参数是否有效：无效的源（如淘片）改为往深翻页
        first = pjson(f"{base}ac=list&pg=1")
        t_ok = False
        if cat_list and first and first.get("list"):
            probe = pjson(f"{base}ac=list&t={cat_list[0][0]}&pg=1")
            fids = [v.get("vod_id") for v in first.get("list", [])[:10]]
            pids = [v.get("vod_id") for v in (probe or {}).get("list", [])[:10]]
            t_ok = bool(pids) and pids != fids  # 同页码下列表不同=分类过滤生效
        # 3) 生成抓取 URL
        urls = []
        if t_ok:
            main = [c for c in cat_list if MAIN_CAT_PY.search(c[1])]
            rest = [c for c in cat_list if c not in main]
            for _tid, _cname in (main + rest)[:max_cats]:
                urls += [f"{base}ac=list&t={_tid}&pg={p}" for p in range(1, cat_pages + 1)]
        else:
            # t 无效：深翻最新列表（片库按更新排序，翻得越深片越多）
            urls = [f"{base}ac=list&pg={p}" for p in range(1, latest_pages + 1)]
        for u in urls:
            jobs.append((site.get("name") or "", api, u))
        print(f"[目录] {site.get('name','')[:12]}: t参数{'有效' if t_ok else '无效(深翻页)'}，任务 {len(urls)} 个")

    print(f"[目录] 共 {len(jobs)} 个抓取任务，并发拉取 ...")
    fetched = {}  # api -> list of (src_name, data)
    def work(job):
        name, api, u = job
        d = pjson(u)
        return (name, api, d)
    # 注意：采集站对高并发限流（16并发全被拒），用低并发+重试保证成功率
    with ThreadPoolExecutor(max_workers=6) as ex:
        for name, api, d in ex.map(work, jobs):
            if d and isinstance(d.get("list"), list):
                fetched.setdefault(api, []).append((name, d))

    # 3) 去重合并（按源优先级，同片名保留多源作备用线路）
    seen = {}
    for site in upstreams:  # 按排名顺序处理，保证优先级
        api = site["api"]
        src_name = site.get("name") or ""
        for _name, d in fetched.get(api, []):
            for v in d["list"]:
                vn = v.get("vod_name")
                if not vn or v.get("vod_id") is None:
                    continue
                vn = str(vn)
                if ADULT_RE.search(vn):  # 成人片名整条排除
                    continue
                key = norm_name_py(vn)
                if not key:
                    continue
                entry = seen.get(key)
                if entry:
                    if len(entry["srcs"]) < max_lines:
                        entry["srcs"].append({"oid": str(v["vod_id"]), "api": api, "name": src_name})
                    continue
                seen[key] = {
                    "id": js_hash_id(key),
                    "name": vn,
                    "pic": str(v.get("vod_pic") or ""),
                    "remarks": str(v.get("vod_remarks") or ""),
                    "cat": classify_py(str(vn), str(v.get("type_name") or ""), str(v.get("vod_remarks") or "")),
                    "srcs": [{"oid": str(v["vod_id"]), "api": api, "name": src_name}],
                }

    items = list(seen.values())[:max_items]
    catalog = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "upstreams": [s.get("name") or "" for s in upstreams],
        "classes": FIXED_CLASSES_PY,
        "items": items,
    }
    print(f"[目录] 完成：{len(items)} 部去重片单（深挖前合并 {sum(len(e['srcs']) for e in seen.values())} 条记录）")
    return catalog


def write_report(path, alive_sites, dead_sites, alive_ch, dead_ch, crawl_failed, crawl_stats, selected_sites):
    alive_sites.sort(key=lambda r: (r["play"] is not True, -min(r["count"], 50), -(r.get("cdn_speed") or 0), r["ms"] or 99999))
    selected_keys = {id(s) for s in selected_sites}
    L = ["# 源自动采集检测报告", "",
         f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
         f"- 爬取聚合配置：{crawl_stats}",
         f"- 点播源：采集去重 {len(alive_sites) + len(dead_sites)} → 存活 {len(alive_sites)} / 失效 {len(dead_sites)}"
         f" → **精选保留 {len(selected_sites)} 个**（可播性+片单量+速度综合排名）",
         f"- 直播频道：去重 {len(alive_ch) + len(dead_ch)} → 存活 {len(alive_ch)}",
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
        key = (s.get("type", 0), (s.get("api") or "").strip().rstrip("/").lower())
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
        key = (s.get("type", 0), (s.get("api") or "").strip().rstrip("/").lower())
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

    print("[检测] 直播频道并发测试...")
    ch_results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(test_channel, c, args.timeout) for c in ch_deduped]
        for i, f in enumerate(as_completed(futs), 1):
            ch_results.append(f.result())
            if i % 50 == 0 or i == len(futs):
                print(f"  {i}/{len(futs)}")
    alive_ch = [r["ch"] for r in ch_results if r["ok"]]
    dead_ch = [r for r in ch_results if not r["ok"]]

    # 测试直播源引用（整个文件可达即算存活），按响应速度排序，只留最快的几个
    ref_results = []
    for ref in ref_deduped:
        t0 = time.perf_counter()
        body = fetch(ref["url"], timeout=args.timeout, ua=UA_BROWSER, max_bytes=20_000)
        ms = int((time.perf_counter() - t0) * 1000)
        if body and ("#EXTM3U" in body[:300] or "genre#" in body[:300] or "#EXTINF" in body[:300]):
            ref_results.append({"ref": ref, "ms": ms})
    ref_results.sort(key=lambda x: x["ms"])
    alive_refs = [r["ref"] for r in ref_results[:args.top_lives] if r["ms"] <= 5000]
    print(f"[检测] 直播源引用存活 {len(ref_results)}/{len(ref_deduped)}，精选最快 {len(alive_refs)} 个")

    # ===== 点播源分拣：可播性优先 → 片单量 → 速度，只留前 top 个 =====
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
        cn = clean_name(r["site"].get("name", ""))
        if cn in seen_cn:
            continue
        # 成人站整站排除（不进 latest.json 也不进超级源目录）
        if ADULT_RE.search(r["site"].get("name", "") or ""):
            continue
        seen_cn.add(cn)
        selected_sites.append(r["site"])
        if len(selected_sites) >= cap:
            break

    # ===== spider jar 测试：让 type 3 高质量源复活 =====
    spider_url = ""
    if jar_candidates:
        print(f"[jar] 测试 {len(jar_candidates)} 个爬虫包 ...")
        jar_results = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(fetch, j, args.timeout, UA_OKHTTP, 60_000): j for j in jar_candidates}
            for f in as_completed(futs):
                body = f.result()
                # jar 是 ZIP 包（PK 头），且至少 10KB——过滤伪装链接和错误页
                if body and len(body) >= 10_000 and body[:2] == "PK":
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
    super_token = os.environ.get("EDGEONE_TOKEN", "")
    if super_token:
        upsert_super_source(latest, super_token)

    # 生成超级源静态目录（边缘函数直接秒级加载，不再实时聚合）
    try:
        catalog = build_super_catalog(selected_sites, timeout=args.timeout)
        catalog_path = Path(args.out).with_name("super_catalog.json")
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
                                encoding="utf-8")
        print(f"[输出] {catalog_path.name}（{catalog_path.stat().st_size // 1024}KB）")
    except Exception as e:
        print(f"[目录] 生成失败（边缘函数会回退到实时聚合）: {e}")

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
    ap.add_argument("--top", type=int, default=8, help="最终精选保留多少个点播源")
    ap.add_argument("--top-lives", type=int, default=3, help="精选保留多少个直播源引用")
    ap.add_argument("--out", default="latest.json")
    ap.add_argument("--report", default="report.md")
    ap.add_argument("--check", action="store_true", help="旧模式参数：联网检测")
    ap.add_argument("--refresh-super", action="store_true",
                    help="只刷新超级源 token 并更新 latest.json（轻量，每小时跑）")
    args = ap.parse_args()

    if args.legacy:
        legacy_mode(args)
    elif args.auto:
        auto_mode(args)
    elif args.refresh_super:
        token = os.environ.get("EDGEONE_TOKEN", "")
        if not token:
            print("[超级源] 缺少 EDGEONE_TOKEN 环境变量")
            return 1
        p = Path(args.out)
        latest = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"sites": [], "lives": []}
        if upsert_super_source(latest, token):
            p.write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[输出] {args.out}")
            return 0
        return 1
    else:
        ap.print_help()


if __name__ == "__main__":
    sys.exit(main())
