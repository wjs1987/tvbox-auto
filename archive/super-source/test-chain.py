# -*- coding: utf-8 -*-
# 模拟 OK影视/TVBox 客户端完整播放链路（本地测试）
import json, urllib.request, urllib.parse, sys

BASE = "http://127.0.0.1:8787/api.js"

def get(params):
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "okhttp/3.12.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", "ignore")

def test_pan_detail(link, disk="夸克网盘", title="链路测试"):
    vid = "pan:" + "|".join(urllib.parse.quote(x) for x in [disk, title, link])
    print(f"== 1) 详情解析 {disk} {link}")
    raw = get({"ac": "detail", "ids": vid})
    d = json.loads(raw)
    info = (d.get("list") or [{}])[0]
    play_from = info.get("vod_play_from", "")
    play_url = info.get("vod_play_url", "")
    print("   线路:", play_from[:60])
    eps = play_url.split("$$$")
    print("   vod_play_url 段数:", len(eps))
    if eps:
        first = eps[0]
        print("   首集片段:", first[:150])
        # 取第一集的播放地址
        seg = first.split("#")[0]
        name, _, u = seg.partition("$")
        if not u:
            u = seg
        print("== 2) 取第一集 m3u8:", u[:120])
        m3u8 = get({"url": u, "mode": "panstream"}) if "mode=" not in u else get_raw(u)
        # u 里已带 mode=panstream 参数, 直接 GET
        return u
    return None

def get_raw(u):
    req = urllib.request.Request(u, headers={"User-Agent": "okhttp/3.12.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "ignore")

def test_m3u8_and_segment(u):
    print("== 3) 经 panstream 代理拉 m3u8")
    m3u8 = get_raw(u)
    lines = [l for l in m3u8.splitlines() if l and not l.startswith("#")]
    print("   分片数:", len(lines))
    print("   首个分片地址:", lines[0][:120] if lines else "(空)")
    if lines:
        print("== 4) 拉首个分片验证视频流")
        req = urllib.request.Request(lines[0], headers={"User-Agent": "okhttp/3.12.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(64 * 1024)
            print("   HTTP", r.status, "前64KB已取, 总大小头:", r.headers.get("Content-Length"))
            # ts 分片魔数: 每188字节一个 0x47 sync byte
            print("   TS魔数校验(应为0x47):", hex(data[0]) if data else "空")

if __name__ == "__main__":
    link = sys.argv[1] if len(sys.argv) > 1 else "https://pan.quark.cn/s/0561c2625585"
    disk = sys.argv[2] if len(sys.argv) > 2 else "夸克网盘"
    u = test_pan_detail(link, disk)
    if u:
        try:
            test_m3u8_and_segment(u)
        except Exception as e:
            print("   m3u8/分片失败:", e)
    else:
        print("!! 详情未产出播放地址")
