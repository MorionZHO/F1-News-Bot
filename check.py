"""自检：python check.py，全部 assert 通过即输出 OK。"""

import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import f1
import health
import main
import news
import push


def race(rnd, name, date, time, **sess):
    r = {"season": "2026", "round": rnd, "raceName": name, "date": date, "time": time,
         "Circuit": {"Location": {"locality": "X"}}}
    r.update({k: dict(zip(("date", "time"), v)) for k, v in sess.items()})
    return r


def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


BAKU = race("15", "Azerbaijan Grand Prix", "2026-09-26", "11:00:00Z",
            FirstPractice=("2026-09-24", "08:30:00Z"), SecondPractice=("2026-09-24", "12:00:00Z"),
            ThirdPractice=("2026-09-25", "08:30:00Z"), Qualifying=("2026-09-25", "12:00:00Z"))
SINGAPORE = race("17", "Singapore Grand Prix", "2026-10-11", "12:00:00Z",
                 FirstPractice=("2026-10-09", "08:30:00Z"), Qualifying=("2026-10-10", "13:00:00Z"),
                 Sprint=("2026-10-10", "09:00:00Z"), SprintQualifying=("2026-10-09", "12:30:00Z"))
AUSTIN = race("18", "United States Grand Prix", "2026-10-25", "20:00:00Z",
              FirstPractice=("2026-10-23", "17:30:00Z"), Qualifying=("2026-10-24", "21:00:00Z"))


def check_preview_time():
    # 巴库一练在北京时间周四 16:30，预告必须早于它 12 小时，而不是周四 20:00
    assert push.preview_time(BAKU) == utc("2026-09-23T20:30:00")
    # 新加坡一练在周五，预告为周四 20:00 北京时间 = 12:00 UTC
    assert push.preview_time(SINGAPORE) == utc("2026-10-08T12:00:00")
    # 周一 12:00 北京时间 = 04:00 UTC；美国站一练在北京时间周六凌晨，仍属同一个自然周
    assert push.welcome_time(SINGAPORE) == utc("2026-10-05T04:00:00")
    assert push.welcome_time(AUSTIN) == utc("2026-10-19T04:00:00")
    # 周四预告开头：新加坡一练周五 → 明日；美国站一练北京时间周六凌晨 → 后天；巴库预告在周四凌晨 04:30、一练当天 16:30 → 今日
    assert push.preview_head(SINGAPORE) == "明日（周五）进入本周赛程：\n"
    assert push.preview_head(AUSTIN) == "后天（周六）进入本周赛程：\n"
    assert push.preview_head(BAKU) == "今日（周四）进入本周赛程：\n"
    t = next(t for t in push.build_tasks(AUSTIN, [30]) if "welcome" in t.key)
    assert t.text.startswith("欢迎来到比赛周！\n第18站") and t.race is AUSTIN and t.when < t.until, t.text


def check_sprint_order():
    names = [n for n, _ in f1.sessions(SINGAPORE)]
    assert names == ["一练 FP1", "冲刺排位 SQ", "冲刺赛", "排位赛 Quali", "正赛 Race"], names


def check_timezone():
    assert "周一 10-26 04:00" in f1.format_weekend(AUSTIN)
    assert "周四" in f1.fmt_time(utc("2026-09-24T08:30:00"))


def check_dedupe_and_catchup():
    with tempfile.TemporaryDirectory() as d:
        st = push.State(Path(d))
        st.register("g1")
        t0 = utc("2026-10-11T11:30:00")  # 正赛前 30 分钟
        due = push.due_tasks([SINGAPORE], t0 + timedelta(minutes=5), [30], st)
        assert [t.key for t in due] == ["2026-17-正赛 Race-30-20261011T1200"], due
        st.sent[due[0].key] = ["g1"]
        assert not push.due_tasks([SINGAPORE], t0 + timedelta(minutes=5), [30], st)
        assert not push.due_tasks([SINGAPORE], t0 + timedelta(minutes=11), [30], st)
        # 正赛推迟 1 小时：已发过的旧提醒不挡新时间的提醒
        moved = dict(SINGAPORE, time="13:00:00Z")
        due = push.due_tasks([moved], t0 + timedelta(hours=1, minutes=5), [30], st)
        assert [t.key for t in due] == ["2026-17-正赛 Race-30-20261011T1300"], due
        st.groups["g1"]["sub"] = False
        assert not st.targets("any-key")


def check_grid():
    start = f1.race_start(SINGAPORE)
    at = lambda m: start - timedelta(minutes=m)
    assert push.grid_phase(start, at(61), False, None, False) is None  # 太早
    assert push.grid_phase(start, at(60), False, None, False) == "first"
    assert push.grid_phase(start, at(57), False, at(60), False) is None  # 失败后不到 5 分钟
    assert push.grid_phase(start, at(55), False, at(60), False) == "first"  # 满 5 分钟重试
    assert push.grid_phase(start, at(50), True, at(60), False) is None  # 已有缓存不再取
    assert push.grid_phase(start, at(35), True, at(60), False) == "final"  # 35 分钟必取最新版
    assert push.grid_phase(start, at(33), True, at(35), True) is None  # 最新版只取一次
    assert push.grid_phase(start, at(30), False, None, False) is None  # 30 分钟起 OpenF1 关闭
    # 只有正赛/冲刺赛、且开赛前 35 分钟内的提醒附发车格
    tasks = {t.key: t for t in push.build_tasks(SINGAPORE, [60, 30])}
    with_grid = sorted(k for k, t in tasks.items() if t.grid)
    assert with_grid == ["2026-17-冲刺赛-30-20261010T0900", "2026-17-正赛 Race-30-20261011T1200"], with_grid
    assert tasks["2026-17-正赛 Race-30-20261011T1200"].grid.name == "grid-2026-17-race.json"
    assert f1.format_grid(["维斯塔潘", "勒克莱尔", "劳森", "安东内利", "周冠宇"]) == \
        "发车顺序（维修区起步不单独标注）：\n" \
        "　１ 维斯塔潘　　２ 勒克莱尔\n" \
        "　３ 劳森　　　　４ 安东内利\n" \
        "　５ 周冠宇"


