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
HELP = (
    "F1 赛事助手，@我并发送：\n"
    "下一场 / 赛程：最近比赛周时间表\n"
    "赛历：全年赛程\n"
    "站名/城市/国家（如 阿布扎比、巴库、上海）：该站比赛周时间表\n"
    "车手积分 / 车队积分：积分榜\n"
    "结果：最近一场正赛结果；加站名（如 比利时结果、斯帕正赛）查指定一站\n"
    "订阅 / 退订：开关本群自动推送"
)
COMMANDS = [  # 顺序即匹配优先级
    ("退订", "unsub"), ("取消", "unsub"), ("订阅", "sub"), ("帮助", "help"),
    ("车队", "constructor"), ("制造商", "constructor"), ("车手", "driver"),
    ("积分", "driver"), ("排名", "driver"), ("榜", "driver"),
    ("结果", "result"), ("上一场", "result"), ("上一站", "result"), ("成绩", "result"), ("正赛", "result"),
    ("赛历", "calendar"), ("全年", "calendar"), ("赛季", "calendar"),
    ("下一场", "next"), ("下一站", "next"), ("赛程", "next"), ("时间", "next"), ("日程", "next"),
]
NO_DATA = "数据源暂时不可用，请稍后再试。"

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


async def race_result(race: dict) -> str:
    res = await f1.fetch_results(race["round"])
    if res:
        return f1.format_results(res)
    pending = f1.race_start(race) + f1.RACE_LEN > datetime.now(timezone.utc)
    note = "该比赛还没进行（或尚未开始），以下是时间安排：" if pending else "该比赛已结束，但结果暂未更新，请稍后再试。以下是时间安排："
    return f1.format_weekend(race, note + "\n")


async def answer_query(cmd: str, text: str = "") -> str:
    race = f1.find_race(text, f1.load_races())
    if race and cmd == "result":
        return await race_result(race)
    if race:
        return f1.format_weekend(race)
    if cmd == "help":
        return HELP
    if cmd in ("driver", "constructor"):
        st = await f1.fetch_standings(cmd)
        return f1.format_standings(st, cmd) if st else NO_DATA
    if cmd == "result":
        res = await f1.fetch_results("last")
        return f1.format_results(res) if res else "暂无已完赛的正赛结果。"
    races = f1.load_races()
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
