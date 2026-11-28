# 项目上下文（当前版 · 2026-11-28 定稿）

## 一句话

用 GitHub Actions 每天从 **GitHub 上的公开配置清单**里爬源、测活、测速，产出一份纯静态的
`latest.json`，供家里的天猫魔屏（`com.fongmi.android.tv`，OK影视）直接拉取。全免费、纯云端。
只做“采集站”这一条线，网盘与全网汇总已彻底下线。

## 组成

| 组件 | 位置 | 作用 |
|---|---|---|
| 源清单池 | `sources_pool.json` | 13 条 GitHub raw 地址（ReneT3 系列 11 条 + niubihu1 + xiaolong69），流水线从这里爬 |
| 种子配置 | `config/tv.json` | 参数沿用（ads 黑名单等）+ 兜底源 + 直播频道原料 |
| 主流水线 | `check_sources.py` | 爬取 → 去重 → 测活/测速/试播 → 排序精选 → 写 `latest.json`、`report.md` |
| 直播整理 | `build_live.py` | 抓 GitHub m3u → 归一化分组 → 多线路合并 → 写 `live.txt` |
| 定时任务 | `.github/workflows/update.yml` | 每天 03:00 北京时间 + push 触发；跑完提交并 purge jsDelivr |
| 自托管 jar | `libs/*.png` | `spider.png`（全局 type3）、`pan_spider.png`（磁力站），jsDelivr 分发 |

## 客户端地址

```
https://gh-proxy.com/https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/latest.json
```

备用：`https://cdn.jsdelivr.net/gh/WJS1987/tvbox-auto@main/latest.json`、raw 直连。

## 产物规格

- `latest.json`：约 22KB、50 精选 + 2 磁力，quickSearch 6 个，直播 4 条（1 外链 + 3 轮播 m3u）
- `live.txt`：约 100KB，由 GitHub m3u 合并去重生成
- 冷启动：12~13 秒（瓶颈在 App 首屏）

## 不要做

- 不要把直播频道内联回 `latest.json`（会从 20KB 涨到 400KB，冷启动显著变慢）
- 不要改站点 `key` 生成规则（会让客户端收藏/进度全部失效）
- 不要把 jar 直连 GitHub raw（2.7MB 会被重置，必须走 jsDelivr `.png`）
- 不要再引入网盘、聚合汇总、非 GitHub 源地址（都已明确放弃并删除）