def check_refresh_interval():
    races = [SINGAPORE, AUSTIN]  # 新加坡正赛 10-11 12:00 UTC
    assert push.refresh_interval(races, utc("2026-10-07T12:00:00")) == timedelta(hours=3)  # 正赛前 4 天
    assert push.refresh_interval(races, utc("2026-10-12T12:00:00")) == timedelta(hours=3)  # 正赛后 1 天
    assert push.refresh_interval(races, utc("2026-10-15T12:00:00")) == timedelta(hours=12)


def check_result_window():
    with tempfile.TemporaryDirectory() as d:
        st = push.State(Path(d))
        st.register("g1")
        start = f1.race_start(SINGAPORE)
        att = push.result_attempts(SINGAPORE)
        assert {a[0] for a in att} == {"sprint", "qualifying", "race"} and len(att) == 9
        assert {a[0] for a in push.result_attempts(AUSTIN)} == {"qualifying", "race"}  # 无冲刺赛
        # 正赛：预计 2h 完赛，完赛后 6h、12h、24h 各试一次，共 3 次，之后放弃
        assert [a[3] for a in att if a[0] == "race"] == [start + timedelta(hours=h) for h in (8, 14, 26)]
        sq = f1.to_utc(**SINGAPORE["Qualifying"])  # 排位赛：预计 1h，完赛后 3h、6h、12h
        assert [a[3] for a in att if a[0] == "qualifying"] == [sq + timedelta(hours=h) for h in (4, 7, 13)]
        tried = set()
        assert not push.due_attempts(SINGAPORE, start + timedelta(hours=7), st, tried)
        due = push.due_attempts(SINGAPORE, start + timedelta(hours=8, minutes=1), st, tried)
        assert [(d[0], d[2]) for d in due] == [("race", 0)], due
        tried.add((due[0][1], 0))
        assert not push.due_attempts(SINGAPORE, start + timedelta(hours=8, minutes=2), st, tried)
        assert not push.due_attempts(SINGAPORE, start + timedelta(hours=30), st, tried)


def check_parse():
    cases = {"下一场": "next", " 车手积分": "driver", "车队积分": "constructor", "退订": "unsub",
             "订阅": "sub", "测试推送": "help", "上一场": "result", "赛历": "calendar", "你好": "help",
             "车手排名": "driver", "车手积分榜": "driver", "积分榜": "driver", "排名": "driver",
             "车队": "constructor", "车队积分": "constructor", "车队排名": "constructor",
             "车队积分榜": "constructor", "取消订阅": "unsub", "下一站": "next",
             "斯帕排位": "qualifying", "排位结果": "qualifying", "迈阿密冲刺赛结果": "sprint",
             "冲刺排位": "sprint_q", "比利时正赛": "result", "斯帕冠军": "winners",
             "蒙扎历届冠军": "winners", "往届": "winners", "ANT夺冠条件": "title", "WDC": "title",
             "争冠形势": "title", "冠军条件": "title", "提前夺冠": "title", "锁定冠军条件": "title",
             "WDC条件": "title", "拉塞尔夺冠条件": "title", "斯帕冠军": "winners",
             "今年所有剩余比赛日期安排": "remaining", "剩余赛程": "remaining", "还剩几站": "remaining",
             "接下来的比赛时间": "remaining", "积分": "driver"}
    for text, cmd in cases.items():
        assert main.parse(text) == cmd, (text, main.parse(text))


def check_find_race():
    spain = race("14", "Spanish Grand Prix", "2026-09-13", "13:00:00Z")
    bcn = race("7", "Barcelona Grand Prix", "2026-06-14", "13:00:00Z")
    china = race("2", "Chinese Grand Prix", "2026-03-15", "07:00:00Z")
    monza = race("13", "Italian Grand Prix", "2026-09-06", "13:00:00Z")
    spa = race("10", "Belgian Grand Prix", "2026-07-19", "13:00:00Z")
    races = [china, bcn, spain, BAKU, monza, spa]
    cases = {"蒙扎": monza, "Monza": monza, "斯帕": spa, "SPA结果": spa, "蒙特梅罗": bcn,"巴库": BAKU, " 西班牙 ": spain, "上海": china, "巴塞罗那": bcn,
             "阿塞拜疆大奖赛": BAKU, "R7": bcn, "你好": None}
    for text, want in cases.items():
        assert f1.find_race(text, races) is want, text


def _row(code, family, pts, wins):
    return {"points": str(pts), "wins": str(wins), "Driver": {"code": code, "givenName": "", "familyName": family}}


