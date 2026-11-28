#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前校验：latest.json 与 kids.json 都必须能被 FongMi/OK影视 正常解析。
任何一项不通过就退出非 0，Actions 会失败、不提交坏配置。"""
import json
import sys
import urllib.parse
from pathlib import Path


def check(path: str, min_sites: int) -> list:
    errs = []
    p = Path(path)
    if not p.exists():
        return [f"{path} 不存在"]
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"{path} JSON 解析失败: {e}"]
    if not isinstance(d, dict):
        return [f"{path} 顶层不是对象"]
    sites = d.get("sites")
    if not isinstance(sites, list):
        return [f"{path} sites 不是数组"]
    if len(sites) < min_sites:
        errs.append(f"{path} 站点只有 {len(sites)} 个（<{min_sites}）")
    keys, names = set(), set()
    for s in sites:
        if not isinstance(s, dict):
            errs.append(f"{path} sites 含非对象项")
            continue
        name, api, typ = s.get("name"), s.get("api"), s.get("type")
        if not name or not isinstance(name, str):
            errs.append(f"{path} 站点缺 name: {s}")
            continue
        if name in names:
            errs.append(f"{path} 站点重名: {name}")
        names.add(name)
        if api is None or not isinstance(api, str):
            errs.append(f"{path} {name} api 不是字符串")
        elif not (api.startswith("http") or api.startswith("csp_")):
            errs.append(f"{path} {name} api 非法: {api[:32]}")
        if not isinstance(typ, int):
            errs.append(f"{path} {name} type 不是整数")
        k = s.get("key")
        if not k or not isinstance(k, str):
            errs.append(f"{path} {name} 缺 key")
        elif k in keys:
            errs.append(f"{path} key 重复: {k}")
        keys.add(k)
        cats = s.get("categories", [])
        if not isinstance(cats, list):
            errs.append(f"{path} {name} categories 不是数组")
        ext = s.get("ext")
        if isinstance(ext, str) and ext.strip():
            try:
                json.loads(ext)
            except Exception:
                errs.append(f"{path} {name} ext 不是合法 JSON")
    lives = d.get("lives")
    if not isinstance(lives, list) or not lives:
        errs.append(f"{path} lives 为空")
    else:
        for lv in lives:
            if not isinstance(lv, dict) or not (lv.get("url") or lv.get("channels") or lv.get("groups")):
                errs.append(f"{path} lives 项缺 url/channels")
    return errs


def load_blocklist() -> set:
    p = Path("home_blocklist.json")
    if not p.exists():
        return set()
    try:
        return {str(x).lower() for x in json.loads(p.read_text(encoding="utf-8")).get("hosts", [])}
    except Exception:
        return set()


ADULT_WORDS = ["伦理", "理论片", "里番", "无码", "有码", "番号", "禁漫", "乱伦", "三级",
               "成人", "福利", "自拍", "偷拍", "探花", "黑料", "约炮", "门事件"]


def main() -> int:
    blocked = load_blocklist()
    errs = check("latest.json", 20) + check("kids.json", 3)
    try:
        kid = json.loads(Path("kids.json").read_text(encoding="utf-8"))
        for s in kid.get("sites", []):
            for cat in s.get("categories", []) or []:
                if any(w in str(cat).lower() for w in ADULT_WORDS):
                    errs.append(f"kids.json {s.get('name')} 分类仍含成人词: {cat}")
    except Exception:
        pass

    for f in ("latest.json", "kids.json"):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for s in d.get("sites", []):
            api = str(s.get("api") or "")
            if api.startswith("http") and urllib.parse.urlsplit(api).netloc.lower() in blocked:
                errs.append(f"{f} {s.get('name')} 命中家宽黑名单域名 {urllib.parse.urlsplit(api).netloc}")
    if errs:
        print("[校验失败]")
        for e in errs:
            print("  -", e)
        return 1
    print("[校验通过] latest.json / kids.json 结构正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
