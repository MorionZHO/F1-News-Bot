"""健康监测：连续失败、限流、刷屏、磁盘空间、陌生 IP 的 SSH 登录，异常时往 config 的 alert_group 发主动告警。
bot 进程卡死、服务器宕机或断网时自己发不出告警，这部分靠腾讯云云监控。"""

import asyncio
import logging
import os
import re
import shutil
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("health")
COOLDOWN = 6 * 3600  # 同一类告警 6 小时内最多发一次
LIMITS = {"fetch": 5, "send": 3, "loop": 3}  # 连续失败多少次告警
FLOOD_WINDOW, FLOOD_GROUP, FLOOD_TOTAL = 60, 20, 60  # 1 分钟内单群 / 全部群的 @ 次数上限
DISK_MIN_FREE, DISK_MIN_RATIO = 1 << 30, 0.10
CHECK_SECONDS = 60
AUTH_LOG = Path("/var/log/auth.log")  # f1bot 需在 adm 组才能读，见 deploy/install.sh
ACCEPTED = re.compile(r"Accepted (\S+) for (\S+) from (\S+)")
SOURCES = {"api.jolpi.ca": "Jolpica", "api.openf1.org": "OpenF1", "raw.githubusercontent.com": "GitHub 新闻"}  # 告警里不能出现网址，QQ 会拒发

_fails: dict[str, int] = {}
_last: dict[str, float] = {}
_pending: list[str] = []
_msgs: deque = deque()  # (时间, 群 openid)
_auth = {"ino": None, "pos": 0}


def alert(key: str, text: str) -> None:
    now = time.monotonic()
    if key in _last and now - _last[key] < COOLDOWN:
        return
    _last[key] = now
    _pending.append(text)
    log.warning("ALERT %s", text)


def fail(key: str, limit: int, text: str) -> None:
    _fails[key] = _fails.get(key, 0) + 1
    if _fails[key] >= limit:
        alert(key, f"{text}（已连续失败 {_fails[key]} 次）")


def ok(key: str) -> None:
    _fails.pop(key, None)


def fetch_done(host: str, error: Exception | None = None) -> None:
    key, name, status = f"fetch:{host}", SOURCES.get(host, "数据源"), getattr(error, "status", None)
    if error is None:
        return ok(key)
    if status == 404:  # OpenF1 查无结果（如发车格还没出）也返回 404，不算故障
        return
    if status == 429:
        alert(f"429:{host}", f"{name} 返回 429：请求过多被限流，查询会退回旧缓存")
    fail(key, LIMITS["fetch"], f"{name} 请求失败：{status or type(error).__name__}")


def send_done(where: str, error: Exception | None = None) -> None:
    if error is None:
        return ok("send")
    code = re.search(r"\d{8}", str(error))  # QQ 接口错误码均为 8 位
    fail("send", LIMITS["send"], f"QQ 消息发送失败（{where}）：{code[0] if code else type(error).__name__}")


def on_message(gid: str, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    _msgs.append((now, gid))
    while _msgs[0][0] <= now - FLOOD_WINDOW:
        _msgs.popleft()
    if sum(g == gid for _, g in _msgs) > FLOOD_GROUP:
        alert(f"flood:{gid}", f"群 {gid[:6]}… 1 分钟内 @ 超过 {FLOOD_GROUP} 次，疑似刷屏")
    if len(_msgs) > FLOOD_TOTAL:
        alert("flood", f"所有群 1 分钟内 @ 合计超过 {FLOOD_TOTAL} 次，疑似刷屏")


def check_disk(path: Path) -> None:
    u = shutil.disk_usage(path)
    if u.free < DISK_MIN_FREE or u.free < u.total * DISK_MIN_RATIO:
        alert("disk", f"存储空间不足：剩余 {u.free / (1 << 30):.1f}GB（{u.free / u.total:.0%}）")


def new_auth_lines(path: Path = AUTH_LOG) -> list[str]:
    """上次读取后新增的完整行；首次只记位置不回看历史，日志轮转（换了文件或变短）后从头读。
    ponytail: 轮转前最后不到 1 分钟写入旧文件的行会漏看。"""
    with path.open("rb") as f:
        st = os.fstat(f.fileno())
        if _auth["ino"] is None:
            _auth.update(ino=st.st_ino, pos=st.st_size)
        elif st.st_ino != _auth["ino"] or st.st_size < _auth["pos"]:
            _auth.update(ino=st.st_ino, pos=0)
        f.seek(_auth["pos"])
        data = f.read()
    data = data[:data.rfind(b"\n") + 1]  # 正在写的半行留到下次
    _auth["pos"] += len(data)
    return data.decode(errors="replace").splitlines()


def check_ssh(trusted: set[str], path: Path = AUTH_LOG) -> None:
    for line in new_auth_lines(path):
        m = ACCEPTED.search(line)
        if m and m[3] not in trusted:
            ip = m[3].replace(".", "·")  # 免得被 QQ 当成网址拒发
            alert(f"ssh:{m[3]}", f"有人从 {ip} 以 {m[2]} 身份 SSH 登录了服务器（{m[1]}），"
                                 "不是常用 IP。不是你本人请立即检查；是你本人就把该 IP 加进 ssh_trusted_ips")


async def run(cfg: dict, send) -> None:
    gid, trusted = cfg.get("alert_group"), set(cfg.get("ssh_trusted_ips", []))
    while True:
        try:
            check_disk(Path(__file__).parent)
            check_ssh(trusted)
        except Exception as e:
            log.exception("health check failed")
            alert("check", f"健康检查本身出错：{type(e).__name__}，磁盘或 SSH 登录告警可能失效")
        while _pending:
            text = _pending.pop(0)
            try:
                if gid:
                    await send(gid, f"【bot告警】{text}")
            except Exception as e:  # 告警本身发不出去只能记日志
                log.error("alert send failed: %s", e)
        await asyncio.sleep(CHECK_SECONDS)