def _brute(me_pts, me_w, r_pts, r_w, sprint, after):
    """穷举两人在本站冲刺赛和之后每个比赛周的全部名次组合，返回与 clinch_requirements 同格式的 [(p, 门槛名次)]。
    判定按官方规则：总分高者胜；同分比正赛冠军数（冲刺赛不计）；冠军数也相同按未锁定。"""
    def pairs(n):  # 两人名次组合，n+1 表示无积分（可并列）
        return [(a, b) for a in range(1, n + 2) for b in range(1, n + 2) if a != b or a == n + 1]
    sp = lambda pos: f1.SPRINT_POINTS[pos - 1] if pos <= len(f1.SPRINT_POINTS) else 0
    race_d = {(f1.points_for(a) - f1.points_for(b), (a == 1) - (b == 1)) for a, b in pairs(10)}
    sprint_d = {(sp(a) - sp(b), 0) for a, b in pairs(8)}
    fut = {(0, 0)}  # (me 比对手多得的分, me 比对手多的正赛冠军)
    for weekend in ([sprint_d] if sprint else []) + [d for has in after for d in ([race_d, sprint_d] if has else [race_d])]:
        fut = {(d1 + d2, w1 + w2) for d1, w1 in fut for d2, w2 in weekend}
    worst = {}
    for d, w in fut:
        worst[d] = min(w, worst.get(d, w))
    out = []
    for p in range(1, 12):
        bad = [q for q in range(1, 12) if (q != p or p == 11) and not all(
            g > 0 or (g == 0 and me_w + (p == 1) - r_w - (q == 1) + w > 0)
            for d, w in worst.items() for g in [me_pts + f1.points_for(p) - r_pts - f1.points_for(q) + d])]
        if 11 in bad:
            break
        out.append((p, max(bad) + 1 if bad else None))
    return out


def check_title_bruteforce():
    import random
    rnd = random.Random(0)
    for _ in range(150):
        after = [rnd.random() < 0.3 for _ in range(rnd.randint(0, 2))]  # 之后每站是否有冲刺赛
        sprint = rnd.random() < 0.3
        me_pts, me_w = rnd.randint(0, 150), rnd.randint(0, 6)
        r_pts, r_w = max(0, me_pts - rnd.randint(-10, 90)), rnd.randint(0, 6)
        me, rv = _row("AAA", "Me", me_pts, me_w), _row("BBB", "Rival", r_pts, r_w)
        nxt = race("1", "X", "2026-01-01", "00:00:00Z", **({"Sprint": ("2026-01-01", "00:00:00Z")} if sprint else {}))
        later = [race("2", "X", "2026-01-01", "00:00:00Z", **({"Sprint": ("2026-01-01", "00:00:00Z")} if h else {}))
                 for h in after]
        got = [(pp, (11 if c[0].endswith("未得分") else int(c[0].split("第")[1].split("名")[0])) if c else None)
               for pp, c in f1.clinch_requirements(me, [me, rv], nxt, later)]
        want = _brute(me_pts, me_w, r_pts, r_w, sprint, after)
        assert got == want, (me_pts, me_w, r_pts, r_w, sprint, after, got, want)


def check_title():
    rows = [_row("ANT", "Antonelli", 340, 8), _row("RUS", "Russell", 238, 3),
            _row("HAM", "Hamilton", 229, 1), _row("VER", "Verstappen", 221, 2)]
    st = {"round": "17", "DriverStandings": rows}
    left = [race(str(r), "Spanish Grand Prix", "2026-11-01", "12:00:00Z") for r in range(18, 24)]  # 6 站无冲刺
    out = f1.format_title_outlook(st, left)
    # 赢下本站得 365；拉塞尔第 9（2 分）时同分 365，对手需后 5 站全胜才追平，冠军数 3+5=8 < 9，仍锁定
    assert "· 第1名：拉塞尔第9名或更后、汉密尔顿第5名或更后" in out, out
    assert "· 第2名" not in out and out.endswith("（同分按胜场数判定）"), out
    few_wins = dict(st, DriverStandings=[_row("ANT", "Antonelli", 340, 3)] + rows[1:])
    assert "拉塞尔第10名或更后" in f1.format_title_outlook(few_wins, left)  # 冠军数 4 < 8，同分不算锁定
    sprint = [dict(left[0], Sprint={"date": "2026-10-24", "time": "09:00:00Z"})] + left[1:]
    assert "拉塞尔第9名或更后" not in f1.format_title_outlook(st, sprint)  # 冲刺赛按最坏情况多算 8 分
    late = dict(st, round="22")  # 只剩 1 站：340 > 238+25
    assert "已提前夺冠" in f1.format_title_outlook(late, left[-1:])
    assert "已无夺冠可能" in f1.format_title_outlook(late, left[-1:], rows[3])
    assert f1.find_driver("ant夺冠", rows) is rows[0]
    # 领先更多时：条件相同的相邻名次合并，“第11名”显示为未得分
    lead = f1.format_title_outlook(dict(st, DriverStandings=[_row("ANT", "Antonelli", 380, 8)] + rows[1:]), left)
    assert "· 第1-5名：无需对手配合\n· 第6-9名：拉塞尔第2名或更后\n· 第10名及更后：拉塞尔第3名或更后" in lead, lead
    lead = f1.format_title_outlook(dict(st, DriverStandings=[_row("ANT", "Antonelli", 360, 8)] + rows[1:]), left)
    assert "· 第8名：拉塞尔未得分、" in lead and "第11名" not in lead, lead


