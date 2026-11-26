#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox / OK影视 配置源批量检测工具
功能：
  1. 解析配置文件（容忍 # 注释行、BOM）
  2. 按 api 地址去重（同址多写只保留一个，优先保留 searchable=1 的）
  3. 并发检测每个源是否存活 + 响应速度（毫秒）
  4. 输出：检测报告（Markdown 表格） + 清理后的新配置文件（仅含存活源，按速度排序）

用法：
  python check_sources.py <配置文件>                 # 仅解析+去重，不发网络请求
  python check_sources.py <配置文件> --check         # 解析+去重+联网检测
  python check_sources.py <配置文件> --check --workers 30 --timeout 8
  可选参数：
    --out   输出清理后配置的路径（默认 <原名>_clean.json）
    --report 输出报告路径（默认 <原名>_report.md）
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

UA = "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE  # 这类采集站证书大多不规范，忽略校验


# ---------- 解析 ----------
def load_config(path: str) -> dict:
    raw = Path(path).read_text(encoding="utf-8-sig")
    # 去掉 JSON 不支持的 # 注释行（如 "# 直播"）
    lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith("#")]
    return json.loads("\n".join(lines))


def norm_url(u: str) -> str:
    return (u or "").strip().rstrip("/").lower()


def dedup_sites(sites: list) -> tuple[list, list]:
    """按 (type, api) 去重；同名同址冲突时优先保留 searchable=1 的条目。返回(去重后, 被去掉的)。"""
    seen = {}
    dropped = []
    for s in sites:
        key = (s.get("type", 0), norm_url(s.get("api", "")))
        if not key[1]:
            dropped.append((s, "api 为空"))
            continue
        if key in seen:
            old = seen[key]
            # 优先保留可搜索、信息更全的
            score = lambda x: (x.get("searchable", 0), bool(x.get("name")), bool(x.get("playUrl")))
            if score(s) > score(old):
                dropped.append((old, "重复(保留更优)"))
                seen[key] = s
            else:
                dropped.append((s, "重复"))
        else:
            seen[key] = s
    return list(seen.values()), dropped


# ---------- 检测 ----------
def build_test_url(site: dict) -> str | None:
    """构造探测用的列表请求地址。type 3 检测其 ext 规则文件。"""
    t = site.get("type", 0)
    api = (site.get("api") or "").strip()
    if not api:
        return None
    if t == 3:
        return (site.get("ext") or "").strip() or None
    sep = "&" if "?" in api else "?"
    if t == 1:  # JSON 接口
        return api + sep + "ac=list"
    # type 0 XML 接口
    return api if "ac=" in api else api + sep + "ac=videolist"


def validate(resp_text: str) -> tuple[bool, str]:
    """宽松校验返回内容是否像有效的视频数据。"""
    txt = resp_text.strip()
    if len(txt) < 50:
        return False, "响应过短"
    low = txt[:2000].lower()
    if "<video" in low or "<list" in low:
        return True, "XML格式"
    try:
        data = json.loads(txt)
        if isinstance(data, dict) and any(k in data for k in ("list", "vod_list", "data")):
            return True, "JSON格式"
        if isinstance(data, list) and data:
            return True, "JSON数组"
        return False, "JSON无数据"
    except Exception:
        return False, "非XML/JSON"


def check_site(site: dict, timeout: float, retries: int = 1) -> dict:
    url = build_test_url(site)
    name = site.get("name", "?")
    skey = (site.get("type", 0), norm_url(site.get("api", "")))
    if not url:
        return {"name": name, "key": skey, "ok": False, "ms": None, "note": "无可用探测地址"}
    last_err = ""
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
                body = resp.read(200_000).decode("utf-8", errors="replace")
                ms = int((time.perf_counter() - t0) * 1000)
                ok, note = validate(body)
                return {"name": name, "ok": ok, "ms": ms, "note": note if ok else f"内容无效({note})"}
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except Exception as e:
            last_err = type(e).__name__
        time.sleep(0.3)
    return {"name": name, "key": skey, "ok": False, "ms": None, "note": f"{last_err}(重试{retries + 1}次)"}


