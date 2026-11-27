# 超级源 — EdgeOne Pages 部署说明

把精选片库的片单汇总去重成一个"全网汇总"源，部署在腾讯 EdgeOne Pages（免费）。

## 部署步骤（控制台操作，约 10 分钟）

1. 注册腾讯云账号：https://cloud.tencent.com （QQ/微信快捷登录），完成实名认证
2. 打开 EdgeOne Pages 控制台：https://console.cloud.tencent.com/edgeone/pages
3. **创建 KV 存储**：左侧「KV 存储」→ 开通 → 创建命名空间，**名称必须填 `KV`**
4. **导入项目**：「项目」→ 创建项目 → 选择「导入 Git 仓库」→ 授权 GitHub → 选 `tvbox-auto` 仓库
   - 项目名：`tvbox-super-source`（任意）
   - 根目录：`super-source`
   - 构建命令：留空（纯函数项目无需构建）
   - 输出目录：留空或 `.`
5. 部署完成后获得访问地址：`https://tvbox-super-source.edgeone.app`（以实际为准）
6. **验证**：浏览器打开 `https://你的地址/api?mode=status`，看到 JSON 即成功
   - 第一次访问会自动重建片单（约 10~20 秒），之后秒开

## 环境变量（可选，控制台项目设置里配）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MAX_UPSTREAMS` | 6 | 汇总取多少个源 |
| `PAGES_PER_SOURCE` | 2 | 每个源抓几页 |
| `LATEST_URL` | 内置 | latest.json 地址（默认 GitHub raw，多线路自动回退） |
| `REFRESH_TOKEN` | 空 | 设置后 /refresh 需带 key 参数 |

## 魔屏 OK影视 填法

配置 latest.json 的 sites 里第一项（流水线自动加），或手动测试：

```
https://你的地址/api
```

## 接口约定（FongMi 兼容）

- `?ac=list` 汇总片单（支持 `t=分类` `pg=页码`）
- `?ac=list&wd=片名` 跨源全局搜索（自动去重）
- `?ac=detail&ids=xx` 实时回原始源取播放地址
- `?mode=status` / `?mode=refresh` 状态与手动重建
- `?ac=img&u=<图片URL>` 图片代理（见下）

片单每 20 小时自动重建；源失效由每日流水线保证 latest.json 始终可用。

## 图片代理（解决海报只显示片名首字）

部分上游采集站的海报域名在国内被 DNS 污染（`taopianimage.xyz:43333`、
`un.shayu260522.top` → `1.1.1.1`；`fb.lj260522.top` 解析失败），
OK影视 只能显示片名首字占位。函数在输出 `vod_pic` 时会自动把这些域名的图片
改写成 `?ac=img&u=...`，由 EdgeOne 海外边缘节点（DNS 干净）代拉图片再返回。

**重要前提**：图片请求由 OK影视 的图片加载器发起，**不会携带站点的 eo_token Cookie**。
因此本项目必须部署到**正式域名 / 已开启公开访问**（访问 `/api?mode=status` 不带任何
Cookie 应返回 JSON），否则图片代理会 401。

- 有自定义域名：在 EdgeOne Pages 控制台为项目绑定域名，绑定后该域名即免预览鉴权。
- 无自定义域名：控制台关闭「预览链接保护 / 访问鉴权」，用 `*.edgeone.app` 正式域名。

代理仅放行白名单域名（`PROXY_PIC_HOSTS`），非白名单返回 403，不会被当作开放代理滥用。
图片缓存 7 天，正常域名（如 `fhp261111.top`）保持直连不走代理。