def check_clinch_preview():
    rows = [_row("ANT", "Antonelli", 340, 8), _row("RUS", "Russell", 238, 3),
            _row("HAM", "Hamilton", 229, 1), _row("VER", "Verstappen", 221, 2)]
    st = {"round": "17", "DriverStandings": rows}
    left = [race(str(r), "Spanish Grand Prix", "2026-11-01", "12:00:00Z") for r in range(18, 24)]
    pre = f1.format_clinch_preview(st, left, left[0])
    assert pre.startswith("【争冠】\n安东内利本站锁定车手总冠军的条件：") and pre.endswith("（同分按胜场数判定）"), pre
    assert f1.format_clinch_preview(dict(st, round="16"), left, left[0]) == ""  # 积分榜过期
    season = [race(str(r), "Spanish Grand Prix", "2026-05-01", "12:00:00Z") for r in range(3, 24)]
    assert f1.format_clinch_preview(dict(st, round="2"), season, season[0]) == ""  # 赛季前半段
    # 最后一站：冠军已锁定，亚军在拉塞尔和汉密尔顿之间争
    final = {"round": "22", "DriverStandings": [_row("ANT", "Antonelli", 400, 8), _row("RUS", "Russell", 300, 3),
                                                _row("HAM", "Hamilton", 290, 1), _row("VER", "Verstappen", 200, 2)]}
    pre = f1.format_clinch_preview(final, left, left[-1])
    assert pre.startswith("【争冠】\n安东内利已锁定车手总冠军\n拉塞尔本站锁定车手总亚军的条件："), pre
    assert "汉密尔顿本站锁定车手总亚军的条件：" in pre and "季军" not in pre, pre
    # 冠亚已锁定，算季军
    final["DriverStandings"][1] = _row("RUS", "Russell", 350, 3)
    pre = f1.format_clinch_preview(final, left, left[-1])
    assert "安东内利已锁定车手总冠军\n拉塞尔已锁定车手总亚军\n汉密尔顿已锁定车手总季军" not in pre
    final["DriverStandings"][3] = _row("VER", "Verstappen", 280, 2)
    pre = f1.format_clinch_preview(final, left, left[-1])
    assert "拉塞尔已锁定车手总亚军\n汉密尔顿本站锁定车手总季军的条件：" in pre, pre
    # 前三都已确定
    final["DriverStandings"][3] = _row("VER", "Verstappen", 200, 2)
    pre = f1.format_clinch_preview(final, left, left[-1])
    assert pre == "【争冠】\n车手总排名前三已确定：冠军 安东内利、亚军 拉塞尔、季军 汉密尔顿", pre


def check_winners():
    def win(season, name, family):
        return {"season": season, "raceName": name, "Results": [{
            "Driver": {"givenName": "", "familyName": family}, "Constructor": {"name": "Red Bull"}}]}
    ws = [win(str(y), "Malaysian Grand Prix", "Vettel") for y in range(2010, 2017)]
    ws.append(win("2026", "Bahrain Grand Prix in Malaysia", "Verstappen"))
    lines = f1.format_winners(race("16", "Bahrain Grand Prix in Malaysia", "2026-10-04", "07:00:00Z"), ws).split("\n")
    assert lines[0] == "本赛道近5届冠军：" and len(lines) == 6, lines
    assert lines[1] == "2026 维斯塔潘（红牛）", lines[1]  # 最新在前，同名站不标注
    assert lines[2] == "2016 维特尔（红牛） [马来西亚大奖赛]", lines[2]
    assert f1.format_winners(BAKU, []) == "本赛道此前未举办过F1大奖赛。"


def check_session_results():
    def res(pos, family, pts, status="Finished", laps="19"):
        return {"position": pos, "positionText": pos, "points": str(pts), "status": status, "laps": laps,
                "Driver": {"givenName": "", "familyName": family}, "Constructor": {"name": "Red Bull"}}
    rows = [res(str(i), f"D{i}", p) for i, p in enumerate([8, 7, 6, 5, 4, 3, 2, 1, 0], 1)]
    rows += [res("10", "Albon", 0, "Retired", "18"), res("R", "Stroll", 0, "Retired", "3"),
             res("W", "Gasly", 0, "Did not start", "0"), res("D", "Ocon", 0, "Disqualified")]
    lines = f1.format_session({"raceName": "Singapore Grand Prix", "Results": rows}, "sprint").split("\n")
    assert lines[0] == "新加坡大奖赛 冲刺赛结果（前8）" and lines[8].startswith("8. "), lines
    assert lines[9:] == ["", "【退赛】", "阿尔本（完成18圈）、斯特罗尔（完成3圈）",
                         "", "【未起步】", "加斯利", "", "【取消资格】", "奥康"], lines[9:]
    rows[0]["FastestLap"] = {"rank": "1", "Time": {"time": "1:35.202"}}
    text = f1.format_session({"raceName": "Singapore Grand Prix", "Results": rows}, "race")
    assert "\n\n【最快圈速】\n D1（红牛） 1:35.202\n\n【退赛】\n" in text, text


