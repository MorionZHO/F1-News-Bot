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
        assert [t.key for t in due] == ["2026-17-正赛 Race-30"], due
        st.sent[due[0].key] = ["g1"]
        assert not push.due_tasks([SINGAPORE], t0 + timedelta(minutes=5), [30], st)
        assert not push.due_tasks([SINGAPORE], t0 + timedelta(minutes=11), [30], st)
        st.groups["g1"]["sub"] = False
        assert not st.targets("any-key")


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
             "车队积分榜": "constructor", "取消订阅": "unsub", "下一站": "next"}
    for text, cmd in cases.items():
        assert main.parse(text) == cmd, (text, main.parse(text))


def check_find_race():
    spain = race("14", "Spanish Grand Prix", "2026-09-13", "13:00:00Z")
    bcn = race("7", "Barcelona Grand Prix", "2026-06-14", "13:00:00Z")
    china = race("2", "Chinese Grand Prix", "2026-03-15", "07:00:00Z")
    races = [china, bcn, spain, BAKU]
    cases = {"巴库": BAKU, " 西班牙 ": spain, "上海": china, "巴塞罗那": bcn,
             "阿塞拜疆大奖赛": BAKU, "R7": bcn, "你好": None}
    for text, want in cases.items():
        assert f1.find_race(text, races) is want, text


if __name__ == "__main__":
    for fn in (check_preview_time, check_sprint_order, check_timezone,
               check_dedupe_and_catchup, check_result_window, check_parse, check_find_race):
        fn()
    print("OK")
