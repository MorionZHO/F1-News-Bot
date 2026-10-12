"""定时推送：计算到期任务、群订阅状态与已发送记录（重启安全）。"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import f1
import health

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
_EXTRAS: dict[str, str] = {}  # 预告 key -> 附加内容后的完整文本
RACE_WEEK = (timedelta(days=4), timedelta(days=1))  # 正赛前 4 天到正赛后 1 天算比赛周（覆盖周四预告到周一）
SEND_GAP_SECONDS = 2
# 发车格：开赛前 60 分钟起取，失败每 5 分钟重试；开赛前 35 分钟再取一次最新版（OpenF1 在开赛前 30 分钟起对免费账号关闭）
GRID_FIRST, GRID_LAST, GRID_RETRY = timedelta(minutes=60), timedelta(minutes=35), timedelta(minutes=5)
GRID_SESSIONS = {"正赛 Race": "race", "冲刺赛": "sprint"}  # 节次名 -> f1.GRID_SOURCES 的 kind


@dataclass
class Task:
    key: str
    when: datetime
    until: datetime
    text: str
    race: dict | None = None  # 仅比赛周预告：发送时附上该赛道历届冠军和争冠形势
    grid: Path | None = None  # 仅正赛/冲刺赛开赛前 35 分钟内的提醒：发送时附上已缓存的发车格


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
    # 记录 key 带上节次开始时间：赛程调整后视为新任务，按新时间重新提醒
    rid, sess = f"{race['season']}-{race['round']}", f1.sessions(race)
    tasks = [Task(f"{rid}-preview-{sess[0][1]:%Y%m%dT%H%M}", preview_time(race), sess[0][1],
                  f1.format_weekend(race, "本周比赛周："), race)]
    for name, start in sess:
        for m in remind_minutes:
            text = f1.format_reminder(race, name, start, m)
            grid = grid_file(race, GRID_SESSIONS[name]) if name in GRID_SESSIONS and \
                timedelta(minutes=m) <= GRID_LAST else None
            tasks.append(Task(f"{rid}-{name}-{m}-{start:%Y%m%dT%H%M}", start - timedelta(minutes=m),
                              start - timedelta(minutes=m) + GRACE, text, grid=grid))
    return tasks


def grid_file(race: dict, kind: str) -> Path:
    return f1.DATA / f"grid-{race['season']}-{race['round']}-{kind}.json"


def grid_phase(start: datetime, now: datetime, cached: bool, last_try: datetime | None,
               final_done: bool) -> str | None:
    """该不该现在取发车格："first"=开赛前 60~35 分钟还没缓存、距上次尝试满 5 分钟；
    "final"=开赛前 35~30 分钟取一次最新版；其余 None。"""
    if start - GRID_FIRST <= now < start - GRID_LAST:
        return "first" if not cached and (last_try is None or now - last_try >= GRID_RETRY) else None
    if start - GRID_LAST <= now < start - timedelta(minutes=30):
        return None if final_done else "final"
    return None


def grid_jobs(races: list[dict], now: datetime) -> list[tuple[dict, str, datetime]]:
    """开赛前 60 分钟内的正赛/冲刺赛：(race, kind, 开始时间)。"""
    return [(r, GRID_SESSIONS[name], start) for r in races for name, start in f1.sessions(r)
            if name in GRID_SESSIONS and start - GRID_FIRST <= now < start]


async def update_grids(races: list[dict], now: datetime, tries: dict, final: set) -> None:
    """取到且与缓存不同才覆盖；取不到就保留之前的缓存。"""
    for race, kind, start in grid_jobs(races, now):
        path = grid_file(race, kind)
        phase = grid_phase(start, now, path.exists(), tries.get(path), path in final)
        if not phase:
            continue
        tries[path] = now
        if phase == "final":
            final.add(path)
        try:
            names = await f1.fetch_grid(race, kind)
        except Exception:  # 接口格式异常不能拖累同一轮的提醒推送
            log.exception("grid %s failed", path.name)
            names = None
        log.info("grid %s %s: %s", path.name, phase, "ok" if names else "unavailable")
        if names and names != f1._read(path):
            path.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")


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
            health.send_done("推送")
        except Exception as e:
            log.error("push %s to %s failed: %s", key, gid, e)
            if "40034105" in str(e):  # 群主关了主动消息，属于群设置，不算故障
                state.groups[gid]["no_push"] = True
            else:
                health.send_done("推送", e)
        state.save()
        await asyncio.sleep(SEND_GAP_SECONDS)


async def with_extras(t: Task, races: list[dict]) -> str:
    """比赛周预告依次附赛道历届冠军、争冠形势；正赛/冲刺赛提醒附发车格。取不到数据就略过该部分。"""
    if t.grid:
        names = f1._read(t.grid)
        return f"{t.text}\n\n{f1.format_grid(names)}" if names else t.text
    if not t.race:
        return t.text
    if t.key in _EXTRAS:  # 有群发送失败时每轮都会重试，避免每 30 秒重复请求接口
        return _EXTRAS[t.key]
    parts = [t.text]
    winners = await f1.fetch_winners(t.race["Circuit"]["circuitId"])
    if winners is not None:
        parts.append(f1.format_winners(t.race, winners))
    st = await f1.fetch_standings("driver")
    if st:
        parts.append(f1.format_clinch_preview(st, races, t.race))
    _EXTRAS[t.key] = "\n\n".join(p for p in parts if p)
    return _EXTRAS[t.key]


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


def refresh_interval(races: list[dict], now: datetime) -> timedelta:
    before, after = RACE_WEEK
    in_week = any(f1.race_start(r) - before <= now <= f1.race_start(r) + after for r in races)
    return timedelta(hours=3 if in_week else 12)


async def run(cfg: dict, state: State, send) -> None:
    """主循环：每 30 秒检查一次到期任务；赛历缓存比赛周超过 3 小时、平时超过 12 小时则刷新。"""
    tried, last_refresh, grid_tries, grid_final = set(), None, {}, set()
    while True:
        now = datetime.now(timezone.utc)
        try:
            if last_refresh is None or now - last_refresh > refresh_interval(f1.load_races(), now):
                await f1.refresh_schedule()
                last_refresh = now
            races = f1.load_races()
            await update_grids(races, now, grid_tries, grid_final)
            for t in due_tasks(races, now, cfg["remind_minutes"], state):
                await push_all(state, t.key, await with_extras(t, races), send)
            for race in races:
                for kind, key, n in due_attempts(race, now, state, tried):
                    tried.add((key, n))
                    await poll_result(race, kind, key, state, send)
            health.ok("loop")
        except Exception as e:
            log.exception("push loop error")
            health.fail("loop", health.LIMITS["loop"], f"推送主循环出错：{type(e).__name__}，提醒和结果推送可能中断")
        await asyncio.sleep(TICK_SECONDS)