def check_health():
    class HTTPError(Exception):
        def __init__(self, status):
            self.status = status
    reset = lambda: [d.clear() for d in (health._fails, health._last, health._pending, health._msgs)]
    reset()
    for _ in range(4):
        health.fetch_done("api.jolpi.ca", TimeoutError())
    health.fetch_done("api.jolpi.ca", HTTPError(404))  # 查无结果不算故障，也不清零
    assert not health._pending
    health.fetch_done("api.jolpi.ca", TimeoutError())
    assert health._pending == ["Jolpica 请求失败：TimeoutError（已连续失败 5 次）"], health._pending
    health.fetch_done("api.jolpi.ca", TimeoutError())  # 6 小时冷却内不重复告警
    health.fetch_done("api.jolpi.ca")
    assert len(health._pending) == 1 and "fetch:api.jolpi.ca" not in health._fails
    health.fetch_done("api.openf1.org", HTTPError(429))  # 限流立即告警
    assert health._pending[-1].startswith("OpenF1 返回 429") and len(health._pending) == 2
    reset()
    for _ in range(3):
        health.send_done("推送", Exception("code: 22009, err_code 40054010"))
    assert health._pending == ["QQ 消息发送失败（推送）：40054010（已连续失败 3 次）"], health._pending

    reset()
    for i in range(21):
        health.on_message("BF0169960B9D", now=i * 4.0)  # 80 秒 21 条，任一分钟都不超 20
    assert not health._pending
    for i in range(21):
        health.on_message("BF0169960B9D", now=100 + i)
    assert health._pending == ["群 BF0169… 1 分钟内 @ 超过 20 次，疑似刷屏"], health._pending

    reset()
    health._auth.update(ino=None, pos=0)
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "auth.log"
        log.write_text("old: Accepted password for root from 6.6.6.6 port 1 ssh2\n")
        health.check_ssh({"1.1.1.1"}, log)  # 启动前的历史不告警
        with log.open("a") as f:
            f.write("Accepted publickey for ubuntu from 1.1.1.1 port 2 ssh2\nAccepted publickey for ubuntu from 2.2.2.2")
        health.check_ssh({"1.1.1.1"}, log)
        assert not health._pending  # 常用 IP 不告警，半行留到下次
        with log.open("a") as f:
            f.write(" port 3 ssh2\n")
        health.check_ssh({"1.1.1.1"}, log)
        assert len(health._pending) == 1 and "2·2·2·2 以 ubuntu 身份" in health._pending[0], health._pending
        log.unlink()
        log.write_text("Accepted password for root from 3.3.3.3 port 4 ssh2\n")  # 日志轮转后从头读
        health.check_ssh({"1.1.1.1"}, log)
        assert "3·3·3·3" in health._pending[-1], health._pending

    reset()
    health.check_disk(Path(__file__).parent)
    assert not health._pending  # 本机磁盘空间充足
    old, health.DISK_MIN_FREE = health.DISK_MIN_FREE, 1 << 60
    health.check_disk(Path(__file__).parent)
    health.DISK_MIN_FREE = old
    assert health._pending[0].startswith("存储空间不足"), health._pending
    reset()


