#!/usr/bin/env bash
# 用法（在本机项目根目录）: deploy/install.sh ubuntu@服务器IP
# 首次部署前，需先把 config.toml 放在项目根目录；之后重复执行即为更新。
# 登录用户需要有免密 sudo（腾讯云 Ubuntu 的 ubuntu 用户默认有；root 也可）。
set -euo pipefail
HOST="${1:?用法: deploy/install.sh user@host}"
cd "$(dirname "$0")/.."
[ -f config.toml ] || { echo "缺少 config.toml"; exit 1; }
.venv/bin/python check.py

ssh "$HOST" 'sudo bash -s' <<'EOF'
set -e
id f1bot >/dev/null 2>&1 || useradd -r -m -s /usr/sbin/nologin f1bot
usermod -aG adm f1bot  # 读 /var/log/auth.log，做 SSH 登录告警
# 只允许密钥登录、禁止 root 登录；00- 前缀让它排在 cloud-init 的 PasswordAuthentication yes 之前生效
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' \
  > /etc/ssh/sshd_config.d/00-f1bot-harden.conf
sshd -t && systemctl reload ssh
mkdir -p /opt/f1bot && chown ubuntu:ubuntu /opt/f1bot 2>/dev/null || true
dpkg -s python3-venv rsync >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq python3-venv rsync)
EOF
rsync -az --delete --rsync-path="sudo rsync" \
  --exclude .venv --exclude data --exclude __pycache__ --exclude .git \
  ./ "$HOST:/opt/f1bot/"
ssh "$HOST" 'sudo bash -s' <<'EOF'
set -e
cd /opt/f1bot
chown -R f1bot:f1bot . && chmod 600 config.toml
[ -d .venv ] || sudo -u f1bot python3 -m venv .venv
sudo -u f1bot .venv/bin/pip install -q -r requirements.txt
cp deploy/f1bot.service /etc/systemd/system/f1bot.service
systemctl daemon-reload && systemctl enable f1bot && systemctl restart f1bot
sleep 10
systemctl is-active f1bot
journalctl -u f1bot -n 15 --no-pager
EOF
