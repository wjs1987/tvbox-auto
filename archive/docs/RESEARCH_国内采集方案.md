# 饭太硬网盘资源实现方式 + 国内直采方案研究

> 调研时间：2026-11-27　结论基于饭太硬实际配置解析 + EdgeOne 国内节点实测 + 全网检索

## 一、饭太硬是怎么做"网盘资源"的

### 1. 它的服务器只发配置，不负责爬取

饭太硬配置（`http://www.饭太硬.net/tv`）的关键字段：

```json
"spider": "https://xiuxiu-pro-new.meitudata.com/posters/8ad28c3ad40145e2f9331fe3f8abddb2.jpg;md5;5302302a73a9e27cfaa65f3873b727ab"
```

这个 `.jpg` 其实是**伪装成图片的 Java 爬虫 jar 包**（TVBox/FongMi 的 spider 机制，分号后是 md5 校验）。52 个站点里绝大多数是：

```json
{"key":"ZPan","name":"🎈盘搜┃四盘","type":3,"api":"csp_S_zpsGuard","ext":{"siteUrl":"http://107.173.211.148/"}}
{"key":"YpanSo","name":"🐌盘她┃夸父","type":3,"api":"csp_YpanSoGuard","ext":{"Cloud-drive":"tvfan/Cloud-drive.txt"}}
```

- `type: 3` = 客户端 CSP 爬虫站点
- `csp_XXXGuard` = jar 里的 Java 爬虫类
- `ext.Cloud-drive` = 指向用户自己的网盘账号配置（cookie/token）

**也就是说：真正的搜索和爬取发生在电视/手机端，用的是你家里的网络，饭太硬服务器只是个配置分发站。** 这就是它不需要"国外服务器爬国内站"的原因。

### 2. 网盘爬虫的 JS 规则（开源可查）

饭太硬生态的 JS 插件库在 `github.com/qist/tvbox` 的 `FTY/` 目录，其中：

- `盘他.js`（1.5KB）：就是一个标准的 drpy 规则
  ```
  host: 'https://panso.pro'
  searchUrl: '/search?exact=false&format=video&page=fypage&q=**...'
  play_parse: true
  播放: push://分享链接
  ```
- `短视频.js`：抓 `sharenice.net`，按抖音/快手/微视等分类直接取 mp4 直链
- `json/` 目录里是**预爬好的分享索引**：`115share.txt`(50KB)、`alishare.txt`、`pikpakclass.json` + `.db.gz`(1MB)、`pushshare.txt` 等

### 3. 对我们的启示

| 饭太硬做的 | 我们现在的做法 | 差距 |
|---|---|---|
| 爬虫跑在用户设备（国内网络） | 爬虫跑在 GitHub 美国机房 | 我们看到的"存活源"大量国内打不开 |
| 网盘分享索引预先爬好、随配置分发 | 只爬 maccms 采集站 API | 完全没有网盘这类资源 |
| 播放时用 `push://` 交给客户端解析 + 用户网盘账号 | 直接给 http 播放地址 | 网盘资源需要账号授权 |

---

## 二、关键实测：EdgeOne 函数就是现成的国内爬虫

今天在 EdgeOne 函数里加了 `mode=crawl` 白名单探针，从国内节点实测：

| 目标 | 结果 | 延迟 | 说明 |
|---|---|---|---|
| `panso.pro`（盘搜VIP） | ✅ 200 / 26KB | **675ms** | 完整搜索结果页，可直接解析 |
| `cn.bing.com` | ✅ 200 / 60KB | 718ms | 国内必应可用 |
| `www.sogou.com` | ✅ 200 / 3.9KB | 513ms | 可用 |
| `www.baidu.com` | ⚠️ 200 / 227B | 115ms | 返回 JS 跳转到 http，需换入口 |

请求标记 `eo-is-mainland: 1`（中国大陆节点，上海）。

盘搜结果解析验证（搜"许光"）：单页 10 条，每条含网盘类型（夸克/百度）、片名、`/doc/xxx` 详情链接、文件名、标签。详情页 23KB，含完整文件列表。

**结论：我们不需要任何新服务器——现有的 EdgeOne 定时任务就跑在国内节点，可以直接爬国内站。** 之前"只能在国外 GitHub 爬"的前提已经不成立了。

---

## 三、六条国内直采路线（按推荐度排序）

### 方案 A：EdgeOne 国内节点直采（零成本、已在体系内）⭐⭐⭐⭐⭐

- 做法：定时任务加一个 `mode=crawljob`，每小时在国内节点抓盘搜站/搜索引擎，结果存 Blob
- 优：不用买服务器、不用 GitHub 参与、国内出口、复用现有 Blob 缓存和读取链路
- 缺：单函数执行时间/并发有限制；盘搜站可能改版或要登录；详情页分享链接需额外解析
- 适合：把"国内能搜到的资源名 + 网盘类型 + 详情链接"做成片单

### 方案 B：PanSou 网盘搜索聚合（API 现成）⭐⭐⭐⭐⭐

