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

片单每 20 小时自动重建；源失效由每日流水线保证 latest.json 始终可用。
