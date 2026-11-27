# 饭太硬 jar 是什么、靠不靠得住、能不能自己做

> 2026-11-27 写。起因：你问「这个 jar 是饭太硬自己做的吗？他不更新/停服会影响我们吗？我们不能自己做吗？」
> 本文是**实际解包取证**的结果，不是推测。

---

## 一、结论速览

| 你的问题 | 答案 |
|---|---|
| 是饭太硬自己做的吗？ | **是，私有加壳包**，基于开源 `CatVodSpider` 框架二开，再用 OLLVM + native so 加固，无源码、无开源仓库 |
| 他停服/不更新会影响我们吗？ | **会，但只影响 6 个网盘站点**；「全网汇总」、在线源、直播、超级源全部不受影响（那些是我们自己的 EdgeOne 函数 + GitHub Actions） |
| 我们不能自己做吗？ | **能，而且已经做了最关键一步**：把 jar 镜像进自己仓库，去掉对第三方图床的依赖。真正「从零重写」可行但不划算，见第五节 |

---

## 二、取证：这个 jar 到底是什么

从美图秀秀图床（伪装成 `.jpg`）下载后解包，5 个文件：

| 文件 | 大小 | 作用 |
|---|---|---|
| `classes.dex` | 38 KB | **只是加载壳**，不是业务代码 |
| `assets/ftyguard_v7.so` | 85 KB | ARMv7 加固库 |
| `assets/ftyguard_v8.so` | 102 KB | ARMv8 加固库 |
| `assets/ftyshinidie.guard` | 1.28 MB | **加密的真实代码**（香农熵 7.993 ≈ 加密/压缩，首字节伪装成 ZIP 但打不开） |
| `META-INF/MANIFEST.MF` | 73 B | `Created-By: dx 1.11` |

关键证据：

- `fty` 前缀 = 饭太硬拼音首字母；`ftyguard` / `ftyshinidie.guard` 是其私有命名
- `classes.dex` 里有完整类名表：`com/github/catvod/spider/MyDriveGuard`、`WoGGGuard`、`YpanSoGuard`、`BpanSoGuard`、`KkSsGuard`、`UuSsGuard`、`DouDouGuard`… 共几十个 `csp_*Guard`
- `com.github.catvod.spider` 是**开源 CatVodSpider 框架**的包名 → 说明是在开源骨架上二开
- so 里带 `github.com/heroims/obfuscator.git`（OLLVM 混淆器）→ 刻意防反编译
- **md5 = `5302302a73a9e27cfaa65f3873b727ab`**，与配置里 `;md5;` 完全一致

一句话：**饭太硬（或其技术作者）自己编译的闭源加固包，托管在第三方图床上，不是任何官方/开源服务。**

---

## 三、它会不会「电话回家」

逐个文件扫描内嵌 URL / 域名，结果：

| 文件 | 找到的域名 |
|---|---|
| `classes.dex` | `xmlpull.org`、`jaxp.pro`（XML 解析器标准字符串，非服务器） |
| `ftyguard_v7/v8.so` | `android.googlesource.com`、`github.com`（编译器/混淆器留下的） |
| `ftyshinidie.guard` | 无（**加密的**，无法静态确认） |

判断：

- **可见部分没有任何「作者服务器」依赖** → 扫码登录、网盘直链解析都在**电视端本地**跑，直接连夸克/阿里官方 API
- 但 `guard` 是加密的，**不能 100% 排除**里面藏了远程校验 / 授权开关。这是闭源加固包的固有风险，无法证实也无法证伪
- 实际上：饭太硬服务器只负责**下发配置**，jar 一旦下载到电视就是本地执行的，服务器关掉不会让已下载的 jar 失效

---

## 四、真正的风险在哪（重要）

风险不在「饭太硬停服」，而在 **`jar 的下载地址会失效`**：

| 依赖 | 原地址 | 风险 |
|---|---|---|
| 饭太硬 jar | `xiuxiu-pro-new.meitudata.com/...jpg` | 美图秀秀图床，**随时可能清理 / 防盗链 / 改路径** |
| pan 爬虫 jar | `raw.githubusercontent.com/regtostudy/...` | 别人的 GitHub fork，**原仓库已失效过一次**，可能再删 |
| 全局 spider | `img2.gelonghui.com/...png` | 格隆汇图床，同样第三方 |

图床挂掉时，电视端**同步下载 jar 会失败 → 那几个站点直接空白**（魔屏加载配置是同步等待）。这才是你之前反复遇到「站点突然没内容」的高频原因之一。

> 另外：网盘平台（夸克/阿里）自己改 API 也会让 jar 里的解析逻辑过期，
> 这属于**任何方案都要跟的维护**，跟饭太硬更不更新有关，但换成自研也一样要跟。

### 影响范围（说清楚，别慌）

