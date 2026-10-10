"""自检：python check.py，全部 assert 通过即输出 OK。"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import f1
import main
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
             "蒙扎历届冠军": "winners", "往届": "winners"}
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


if __name__ == "__main__":
    for fn in (check_preview_time, check_sprint_order, check_timezone,
               check_dedupe_and_catchup, check_refresh_interval, check_result_window, check_parse, check_find_race, check_winners):
        fn()
    print("OK")