# ---------- 输出 ----------
def grade(ms):
    if ms is None:
        return "超时"
    if ms < 800:
        return "极快"
    if ms < 2000:
        return "快"
    if ms < 5000:
        return "一般"
    return "慢"


def write_report(path, alive, dead, spider_ok, spider_ms, dup_count):
    alive.sort(key=lambda x: (x["ms"] is None, x["ms"] or 0))
    L = ["# 源检测报告", "",
         f"- 检测时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
         f"- 去重剔除：{dup_count} 条",
         f"- 存活：{len(alive)} 个 / 失效：{len(dead)} 个",
         f"- 爬虫JAR：{'可用 ' + str(spider_ms) + 'ms' if spider_ok else '不可用（type 3 源将失效）'}",
         "", "## 存活源（按速度排序）", "",
         "| 速度 | 延迟 | 名称 | 说明 |", "|---|---|---|---|"]
    for r in alive:
        L.append(f"| {grade(r['ms'])} | {r['ms']}ms | {r['name']} | {r['note']} |")
    L += ["", "## 失效源", "", "| 名称 | 原因 |", "|---|---|"]
    for r in dead:
        L.append(f"| {r['name']} | {r['note']} |")
    Path(path).write_text("\n".join(L), encoding="utf-8")


def build_clean_config(cfg: dict, alive: list, deduped: list) -> dict:
    """仅保留存活源，按速度排序；其余段落原样保留。按 (type, api) 精确匹配，避免同名冲突。"""
    key2site = {(s.get("type", 0), norm_url(s.get("api", ""))): s for s in deduped}
    ordered = [key2site[r["key"]] for r in sorted(alive, key=lambda x: (x["ms"] is None, x["ms"] or 0))
               if r.get("key") in key2site]
    out = dict(cfg)
    out["sites"] = ordered
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config", help="配置文件路径")
    ap.add_argument("--check", action="store_true", help="执行联网检测（默认仅解析+去重）")
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    sites = cfg.get("sites", [])
    deduped, dropped = dedup_sites(sites)
    p = Path(args.config)

    print(f"[解析] 原始 {len(sites)} 个源 → 去重后 {len(deduped)} 个（剔除 {len(dropped)} 条冗余）")
    if not args.check:
        print("[提示] 未加 --check，仅做解析和去重，不发网络请求。")
        print("       各源名称预览（前20个）：")
        for s in deduped[:20]:
            print(f"  - {s.get('name')}  (type={s.get('type')})")
        return

    # 检测 spider jar
    spider = cfg.get("spider", "")
    spider_ok, spider_ms = False, None
    if spider:
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(spider, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=args.timeout, context=CTX) as r:
                if len(r.read(1000)) > 0:
                    spider_ok, spider_ms = True, int((time.perf_counter() - t0) * 1000)
        except Exception:
            pass
        print(f"[spider] {'可用 ' + str(spider_ms) + 'ms' if spider_ok else '不可用'}")

    # 并发检测
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(check_site, s, args.timeout): s for s in deduped}
        done = 0
        for fut in as_completed(futs):
            results.append(fut.result())
            done += 1
            if done % 10 == 0 or done == len(futs):
                print(f"[进度] {done}/{len(futs)}")

    alive = [r for r in results if r["ok"]]
    dead = [r for r in results if not r["ok"]]

    out_path = args.out or str(p.with_name(p.stem + "_clean.json"))
    rep_path = args.report or str(p.with_name(p.stem + "_report.md"))
    write_report(rep_path, alive, dead, spider_ok, spider_ms, len(dropped))
    Path(out_path).write_text(json.dumps(build_clean_config(cfg, alive, deduped), ensure_ascii=False, indent=2),
                              encoding="utf-8")

    alive.sort(key=lambda x: (x["ms"] is None, x["ms"] or 0))
    print(f"\n[结果] 存活 {len(alive)} / 失效 {len(dead)}")
    print("最快的前10个：")
    for r in alive[:10]:
        print(f"  {r['ms']}ms  {grade(r['ms'])}  {r['name']}")
    print(f"\n[输出] 报告: {rep_path}")
    print(f"[输出] 清理后配置: {out_path}")


if __name__ == "__main__":
    sys.exit(main())
