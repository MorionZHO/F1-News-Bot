"""自检：python check.py，全部 assert 通过即输出 OK。"""

import asyncio
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
               check_dedupe_and_catchup, check_grid, check_refresh_interval, check_result_window, check_parse, check_find_race, check_winners, check_session_results, check_title, check_title_bruteforce, check_clinch_preview, check_health, check_news):
        fn()
    print("OK")