def check_openf1_results():
    flag = {"dnf": False, "dns": False, "dsq": False}
    sess = [{"session_key": 7, "session_name": "Race", "date_start": "2026-10-25T20:00:00+00:00"},
            {"session_key": 8, "session_name": "Qualifying", "date_start": "2026-10-24T21:00:00+00:00"}]
    drivers = [{"driver_number": 1, "name_acronym": "VER", "first_name": "Max", "last_name": "Verstappen", "team_name": "Red Bull Racing"},
               {"driver_number": 44, "name_acronym": "HAM", "first_name": "Lewis", "last_name": "Hamilton", "team_name": "Ferrari"},
               {"driver_number": 16, "name_acronym": "LEC", "first_name": "Charles", "last_name": "Leclerc", "team_name": "Ferrari"}]
    race_rows = [{"position": 1, "driver_number": 1, "points": 25.0, "number_of_laps": 56} | flag,
                 {"position": 2, "driver_number": 44, "points": 18.0, "number_of_laps": 56} | flag,
                 {"position": None, "driver_number": 16, "points": 0.0, "number_of_laps": 10} | flag | {"dnf": True}]
    # 汉密尔顿第 7 圈 1:29.500 被赛会取消（赛道界限），最快圈应是他第 8 圈的 1:29.900
    laps = [{"driver_number": 1, "lap_number": 5, "lap_duration": 90.0}, {"driver_number": 44, "lap_number": 7, "lap_duration": 89.5},
            {"driver_number": 44, "lap_number": 8, "lap_duration": 89.9}]
    control = [{"message": "CAR 44 (HAM) TIME 1:29.500 DELETED - TRACK LIMITS AT TURN 2 LAP 7 20:07:17"},
               {"message": "CAR 1 (VER) LAP DELETED - TRACK LIMITS AT TURN 2 LAP 5 20:05:00 (PIT)"}]
    qual_rows = [{"position": 1, "driver_number": 44, "duration": [95.0, 94.5, 94.035]},
                 {"position": 2, "driver_number": 1, "duration": [96.0, 95.2, None]},
                 {"position": 3, "driver_number": 16, "duration": [None, None, None]}]
    feeds = {"/sessions?": sess, "session_result?session_key=7": race_rows, "drivers?session_key=7": drivers,
             "laps?session_key=7": laps, "race_control?session_key=7": control,
             "session_result?session_key=8": qual_rows, "drivers?session_key=8": drivers,
             "current/18/results": {"MRData": {"RaceTable": {"Races": [{"round": "18", "Results": [{"marker": "jolpica"}]}]}}}}

    def run(overrides=None):
        async def fake(url, cache):
            for part, data in (feeds | (overrides or {})).items():
                if part in url:
                    if isinstance(data, Exception):
                        raise data
                    return data
        f1._fetch = fake
        f1._RECENT.clear()
        return lambda kind: asyncio.run(f1.fetch_session("18", kind))

    saved = f1._fetch, f1.load_races, f1._code_drivers, f1.DATA, f1._openf1_live
    try:
        with tempfile.TemporaryDirectory() as d:
            f1.DATA, f1.load_races, f1._code_drivers = Path(d), lambda: [AUSTIN], lambda: {}
            f1._openf1_live = lambda now: False  # 不受真实时钟影响（真实赛中时段会跳过 OpenF1）
            got = run()("race")
            assert f1.format_session(got, "race") == "\n".join([
                f"{f1.gp_name(AUSTIN)} 正赛结果（前2）", "1. 维斯塔潘（红牛）+25分", "2. 汉密尔顿（法拉利）+18分", "",
                "【最快圈速】", "汉密尔顿（法拉利） 1:29.900", "", "【退赛】", "勒克莱尔（完成10圈）"]), f1.format_session(got, "race")
            assert f1.format_session(run()("qualifying"), "qualifying") == "\n".join([
                f"{f1.gp_name(AUSTIN)} 排位赛结果（前10）", "1. 汉密尔顿（法拉利）1:34.035",
                "2. 维斯塔潘（红牛）1:35.200", "3. 勒克莱尔（法拉利）无成绩"])
            full = f1.format_session(run()("qualifying"), "qualifying", top=None)  # 主动查询：列全部，标题不带“前N”
            assert full.startswith(f"{f1.gp_name(AUSTIN)} 排位赛结果\n1.") and "3. 勒克莱尔" in full, full
            assert (Path(d) / "openf1-fastest-7.json").exists()  # 最快圈已缓存
            (Path(d) / "openf1-fastest-7.json").unlink()
            text = f1.format_session(run({"laps?session_key=7": None})("race"), "race")  # 圈速取不到：照出成绩，不显示最快圈
            assert "1. 维斯塔潘" in text and "最快圈速" not in text, text
            for name, over in {"连不上": {"/sessions?": ConnectionError()}, "成绩未公布": {"session_result?session_key=7": None},
                               "只公布一部分": {"session_result?session_key=7": race_rows[:2]},
                               "积分没填": {"session_result?session_key=7": [r | {"points": None} for r in race_rows]}}.items():
                assert run(over)("race")["Results"] == [{"marker": "jolpica"}], name  # 回退 Jolpica
            assert run({"session_result?session_key=8": [r | {"duration": None} for r in qual_rows]})(
                "qualifying") is None  # 圈速没填、Jolpica 也没有：不出成绩
            f1._fetch = saved[0]
            f1._openf1_live = lambda now: True  # 赛中：OpenF1 只读缓存，不发请求
            (Path(d) / "openf1-x.json").write_text('["cached"]', encoding="utf-8")
            assert asyncio.run(f1._fetch(f"{f1.OPENF1}/session_result?session_key=1", "openf1-x.json")) == ["cached"]
            f1._openf1_live = saved[4]
            assert f1._openf1_live(utc("2026-10-25T21:00:00")) and f1._openf1_live(utc("2026-10-25T19:30:00"))  # 正赛中、赛前 30 分钟
            assert not f1._openf1_live(utc("2026-10-25T22:35:00"))  # 正赛预计 22:00 结束，30 分钟后放开
    finally:
        f1._fetch, f1.load_races, f1._code_drivers, f1.DATA, f1._openf1_live = saved
        f1._RECENT.clear()


FAMILY = {"VER": "Verstappen", "HAM": "Hamilton", "LEC": "Leclerc", "NOR": "Norris", "HUL": "Hülkenberg"}


def _res(code, team, pos, pts, text=None, status="Finished"):
    return {"position": str(pos), "positionText": text or str(pos), "points": str(pts), "status": status, "laps": "50",
            "Driver": {"driverId": code.lower(), "code": code, "givenName": "", "familyName": FAMILY.get(code, code)},
            "Constructor": {"name": team}}


