# F1 QQ 群聊机器人

使用 QQ 开放平台官方机器人（WebSocket 接入）+ Jolpica-F1 免费数据。

功能：周四赛程预告、每节开赛前 30 分钟提醒、赛后自动推送结果；群里 @机器人 查询赛程、赛历、积分榜、结果。

## 一、需要你本人完成的步骤

1. 打开 https://q.qq.com ，用 QQ 登录，完成个人实名认证（姓名/身份证需与手机号实名一致）。
2. 在“机器人”里创建机器人。名称勿冒充官方、勿用 F1 商标，如“赛车周末播报”。填写隐私协议。
3. 开发设置 -> “事件订阅与回调地址” 选择 **WebSocket**。记下 AppID 和 AppSecret。
4. 使用范围：群聊设为“全量”（公开）。若提示需要审核，按提示提交；功能说明可写：
   “F1 赛程提醒与积分榜/结果查询，仅发送赛事资讯文本，每个比赛周约 10 条。”
5. 腾讯云购买轻量应用服务器：香港地域、Ubuntu 24.04、最低配置。把你的 SSH 公钥（`cat ~/.ssh/id_ed25519.pub`）加到服务器，并告诉我 IP。
6. 开放平台“开发设置 -> IP 白名单”填入服务器 IP。
7. 手机 QQ 打开机器人资料卡 -> 添加到群聊（群主或管理员均可）。再进群设置 -> 机器人，打开“机器人主动在群聊内发言”（若管理员无此权限，需群主操作一次）。
8. 群里发一次 `@机器人 下一场`，收到回复即成功（同时会让程序记录这个群）。

## 二、本地试运行（阶段 A）

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml   # 填入 appid / secret
.venv/bin/python check.py            # 应输出 OK
.venv/bin/python main.py
```

沙箱群：开放平台“沙箱配置”可指定一个 20 人以内、你是群主/管理员的群，用于未上线时测试。

## 三、部署与运维

```bash
deploy/install.sh ubuntu@服务器IP      # 首次部署与后续更新均用它
ssh ubuntu@服务器IP systemctl status f1bot
ssh ubuntu@服务器IP journalctl -u f1bot -f      # 看日志，应有 on_ready
```

部署脚本会把 SSH 改为仅密钥登录并禁止 root 登录，首次部署前先确认本机能用密钥登录 ubuntu 用户。

部署成功后务必停掉本机运行的 `main.py`，同一机器人只能有一个实例。

## 四、指令

帮助、下一场/赛程、赛历、车手积分、车队积分、结果、订阅、退订。

## 五、数据与配置

- 赛历自动获取并缓存于 `data/schedule.json`（每 12 小时刷新，失败用缓存）。
- 手动导入：把同格式 JSON 放到 `data/schedule.override.json`，优先使用。
- `config.toml` 的 `remind_minutes = [30, 10]` 可设置多次提醒。
- `alert_group` 填群 openid 后，数据源连续失败或限流、QQ 发送失败、刷屏、磁盘不足、陌生 IP 登录 SSH 时会往该群告警（见 `health.py`）；服务器宕机或断网时 bot 发不出告警，需另配云监控。
- `data/groups.json` 为订阅群，`data/sent.json` 为已发送记录（防重复）。
