# TVBox 自动维护仓库（自用 OK影视 源）

一个人的自建源：GitHub Actions 每天爬源、测活、测速，产出 `latest.json` 给天猫魔屏里的 OK影视 用。
**纯云端，不需要家里的电脑开机参与。**

## 流水线干什么

每天北京时间 03:00（＋每次推送代码）自动执行：

```
爬取聚合配置站（sources_pool.json 里的地址，含多仓递归）
→ 提取点播源 / 直播频道 / 直播源引用
→ 按 (type, 规范化 API) 去重
→ 并发测试：HTTP 存活 + 延迟 + 片单数据量 + 真实可播性
→ 剔除 HOME_DEAD_APIS 黑名单（家里实测连不上的源）
→ 按 可播性 → 片单量 → CDN 速度 → 延迟 排序，取前 50 个
→ 生成 latest.json（+ report.md），自动提交回仓库
```

直播由 `build_live.py` 抓取归一化后写 `live.txt`；配置里直播只放一条 **url 引用**，
频道表等用户点进直播页才加载，主配置因此只有 ~20KB。

## 关键设计（别随手改）

- **站点 key 是 API 的短哈希**，不是序号；改成 `src1/src2` 递增会让客户端收藏全部失效。
- **jar / spider 自托管在 `libs/`**，统一用 jsDelivr 的 `.png` 后缀分发（jsDelivr 对 `.jar` 返回 403，
  对 `.png` 秒开；客户端只校验字节和 md5，不看后缀）。raw 直连 2.7MB 的 jar 实测 142 秒后被重置。
- **quickSearch 只给 type=1 且无 jar/ext 的最快 6 个 CMS 源**；全开会让 1GB 内存的魔屏搜索页长时间空白。
- **直播外链**：`latest.json` 里 `lives` 只留 `live.txt` 的 url，别把几千个频道内联回去（会把配置撑到 400KB）。
- **国内探测链路已废弃**：原来靠 EdgeOne 边缘函数回传国内节点探测结果，该服务已删，
  现在只用 `HOME_DEAD_APIS` 黑名单兜底。

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
| `build_live.py` | 直播整理：抓取频道、归一化分组、合并多线路 → `live.txt` |
| `sources_pool.json` | 聚合配置站地址池（想加源就加一行） |
| `config/tv.json` | 种子配置：参数沿用 + 兜底源 |
| `libs/*.png` | 自托管 jar/spider 的 jsDelivr 分发副本（`spider.png`、`pan_spider.png`、`fty_spider.png`） |
| `device_test.py` | 家里网络端到端实测（需同出口），生成 `device_report.md` |
| `latest.json` / `live.txt` | 成品（客户端用） |
| `archive/` | 已下线功能的存档（超级源、国内探测工具、旧网盘文档），流程不读 |

## 本地跑一次（可选）

```bash
python check_sources.py --auto --limit 20   # 快速试 20 个源
python check_sources.py --auto              # 全量
python device_test.py                       # 家里网络端到端实测
```

## 注意

- 仓库是 Public，内容任何人可见。
- `sources_pool.json` 里的聚合站随时可能挂，流水线会自动跳过失败的。
