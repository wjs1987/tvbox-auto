#!/usr/bin/env python3
"""
家宽视角探测：通过 EdgeOne 边缘函数(mode=probe)从国内节点测试所有源，
生成"国内可达存活名单"供 check_sources.py 过滤。

用法：
    python edge_probe.py                       # 读 latest.json，探测全部点播源
    python edge_probe.py --in config/tv.json   # 指定输入配置
    python edge_probe.py --out home_alive.json # 指定输出
    python edge_probe.py --url https://xxx/api # 指定 EdgeOne 函数地址

需要 EdgeOne token 通过预览鉴权：
    - 环境变量 EDGEONE_TOKEN（GitHub Actions secret 已配置）
    - 或 --cookie "eo_token=...; eo_time=..." 手动传入
输出 home_alive.json：{ "updated": ..., "alive": [api地址...], "dead": [...] }
"""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.request

SUPER_SOURCE_DOMAIN = os.environ.get("SUPER_SOURCE_DOMAIN", "tvbox-juxu-spegz3xv.edgeone.cool")
SUPER_SOURCE_PROJECT = os.environ.get("SUPER_SOURCE_PROJECT", "makers-lxb2wlvf61qz")
BATCH = 8  # 与函数端并发上限一致

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))


def mint_token(api_token):
    """调用 EdgeOne API 签发预览 token。"""
    body = json.dumps({
        "Action": "DescribePagesEncipherToken",
        "Region": "ap-guangzhou",
        "ProjectId": SUPER_SOURCE_PROJECT,
        "Text": SUPER_SOURCE_DOMAIN,
    }).encode()
    req = urllib.request.Request(
        "https://pages-api.cloud.tencent.com/v1", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_token})
    with OPENER.open(req, timeout=20) as r:
        d = json.loads(r.read().decode())
    resp = d.get("Data", {}).get("Response", d)
    tok, ts = resp.get("Token"), resp.get("Timestamp")
    if not tok or not ts:
        raise RuntimeError("token 签发失败: " + str(d)[:200])
    return f"eo_token={tok}; eo_time={ts}"


def probe_batch(url, items, cookie, timeout=25):
    """调用 EdgeOne probe 接口探测一批源（最多8个）。"""
    body = json.dumps({"items": items}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "okhttp/3.12.0"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with OPENER.open(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return d.get("results", [])


def load_sites(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    sites = d.get("sites", []) if isinstance(d, dict) else d
    return [s for s in sites if str(s.get("api", "")).startswith("http")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="latest.json")
    ap.add_argument("--out", dest="out", default="home_alive.json")
    ap.add_argument("--url", default=f"https://{SUPER_SOURCE_DOMAIN}/api?mode=probe")
    ap.add_argument("--cookie", default="")
    args = ap.parse_args()

    cookie = args.cookie
    if not cookie:
        token = os.environ.get("EDGEONE_TOKEN", "")
        if token:
            try:
                cookie = mint_token(token)
                print("[auth] EdgeOne token 签发成功")
            except Exception as e:
                print(f"[auth] token 签发失败: {e}", file=sys.stderr)
        if not cookie:
            print("[warn] 无 token，若域名开启预览鉴权将全部返回 401")

    sites = load_sites(args.inp)
    print(f"[probe] 共 {len(sites)} 个 HTTP 源，分 {(len(sites)+BATCH-1)//BATCH} 批探测")

    results = []
    for i in range(0, len(sites), BATCH):
        batch = [{
            "api": s.get("api", ""),
            "type": s.get("type", 0),
            "ext": s.get("ext", ""),
        } for s in sites[i:i + BATCH]]
        try:
            rs = probe_batch(args.url, batch, cookie)
            results.extend(rs)
        except Exception as e:
            print(f"[probe] 批次 {i//BATCH+1} 失败: {e}", file=sys.stderr)
            for s in sites[i:i + BATCH]:
                results.append({"api": s.get("api", ""), "ok": False, "note": "批次请求失败"})
        done = min(i + BATCH, len(sites))
        print(f"  {done}/{len(sites)}", flush=True)

    alive = [r["api"] for r in results if r.get("ok")]
    dead = [r["api"] for r in results if not r.get("ok")]
    out = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "from": "edgeone-cn-node",
        "total": len(results),
        "alive_count": len(alive),
        "dead_count": len(dead),
        "alive": alive,
        "dead": dead,
        "details": results,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[结果] 国内节点存活 {len(alive)}/{len(results)}，失效 {len(dead)}")
    print(f"[输出] {args.out}")


if __name__ == "__main__":
    main()
