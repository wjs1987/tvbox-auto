# TVBox 自动维护仓库（自用 OK影视 源）

一个人的自建源：GitHub Actions 每天爬 GitHub 上的公开配置清单，测活、测速，产出 `latest.json`
给天猫魔屏里的 OK影视（`com.fongmi.android.tv`）使用。**纯云端，不需要家里的电脑开机参与。**

## 只做一件事：采集站

本仓库只维护“采集站（CMS）”这一条线，也就是别人已经搭好的影视接口。

- ✅ 采集站：别人有片库和接口，我们只负责挑、测、筛
- ❌ 网盘：要各自登录、分享链接易失效、解析要维护 —— 已彻底下线
- ❌ 全网汇总（超级源）：要自建聚合服务 —— 已彻底下线

## 流水线

每天北京时间 03:00（＋每次推送代码）自动执行：

```
爬取 GitHub 上的配置清单（sources_pool.json，多仓递归一层）
→ 提取点播源 / 直播频道 / 直播源引用
→ 按 (type, 规范化 API) 去重
→ 并发测试：HTTP 存活 + 延迟 + 片单数据量 + 真实可播性
→ 剔除 HOME_DEAD_APIS 黑名单（家里实测连不上的源）
→ 按 可播性 → 片单量 → CDN 速度 → 延迟 排序，取前 50 个
→ 生成 latest.json（+ report.md），自动提交回仓库
```

直播清单是人工核验过的成果（`live.txt`，只保留序列会前进的真直播），不再由每日任务重建，避免混入伪直播；配置里直播只放
**url 引用**，频道表等用户点进直播页才加载，主配置因此保持在 20KB 出头。

## 关键设计（别随手改）

- **站点 key 是 API 的短哈希**，不是序号；改成 `src1/src2` 递增会让客户端收藏全部失效。
- **jar 自托管在 `libs/`**，用 jsDelivr 的 `.png` 后缀分发（jsDelivr 对 `.jar` 返回 403，
  对 `.png` 秒开；客户端只校验字节和 md5，不看后缀）。raw 直连 2.7MB 的 jar 实测 142 秒后被重置。
- **quickSearch 只给 type=1 且无 jar/ext 的最快 6 个 CMS 源**；全开会让 1GB 内存的魔屏搜索页长时间空白。
- **直播外链**：`latest.json` 里 `lives` 只留 `live.txt` 的 url，别把几千个频道内联回去（会把配置撑到 400KB）。
- **只从 GitHub 取源**：格式统一（TVBox JSON）、免登录、无反爬；其他站点的 txt/网页/二维码
  一律不接，避免天天维护解析器。
- **国内探测链路已废弃**：现在只用 `HOME_DEAD_APIS` 黑名单兜底。

## 魔屏 OK影视 配置地址

```
首选：https://gh-proxy.com/https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/latest.json
备用1：https://cdn.jsdelivr.net/gh/WJS1987/tvbox-auto@main/latest.json
备用2：https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/latest.json
```

- jsDelivr 对 `@main` 有静态缓存（实测落后 9~10 小时），所以首选 gh-proxy（0.6 秒、实时）；
  raw 直连要 20 秒以上，只当最后兜底。
- 流水线每轮结束会主动 purge jsDelivr 的 `latest.json` / `live.txt` 缓存。

## 目录

| 文件 | 作用 |
|---|---|
| `check_sources.py` | 主流水线：爬取、测活、测速、生成 `latest.json` + `report.md` |
| `build_live.py` | 直播整理（手动）：抓列表、归一化分组、合并多线路 → `live.txt` |
| `live_verify.py` | 直播核验（手动）：同一 m3u8 隔 8 秒抓两次，序列不动的一律剔除 → `live_verified.txt` |
| `sources_pool.json` | GitHub 配置清单地址池（想加源就加一行） |
| `config/tv.json` | 种子配置：参数沿用（ads 黑名单）+ 兜底源 |
| `libs/spider.png` | 全局爬虫包（type3 源用），jsDelivr 分发副本 |
| `libs/pan_spider.png` | 磁力站爬虫包（SeedHub / New6V 用） |
| `latest.json` / `live.txt` | 成品（客户端用） |
| `report.md` | 每轮运行报告（存活/失效/精选明细） |

## 本地跑一次（可选）

```bash
python check_sources.py --auto --crawl-only   # 只爬取看清单质量，不联网测
python check_sources.py --auto --limit 20     # 快速试 20 个源
python check_sources.py --auto                # 全量
python build_live.py                          # 单独重建 live.txt（原始全量）
python live_verify.py live.txt live_verified.txt   # 再核验，只留真直播
```

## 注意

- 仓库是 Public，内容任何人可见；配置里不含任何账号信息。
- `sources_pool.json` 里的清单仓库随时可能停更，流水线会自动跳过失败的地址。
- 冷启动 12~13 秒，瓶颈在 OK影视 自身首屏加载，配置侧已无优化空间。
