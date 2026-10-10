"""定时推送：计算到期任务、群订阅状态与已发送记录（重启安全）。"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import f1

log = logging.getLogger("push")
GRACE = timedelta(minutes=10)
ATTEMPT_GRACE = timedelta(hours=1)  # 程序重启或休眠后，错过不足 1h 的尝试仍会补做
# 结果类型 -> (赛历节次字段, 预计时长, 完赛后各次尝试的间隔小时数)；练习赛和冲刺排位无数据源，不推送
# ponytail: 时长是估算值（排位 1h、冲刺 0.5h、正赛 2h），实际完赛时间差异会让尝试略早或略晚
RESULT_KINDS = {
    "sprint": ("Sprint", timedelta(minutes=30), (3, 6, 12)),
    "qualifying": ("Qualifying", timedelta(hours=1), (3, 6, 12)),
    "race": (None, timedelta(hours=2), (6, 12, 24)),
}
TICK_SECONDS = 30
SEND_GAP_SECONDS = 2


@dataclass
class Task:
    key: str
    when: datetime
    until: datetime
    text: str


class State:
    """groups.json: {gid: {sub, no_push}}；sent.json: {task_key: [gid, ...]}。"""

    def __init__(self, directory=f1.DATA):
        directory.mkdir(exist_ok=True)
        self.gp, self.sp = directory / "groups.json", directory / "sent.json"
        self.groups = self._load(self.gp)
        self.sent = self._load(self.sp)

    @staticmethod
    def _load(path):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def save(self):
        self.gp.write_text(json.dumps(self.groups, ensure_ascii=False), encoding="utf-8")
        self.sp.write_text(json.dumps(self.sent), encoding="utf-8")

    def register(self, gid: str):
        if gid not in self.groups:
            self.groups[gid] = {"sub": True, "no_push": False}
            self.save()

    def set_flag(self, gid: str, **flags):
        self.register(gid)
        self.groups[gid].update(flags)
        self.save()

    def targets(self, key: str) -> list[str]:
        done = self.sent.get(key, [])
        return [g for g, v in self.groups.items() if v["sub"] and g not in done]


def preview_time(race: dict) -> datetime:
    """周四 20:00（北京时间）与一练前 12 小时取较早者。"""
    first = f1.sessions(race)[0][1].astimezone(f1.BJT)
    thursday = (first + timedelta(days=3 - first.weekday())).replace(
        hour=20, minute=0, second=0, microsecond=0
    )
    return min(thursday, first - timedelta(hours=12))


def build_tasks(race: dict, remind_minutes: list[int]) -> list[Task]:
    rid, sess = f"{race['season']}-{race['round']}", f1.sessions(race)
    tasks = [Task(f"{rid}-preview", preview_time(race), sess[0][1],
                  f1.format_weekend(race, "本周比赛周：") )]
    for name, start in sess:
        for m in remind_minutes:
            text = f1.format_reminder(race, name, start, m)
            tasks.append(Task(f"{rid}-{name}-{m}", start - timedelta(minutes=m),
                              start - timedelta(minutes=m) + GRACE, text))
    return tasks


def due_tasks(races: list[dict], now: datetime, remind_minutes: list[int], state: State) -> list[Task]:
    out = []
    for race in races:
        if abs((f1.race_start(race) - now).days) > 10:
            continue
        out += [t for t in build_tasks(race, remind_minutes)
                if t.when <= now <= t.until and state.targets(t.key)]
    return out


def result_attempts(race: dict) -> list[tuple[str, str, int, datetime]]:
    """该站所有结果尝试：(kind, 记录key, 第几次, 尝试时间)。"""
    out = []
    for kind, (field, length, delays) in RESULT_KINDS.items():
        if field and field not in race:
            continue
        end = (f1.to_utc(**race[field]) if field else f1.race_start(race)) + length
        for n, hours in enumerate(delays):
            out.append((kind, f"{race['season']}-{race['round']}-{kind}-result", n,
                        end + timedelta(hours=hours)))
    return out


def due_attempts(race: dict, now: datetime, state: State, tried: set) -> list[tuple[str, str, int]]:
    return [(kind, key, n) for kind, key, n, when in result_attempts(race)
            if when <= now <= when + ATTEMPT_GRACE and (key, n) not in tried and state.targets(key)]


async def push_all(state: State, key: str, text: str, send) -> None:
    """逐群发送；失败的群不记入 sent，下一轮重试（仍受到期窗口限制）。"""
    for gid in state.targets(key):
        try:
            await send(gid, text)
            state.sent.setdefault(key, []).append(gid)
        except Exception as e:
            log.error("push %s to %s failed: %s", key, gid, e)
            if "40034105" in str(e):
                state.groups[gid]["no_push"] = True
        state.save()
        await asyncio.sleep(SEND_GAP_SECONDS)


async def poll_result(race: dict, kind: str, key: str, state: State, send) -> None:
    result = await f1.fetch_session(race["round"], kind)
    if not result:
        return
    text = f1.format_session(result, kind)
    if kind == "race":
        standings = await f1.fetch_standings("driver")
        if standings:
            text += "\n\n" + f1.format_standings(standings, "driver", top=5)
    await push_all(state, key, text, send)


async def run(cfg: dict, state: State, send) -> None:
    """主循环：每 30 秒检查一次到期任务；赛历缓存超过 12 小时则刷新。"""
    tried, last_refresh = set(), None
    while True:
        now = datetime.now(timezone.utc)
        try:
            if last_refresh is None or now - last_refresh > timedelta(hours=12):
                await f1.refresh_schedule()
                last_refresh = now
            races = f1.load_races()
            for t in due_tasks(races, now, cfg["remind_minutes"], state):
                await push_all(state, t.key, t.text, send)
            for race in races:
                for kind, key, n in due_attempts(race, now, state, tried):
                    tried.add((key, n))
                    await poll_result(race, kind, key, state, send)
        except Exception:
            log.exception("push loop error")
        await asyncio.sleep(TICK_SECONDS)