def check_standings_catch_up():
    def drv(code, team, pts, wins, pos):
        return {"position": str(pos), "points": str(pts), "wins": str(wins),
                "Driver": {"driverId": code.lower(), "code": code, "givenName": "", "familyName": code},
                "Constructors": [{"name": team}]}
    base = {"round": "17", "DriverStandings": [drv("VER", "Red Bull", 100, 3, 1), drv("HAM", "Ferrari", 90, 2, 2),
                                               drv("LEC", "Ferrari", 80, 0, 3)]}
    race = {"season": "2026", "round": "18"}
    result = {"Results": [_res("LEC", "Ferrari", 1, 25), _res("NEW", "Red Bull Racing", 2, 18), _res("VER", "Red Bull Racing", 3, 15)]}
    sprint = {"Results": [_res("HAM", "Ferrari", 1, 8), _res("VER", "Red Bull Racing", 2, 7)]}
    st = f1._add_round(base, "driver", race, result, sprint)
    got = [(r["Driver"]["code"], r["position"], r["points"], r["wins"]) for r in st["DriverStandings"]]
    # 冲刺赛冠军不计 wins；HAM 与 LEC 同 98 分但胜场不同；新车手补进来
    assert got == [("VER", "1", "122", "3"), ("LEC", "2", "105", "1"), ("HAM", "3", "98", "2"), ("NEW", "4", "18", "0")], got
    assert st["round"] == "18" and base["DriverStandings"][0]["points"] == "100"  # 不改动原榜
    tie = f1._add_round({"DriverStandings": [drv("A", "X", 7, 0, 1), drv("B", "X", 5, 0, 2)]}, "driver", race,
                        {"Results": [_res("B", "X", 9, 2)]}, None)
    assert [r["position"] for r in tie["DriverStandings"]] == ["1", "1"]  # 同分同胜场并列，不硬排先后
    cons = f1._add_round({"ConstructorStandings": [{"position": "1", "points": "50", "wins": "1", "Constructor": {"name": "Red Bull"}}]},
                         "constructor", race, result, sprint)  # OpenF1 叫 Red Bull Racing，按中文名合并
    assert [(f1.team_name(r["Constructor"]), r["points"]) for r in cons["ConstructorStandings"]] == [("红牛", "90"), ("法拉利", "33")]

    official = f1._add_round(base, "driver", race, result, sprint)
    saved = f1._jolpica_standings, f1._latest_result
    try:
        def fake(current):
            async def standings(kind, round_="current"):
                return current if round_ == "current" else base
            async def latest():
                return race, result, sprint
            f1._jolpica_standings, f1._latest_result = standings, latest
            return asyncio.run(f1.fetch_standings("driver"))
        health._pending.clear(), health._last.clear()
        st = fake(base)  # 官方榜还停在第 17 站
        assert st["round"] == "18" and st["provisional"] == "官方积分榜尚未更新" and st["latest_team"]["lec"] == {"name": "Ferrari"}
        assert f1.format_standings(st, "driver").endswith("（官方积分榜尚未更新，暂按最新赛果计算）")
        assert fake(official) is official and "provisional" not in official  # 官方榜已更新且一致：用官方榜
        off = json.loads(json.dumps(official))
        off["DriverStandings"][3]["Driver"]["driverId"] = "new_driver"  # 新车手在 Jolpica 的 id 和 OpenF1 拼的不同
        assert fake(off) is off
        off = json.loads(json.dumps(official))
        off["DriverStandings"][0]["points"] = "120"  # 比如被罚扣分
        st = fake(off)
        assert st["provisional"] == "与官方积分榜有出入" and "VER 官方 120 分、推算 122 分" in health._pending[0], health._pending
        health._pending.clear(), health._last.clear()
    finally:
        f1._jolpica_standings, f1._latest_result = saved


def check_correction():
    race = {"season": "2026", "round": "18", "raceName": "United States Grand Prix"}
    before = {"Results": [_res("VER", "Red Bull", 1, 25), _res("HAM", "Ferrari", 2, 18), _res("LEC", "Ferrari", 3, 15),
                          _res("NOR", "McLaren", 4, 12), _res("HUL", "Audi", 5, 0, "R", "Retired")]}
    after = {"Results": [_res("VER", "Red Bull", 1, 25), _res("LEC", "Ferrari", 2, 18), _res("NOR", "McLaren", 3, 15),
                         _res("HUL", "Audi", 4, 0, "R", "Lapped"), _res("HAM", "Ferrari", 5, 0, "D", "Disqualified")]}
    push_text, query_text = f1.format_session(before | race, "race"), f1.format_session(before | race, "race", top=None)
    assert "（前4）" in push_text and "5. 霍肯伯格" not in push_text  # 推送只列有分的
    assert "正赛结果\n" in query_text and "4. 诺里斯（迈凯轮）+12分" in query_text  # 查询列全部完赛者，无“前N”
    assert "5. 霍肯伯格" not in query_text and "【退赛】\n霍肯伯格" in query_text  # 退赛者只在【退赛】里
    unscored = before["Results"] + [_res("NEW", "Audi", 6, 0)]
    assert "6.  NEW（奥迪）\n" in f1.format_session(race | {"Results": unscored}, "race", top=None) + "\n"  # 无分完赛者也列出，不带“+0分”
    old, new = f1.result_snapshot(before), f1.result_snapshot(after)
    assert old["HUL"] == new["HUL"] == [None, "0", "退赛"]  # 未完赛者名次不比，两源写法不同也不误报
    text = f1.format_correction(race, "race", old, new, after, "车手积分榜（略）")
    assert text == "\n".join([
        "【F1 News】", "「美国大奖赛 正赛成绩更新」", "", "赛后成绩有变动：",
        "勒克莱尔：第3名 15分 → 第2名 18分", "诺里斯：第4名 12分 → 第3名 15分", "汉密尔顿：第2名 18分 → 取消资格 0分",
        "", "车手积分榜（略）", "", "（官方成绩更新｜程序自动比对，内容可能有误）"]), text

    sent, results = [], {"now": after}
    async def send(gid, msg):
        if gid == "flaky" and len(sent) == 0:
            raise RuntimeError("timeout")
        sent.append((gid, msg))
    async def fake_session(round_, kind):
        return race | results["now"]
    async def no_standings(kind):
        return None
    saved = f1.DATA, f1.openf1_result, f1.fetch_session, f1.fetch_standings, push.SEND_GAP_SECONDS
    try:
        with tempfile.TemporaryDirectory() as d:
            f1.DATA, f1.openf1_result, f1.fetch_session, f1.fetch_standings, push.SEND_GAP_SECONDS = (
                Path(d), fake_session, fake_session, no_standings, 0)
            st = push.State(Path(d))
            st.register("flaky"), st.register("g2")
            key = "2026-18-race-result"
            recheck = lambda: asyncio.run(push.recheck_result(race, "race", key, st, send))
            recheck()  # 上线前推过、没有快照：以当前为基准，不推
            assert sent == [] and f1._read(Path(d) / f"snapshot-{key}.json")["current"] == new
            (Path(d) / f"snapshot-{key}.json").unlink()
            push._save_snapshot(key, old)  # 模拟推出去的是改判前的版本
            recheck()
            assert [g for g, _ in sent] == ["g2"]  # flaky 这次失败
            recheck()
            assert [g for g, _ in sent] == ["g2", "flaky"] and "汉密尔顿" in sent[1][1]  # 补发失败的群，同一更正不重复
            recheck()
            results["now"] = before  # 两源一新一旧来回切换，回到推过的旧版本
            recheck()
            assert len(sent) == 2
            st.sent[key] = ["g2"]  # 补发给失败的群时成绩已变：不覆盖基准，复查会给所有群推更正
            asyncio.run(push.poll_result(race, "race", key, st, send))
            assert f1._read(Path(d) / f"snapshot-{key}.json")["current"] == new
    finally:
        f1.DATA, f1.openf1_result, f1.fetch_session, f1.fetch_standings, push.SEND_GAP_SECONDS = saved


