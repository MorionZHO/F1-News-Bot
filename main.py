"""F1 QQ 群聊机器人入口：指令路由 + 被动回复 + 启动定时推送。"""

import asyncio
import logging
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import botpy

import f1
import push

log = logging.getLogger("main")
HELP = (  # 每行尽量不超过手机气泡一行宽（约 16 个汉字）
    "F1 赛事助手 · @我 + 关键词\n"
    "\n"
    "【赛程】\n"
    "下一场　赛历\n"
    "站名 → 该站时间表，例：巴库\n"
    "\n"
    "【成绩】\n"
    "结果　排位　冲刺\n"
    "车手积分　车队积分\n"
    "加站名查指定一站，例：斯帕排位\n"
    "\n"
    "【赛道】\n"
    "站名+冠军 → 近5届冠军，例：蒙扎冠军\n"
    "\n"
    "【推送】订阅　退订\n"
    "\n"
    "站名可用国家、城市或赛道名"
)
COMMANDS = [  # 顺序即匹配优先级
    ("退订", "unsub"), ("取消", "unsub"), ("订阅", "sub"), ("帮助", "help"),
    ("车队", "constructor"), ("制造商", "constructor"), ("车手", "driver"),
    ("积分", "driver"), ("排名", "driver"), ("榜", "driver"),
    ("冲刺排位", "sprint_q"), ("冲刺", "sprint"), ("排位", "qualifying"),
    ("结果", "result"), ("上一场", "result"), ("上一站", "result"), ("成绩", "result"), ("正赛", "result"),
    ("赛历", "calendar"), ("全年", "calendar"), ("赛季", "calendar"),
    ("冠军", "winners"), ("历届", "winners"), ("往届", "winners"), ("赛道", "winners"),
    ("下一场", "next"), ("下一站", "next"), ("赛程", "next"), ("时间", "next"), ("日程", "next"),
]
NO_DATA = "数据源暂时不可用，请稍后再试。"
SESSION_CMDS = {"result": "race", "qualifying": "qualifying", "sprint": "sprint"}  # 指令 -> 结果类型
SESSION_LABELS = {"race": "正赛", "qualifying": "排位赛", "sprint": "冲刺赛"}

def parse(text: str) -> str:
    text = text.strip()
    return next((cmd for kw, cmd in COMMANDS if kw in text), "help")


class Bot(botpy.Client):
    def __init__(self, cfg: dict, state: push.State):
        super().__init__(intents=botpy.Intents(public_messages=True))
        self.cfg, self.state = cfg, state

    async def on_ready(self):
        log.info("on_ready: %s", self.robot.name)
        asyncio.create_task(push.run(self.cfg, self.state, self.send_active))

    async def send_active(self, gid: str, text: str):
        await self.api.post_group_message(group_openid=gid, msg_type=0, content=text)

    async def on_group_add_robot(self, event):
        log.info("added to group %s", event.group_openid)
        self.state.register(event.group_openid)

    async def on_group_del_robot(self, event):
        self.state.groups.pop(event.group_openid, None)
        self.state.save()

    async def on_group_msg_reject(self, event):
        self.state.set_flag(event.group_openid, no_push=True)

    async def on_group_msg_receive(self, event):
        self.state.set_flag(event.group_openid, no_push=False)

    async def on_group_at_message_create(self, message):
        gid = message.group_openid
        log.info("at message from group %s: %r", gid, message.content)
        self.state.register(gid)
        try:
            reply = await self.answer(message.content, gid)
        except Exception:
            log.exception("command failed")
            reply = NO_DATA
        await message.reply(msg_type=0, content=reply)

    async def answer(self, text: str, gid: str) -> str:
        cmd = parse(text)
        if cmd in ("sub", "unsub"):
            self.state.set_flag(gid, sub=(cmd == "sub"))
            return "已开启本群自动推送。" if cmd == "sub" else "已关闭本群自动推送。"
        return await answer_query(cmd, text)


async def race_result(race: dict, kind: str) -> str:
    field = push.RESULT_KINDS[kind][0]
    if field and field not in race:
        return f1.format_weekend(race, f"本站没有{SESSION_LABELS[kind]}，以下是时间安排：\n")
    res = await f1.fetch_session(race["round"], kind)
    if res:
        return f1.format_session(res, kind)
    label = SESSION_LABELS[kind]
    note = (f"本站{label}还没进行（或尚未开始），以下是时间安排：" if session_end(race, kind) > datetime.now(timezone.utc)
            else f"本站{label}已结束，但结果暂未更新，请稍后再试。以下是时间安排：")
    return f1.format_weekend(race, note + "\n")


async def circuit_winners(race: dict | None) -> str:
    if not race:
        return "请带上站名，例如：斯帕冠军、蒙扎历届。"
    winners = await f1.fetch_winners(race["Circuit"]["circuitId"])
    if winners is None:
        return NO_DATA
    return f"第{race['round']}站 {f1.gp_name(race)}\n{f1.format_winners(race, winners)}"


def session_end(race: dict, kind: str) -> datetime:
    field, length, _ = push.RESULT_KINDS[kind]
    return (f1.to_utc(**race[field]) if field else f1.race_start(race)) + length


def last_done(races: list[dict], kind: str) -> dict | None:
    field, now = push.RESULT_KINDS[kind][0], datetime.now(timezone.utc)
    done = [r for r in races if (not field or field in r) and session_end(r, kind) <= now]
    return done[-1] if done else None


async def answer_query(cmd: str, text: str = "") -> str:
    if cmd == "sprint_q":
        return "暂无冲刺排位赛的数据源，可查询冲刺赛、排位赛或正赛结果。"
    races = f1.load_races()
    race, kind = f1.find_race(text, races), SESSION_CMDS.get(cmd)
    if race and kind:
        return await race_result(race, kind)
    if cmd == "winners":
        return await circuit_winners(race)
    if race:
        return f1.format_weekend(race)
    if cmd == "help":
        return HELP
    if cmd in ("driver", "constructor"):
        st = await f1.fetch_standings(cmd)
        return f1.format_standings(st, cmd) if st else NO_DATA
    if kind:
        race = last_done(races, kind)
        return await race_result(race, kind) if race else f"暂无已完赛的{SESSION_LABELS[kind]}。"
    if not races:
        return NO_DATA
    if cmd == "calendar":
        return f1.format_calendar(races)
    race = f1.next_race(races, datetime.now(timezone.utc))
    return f1.format_weekend(race, "最近比赛周：") if race else "本赛季已全部结束。"


def main():
    cfg = tomllib.loads((Path(__file__).parent / "config.toml").read_text(encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", force=True)
    asyncio.set_event_loop(asyncio.new_event_loop())  # botpy 在 Python 3.14 下需要预先存在事件循环
    Bot(cfg, push.State()).run(appid=cfg["appid"], secret=cfg["secret"])


if __name__ == "__main__":
    main()