- 开源项目 `fish2018/pansou`（Go，3.5k stars），Docker 一键部署
- 聚合 Telegram 频道 + 60 个插件 + 12 种网盘（百度/阿里/夸克/迅雷/UC/115/天翼/123/PikPak/磁力/电驴）
- 提供 HTTP API：`GET /search?query=电影&disk=baidu`，直接返回 `https://pan.quark.cn/s/xxx` 这种真实分享链接
- 优：结果直接可用，不用自己解析网页；部署在国内 VPS/NAS/家里电脑都行
- 缺：需要一台常开的国内机器（1核1G 够用）；TG 源可能受网络影响
- 变体：直接用别人公开的 PanSou 实例（省部署，但稳定性不可控）

### 方案 C：客户端 CSP/JS 爬虫（饭太硬同款）⭐⭐⭐

- 做法：把 drpy JS 规则 + Java spider jar 下发给魔屏，爬虫在电视端跑
- 优：完全用你家网络，零服务器成本，和饭太硬一模一样
- 缺：配置复杂；需要网盘账号；OK 影视/FongMi 对 drpy 的兼容性要实测
- 适合：作为补充源，不适合做主片单

### 方案 D：AList / OmniBox 家庭媒体中枢 ⭐⭐⭐⭐

- 家里 NAS 或旧电脑 Docker 跑 `lampon/omnibox` 或 `alist-tvbox`
- 绑定 115/夸克/UC/阿里/百度/迅雷等网盘 token，直接浏览和秒播
- 内置 PanSou/PanHub 盘搜联动，输出 TVBox 订阅地址，全屋设备共用一个配置
- 优：播放最稳（网盘授权后秒播）；生态成熟
- 缺：需要常开设备 + 部分网盘会员；不属于"爬"，属于"整理你自己的资源"

### 方案 E：国内云函数 / 轻量服务器 ⭐⭐⭐

- 腾讯云 SCF / 轻量应用服务器（几元/月），跑 Python 爬虫定时抓取
- 优：稳定可控、国内 IP、想爬什么爬什么
- 缺：要花钱、要维护；EdgeOne 函数已经能覆盖大部分需求，性价比不高

### 方案 F：搜索引擎直接检索 ⭐⭐⭐

- `cn.bing.com`、`sogou` 已验证国内节点可直连
- 关键词模板：`片名 + 网盘`，从搜索结果页提取分享链接
- 优：覆盖广、不依赖专门盘搜站
- 缺：结果噪声大、反爬策略多变、需要清洗

---

## 四、嗅探 / 检索的补充思路（别只盯"采集站 API"）

1. **视频直链嗅探**：B站/抖音/快手页面的 m3u8/mp4 地址，饭太硬的 `短视频.js` 就是这类
2. **磁力 / BT**：PanSou 已把磁力、电驴作为独立网盘类型返回
3. **Telegram 频道索引**：PanSou 的核心数据源就是 90 个 TG 频道，等于绕过了传统搜索引擎
4. **分享列表订阅**：饭太硬的 `alishare.txt` / `pikpakclass.json` 思路——订阅别人维护的分享索引，比自己爬更省心
5. **国内代码托管镜像**：Gitee/GitCode 上的配置仓库（注意会定期清理，不适合做长期源）

---

## 五、推荐组合（下一步可落地）

```
EdgeOne 定时任务（国内节点）
   ├─ 抓盘搜站 panso.pro → 片单（片名/网盘类型/详情链接）→ 存 Blob
   └─ 调用自建 PanSou API → 真实网盘分享链接 → 存 Blob
                    ↓
GitHub 流水线读 Blob → 生成 latest.json / catalog.json → 魔屏
                    ↓
播放：网盘链接用 push:// 交客户端解析（需网盘账号），
      或接 AList/OmniBox 授权后直接秒播
```

**第一步建议**：先在 EdgeOne 定时任务里加盘搜抓取，把"国内盘搜结果"做成一类新片单。这一步不用买服务器、不用装 Docker，今天就能做完并验证。


---

## 六、已落地：方案 A 网盘直采（2026-11-27 实测通过）

- **函数端** `api.js` 新增 `mode=pansearch`：EdgeOne 国内节点实时搜 `panso.pro` → 详情页提取 `jump-link` 真实网盘直链（夸克/百度/阿里/115/UC/123/PikPak）→ 转 maccms 格式返回
- **详情接口**：`pan:` 前缀 id 直接吐出网盘链接，点开即得到 `打开夸克网盘$https://pan.quark.cn/s/xxx`
- **缓存**：Blob(6h) + 内存双层，重复搜索提速
- **配置端** `check_sources.py`：流水线自动置顶 `📀网盘搜索` 独立站点（与全网汇总共用新鲜 token）
- **实测**：搜"庆余年"返回 9 条真实网盘资源，3.8s；魔屏搜索时作为独立源并行调用，不拖慢主片单（本地命中 1.6s）
- **注意**：网盘链接需客户端支持网盘解析（FongMi 内核 + `parses` 配置），播放时可能需网盘账号/跳转浏览器

### 其它免费源接入状态
| 源 | 状态 | 说明 |
|---|---|---|
| panso.pro | ✅ 已接入 | 国内直采，工作正常 |
| cuppaso / alipansou / codelicence / yunso / duanjuso | 探测可访问 | 需各自解析规则，未接入（panso 已覆盖同类聚合） |
| 磁力/种子（SeedHub/New6V） | ✅ 配置已有 | 依赖 spider.jar，魔屏原生迅雷引擎可播 |