def check_early_polls():
    with tempfile.TemporaryDirectory() as d:
        st = push.State(Path(d))
        st.register("g1")
        key, tries = "2026-18-qualifying-result", {}
        poll = lambda s: push.early_polls(AUSTIN, utc(s), st, tries)
        assert poll("2026-10-24T22:34:00") == []  # 排位预计 22:00 结束，35 分钟后才取
        assert poll("2026-10-24T22:35:00") == [("qualifying", key)]
        tries[key] = utc("2026-10-24T22:35:00")
        assert poll("2026-10-24T22:44:00") == [] and poll("2026-10-24T22:45:00") == [("qualifying", key)]  # 每 10 分钟
        assert poll("2026-10-25T01:00:00") == []  # 到 3 小时由慢速尝试接手
        st.sent[key] = ["g1"]
        assert poll("2026-10-24T22:45:00") == []  # 已推送成功就不再取
        assert poll("2026-10-25T22:35:00") == [("race", "2026-18-race-result")]  # 正赛预计 22:00 结束


def check_news():
    now = utc("2026-10-15T03:30:00")
    good = {"id": "a" * 40, "title_zh": "F1与卢旺达签约十年", "summary_zh": "最早于2030年首办。",
            "source": "Autosport", "published": "2026-10-13T09:20:07Z"}
    bad = [good | {"summary_zh": "详见 www.example.com"}, good | {"source": "autosport.com"},
           good | {"id": "123"}, good | {"published": "2026-10-13T09:20:07"}, {"title_zh": "缺字段"}, "x"]
    health._pending.clear(), health._last.clear()
    items = news.valid_items({"items": [good] + bad[:4]}, now)
    assert [i["id"] for i in items] == ["a" * 40] and health._pending, (items, health._pending)
    assert news.valid_items({"items": [good]}, utc("2026-10-17T03:30:00")) == []  # 超过 3 天不推
    assert news.format_news(items[0]) == ("【F1 News】\n「F1与卢旺达签约十年」\n\n最早于2030年首办。\n\n"
                                          "（Autosport · 周二 10-13 17:20｜程序自动抓取，内容可能有误）")
    assert news.valid_items({"items": bad[4:]}, now) == [] and news.valid_items(None, now) == []

    async def fake_fetch(url, cache):
        return {"items": [good]}
    sent = []
    async def send(gid, text):
        sent.append(gid)
    old_fetch, old_gap, f1._fetch, push.SEND_GAP_SECONDS = f1._fetch, push.SEND_GAP_SECONDS, fake_fetch, 0
    old_age, news.MAX_AGE = news.MAX_AGE, timedelta(days=3650)  # poll_news 用真实当前时间
    try:
        with tempfile.TemporaryDirectory() as d:
            st = push.State(Path(d))
            st.register("test"), st.register("other")
            for _ in range(2):  # 第二轮按 id 去重，不重复推
                asyncio.run(push.poll_news({"news_groups": ["test"]}, st, send))
            assert sent == ["test"], sent
            asyncio.run(push.poll_news({}, st, send))  # 不配置则不推
            asyncio.run(push.poll_news({"news_groups": "all"}, st, send))
            assert sent == ["test", "other"], sent
    finally:
        f1._fetch, push.SEND_GAP_SECONDS, news.MAX_AGE = old_fetch, old_gap, old_age
    health._pending.clear(), health._last.clear()


if __name__ == "__main__":
    for fn in (check_preview_time, check_sprint_order, check_timezone,
               check_dedupe_and_catchup, check_grid, check_refresh_interval, check_result_window, check_parse, check_find_race, check_winners, check_session_results, check_title, check_title_bruteforce, check_clinch_preview, check_health, check_openf1_results, check_standings_catch_up, check_correction, check_early_polls, check_news):
        fn()
    print("OK")
