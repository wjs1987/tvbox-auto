# 项目上下文（换电脑 / 新会话必读）

> 这份文件是给"新电脑上的新会话"看的。读完它就不需要再问用户背景。
> 最后更新：2026-11-26

## 一句话

给魔屏上的 OK影视（FongMi 内核）做一套**全自动 TVBox 源维护系统**：每天自动检测点播源、汇总去重成"全网汇总"超级源、整理直播源，输出一份可直接导入的配置。

## 三个部件

| 部件 | 跑在哪 | 干什么 |
|---|---|---|
| 点播流水线 `check_sources.py` | GitHub Actions（每天 03:00 北京时间）+ 本地 | 爬源池 → 测活/测速 → 生成 `latest.json`（配置本体）+ `super_catalog.json`（片单目录） |
| 超级源 `super-source/` | 腾讯 EdgeOne Pages 边缘函数 | 把 `super_catalog.json` 变成 maccms 标准接口 `/api`，供"全网汇总"分类调用 |
| 直播整理 `build_live.py` | 本地跑（测速必须国内网络） | 多源抓取 → 频道归一化分组 → 多线路合并 → 实测择优 → 写 `live.json`，被流水线并入 `latest.json` 的 `lives` |

数据流：

```
GitHub Actions(每日) ──► latest.json（含 sites + lives）──► 魔屏 OK影视 配置地址
        │                        ▲
        └─► super_catalog.json ──┘（复制到 super-source/catalog.json 随函数部署）
                                        │
                                  EdgeOne /api ◄── 魔屏"全网汇总"站点
```

## 关键常量

| 项 | 值 |
|---|---|
| GitHub 仓库 | `https://github.com/WJS1987/tvbox-auto` |
| 超级源域名 | `https://tvbox-juxu-spegz3xv.edgeone.cool` |
| EdgeOne 项目名 | `makers-lxb2wlvf61qz` |
| 用户家宽 | 上海移动，**无 IPv6** |
| 魔屏配置地址 | `latest.json` 的 raw 地址（gh-proxy 加速） |

**密钥**：`EDGEONE_TOKEN` 只存在 GitHub Secrets，本地跑时用环境变量传入，**不要写进任何文件**。超级源的 `eo_token` Cookie 由流水线每小时刷新后自动写进 `latest.json` 的 `header` 里。

## 文件职责

| 文件 | 说明 |
|---|---|
| `check_sources.py` | 主流水线：爬取、测速、生成 latest.json / super_catalog.json / report.md |
| `build_live.py` | 直播源整理（本次新增），参数见下 |
| `config/tv.json` | 种子配置：157 个种子站点 + flags/parses/lives 模板 |
| `super-source/functions/api.js` | 边缘函数：maccms 接口、片单聚合、详情聚合多线路、成人过滤 |
| `super-source/catalog.json` | 片单静态目录（随函数部署，毫秒级加载） |
| `latest.json` | 最终配置（sites + lives），魔屏读的就是它 |
| `HANDOFF.md` | 直播部分的交接说明（更细的操作步骤） |
| `live.json` / `live.txt` | 直播成品（FongMi LiveConfig / 通用 TXT） |

## 常用命令

```bash
# 直播：在家里网络实测（推荐，8~10 分钟）
python build_live.py --test --test-urls 5 --limit-urls 3 --isp mobile

# 直播：只抓取合并，不测速（快，几十秒）
python build_live.py --out live_raw.json

# 点播：完整跑一轮（会写 latest.json / super_catalog.json / report.md）
python check_sources.py --auto --workers 30 --timeout 8

# 边缘函数部署（在 super-source 目录）
npx edgeone pages deploy . -n makers-lxb2wlvf61qz -t <EDGEONE_TOKEN>
```

## 踩过的坑（别重复踩）

1. **eo_token**：带 token 的 URL 会 302 换 cookie，FongMi 没有 CookieJar → 站点 `api` 必须保持干净，把 `Cookie: eo_token=..; eo_time=..` 写进站点 `header`。
2. **字段名**：返回必须是 maccms 标准 `vod_id/vod_name/vod_pic/vod_remarks`，写 `id/name` 会被播放器整体丢弃。
3. **ac=detail**：FongMi 的 `ac=detail` 既用于详情也用于分类浏览，必须按"有没有 ids"分流。
4. **gh-proxy 缓存**：raw.githubusercontent 走 CDN 会缓存，要加 `?t=时间戳` 或 `_cb` 穿透。
5. **Actions 测速无效**：GitHub 机房在海外，测出来的速度/存活不代表国内；直播测速只能在用户家里跑。
6. **运营商定向**：`2409:` 是移动 IPv6、`139.227.221.147` 是上海电信 IPTV，用户是移动 → 这些源必死。`--isp mobile` 用于把同运营商源排前。
7. **成人过滤**：`check_sources.py` 的 `ADULT_RE` 和 `build_live.py` 的 `BLOCK_RE` 是固定策略，不提供关闭选项，不要改。
8. **git push 别吞错误**：之前用 `2>/dev/null; echo OK` 导致三次假推送，现在必须显式看 push 输出并 `git log -1` 验证。

## 当前状态（2026-11-26）

- 点播：上游 5 个（淘片/建安/极速/虎牙/光速），超级源 839 部（去重后），热请求 ~0.47s
- 直播：`build_live.py` 已跑通；**公司网络**实测 972 频道 → 399 可播（央视 9 / 卫视 23）
- 直播已接入流水线，`latest.json` 的 `lives` = 自建直播 + 虎牙/斗鱼/YY 一起看

## 待办（在家里的电脑上做）

1. 关代理，跑 `python build_live.py --test --test-urls 5 --limit-urls 3 --isp mobile`
2. 看央视/卫视数量是否提升（目标 央视 ≥20、卫视 ≥30）
3. 若仍偏低：给 `build_live.py` 的 `SOURCES` 增加移动可用的源（`chinamobile/cmcc` 域名、39.134-39.135 段）
4. 把 `live.json` 提交推送，触发流水线，魔屏重新拉配置验证直播页
5. 观察：`live.json` 内联进 `latest.json` 后配置体积约 100KB，确认魔屏加载无异常