| 受影响 | 不受影响 |
|---|---|
| 我的云盘 扫码登录、玩偶哥哥4K、盘她、盘他、抠抠、优汐（6 个） | **全网汇总（超级源）**、所有在线采集源、直播源、`latest.json` 生成、`📀网盘搜索` |
| 云盘搜索/SeedHub/New6V（pan jar 挂了才受影响） | EdgeOne 函数、GitHub Actions 流水线、Blob 存储 |

**最坏情况：网盘 4K 看不了，日常看片完全不受影响。**

---

## 五、我们能自己做吗？分三层

### ✅ 第 1 层：镜像自托管（**已完成，2026-11-27**）

把三个 jar 下载进本仓库 `libs/`，配置改指向自己的 GitHub raw：

```
libs/fty_spider.jar      1.40 MB  md5 5302302a73a9e27cfaa65f3873b727ab  (饭太硬包)
libs/pan_spider.jar      2.74 MB  md5 540cb3abf9883a2de6d73b0624ffcabc  (云盘/磁力搜索)
libs/global_spider.jar   1.15 MB  md5 3f9b9cac793a3e9b818131c8a7208800  (全局 spider，仅备份)
```

- `check_sources.py` 里 `PAN_JAR` / `FTY_JAR` 改为 `REPO_RAW`，9 个站点全部指向自有仓库
- 保留 `;md5;` 供 FongMi 做完整性校验
- 用 raw 直连（实测国内约 2 秒）；gh-proxy 要 51 秒会让电视黑屏等待，禁用
- **效果**：只要你的仓库在，jar 就在。图床删图、别人删库、饭太硬关站 —— 都不影响

> 全局 `spider` 是流水线按「配对最多 type3 存活源」动态投票选的，
> **强行写死会断掉配对、连累几十个站点**，所以没改代码，只备份文件。
> 真要切换时改 `spider` 字段一行即可，见第七节。

### ⚠️ 第 2 层：换开源 jar（可行，但功能不等价）

开源生态里有 `CatVodSpider`、`power721/alist-tvbox`（3.1k star）、`jadehh/TVSpider` 等。
但它们**不含** `MyDriveGuard` 这套私有网盘类，换上去「玩偶哥哥4K / 我的云盘」会直接找不到类。
要等价替代，得自己写几十个 `csp_*Guard` 实现。工作量不小，且同样要跟网盘 API 变化。

### 🔧 第 3 层：完全自研（能做，但不划算）

- 基于 `AndroidCatVodSpider` 开源骨架，用 Android Studio 编译自己的 `custom_spider.jar`
- 或者用 **AList / alist-tvbox** 自建服务端网关：把夸克/阿里挂成自己的 WebDAV，电视端直接读，**彻底不依赖任何 jar**
- 代价：需要一台常开的家庭设备（NAS/旧电脑）或服务器 + 持续维护网盘 token

**建议**：暂不上第 2/3 层。当前「自托管镜像 + 网盘只是增强」的定位已经把风险降到最低，
投入产出比最高。等哪天网盘 4K 变成刚需，再上 AList 自建。

---

## 六、jar 校验与更新方法

本地核对（防止仓库里的包被意外改动）：

```bash
md5sum libs/*.jar
# fty_spider.jar     5302302a73a9e27cfaa65f3873b727ab
# pan_spider.jar     540cb3abf9883a2de6d73b0624ffcabc
# global_spider.jar  3f9b9cac793a3e9b818131c8a7208800
```

若饭太硬后续出了新版 jar（新 md5），更新步骤：

1. 下载新 jar 覆盖 `libs/fty_spider.jar`
2. 算出新 md5：`python -c "import hashlib;print(hashlib.md5(open('libs/fty_spider.jar','rb').read()).hexdigest())"`
3. 改 `check_sources.py` 里 `FTY_JAR` 的 `;md5;` 值
4. 推送 → Actions 自动重新生成 `latest.json`

**不更新也不会突然坏** —— 只有当夸克/阿里改了登录或直链接口时才会逐步失效，那时再看有没有社区新版。

---

## 七、应急：外卖 jar 全挂了怎么办

| 场景 | 操作 |
|---|---|
| 自有仓库的 jar 误删 | 从 `libs/` 恢复并推送，或直接从本地 U 盘拷回仓库 |
| 想彻底不依赖 GitHub | 把 `libs/*.jar` 放到任何静态托管（自家 NAS / 对象存储），把 `REPO_RAW` 换成新地址即可 |
| 全局 spider 图床挂了 | 编辑 `latest.json` 顶层 `"spider"` 为 `https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/libs/global_spider.jar`，重新导入配置 |
| 网盘站点全空 | 不影响主功能；先确认 jar 地址能否在电视浏览器打开，再重导配置 |

---

## 八、一句话给家里的你

**jar 已经搬进自己仓库了，不用再担心饭太硬或图床哪天消失。**
日常看片走「全网汇总」，本来就跟这个 jar 无关；网盘 4K 是锦上添花，
真失效了也就是少几个站点，不会整个用不了。
