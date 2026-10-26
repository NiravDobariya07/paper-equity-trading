"""India Standard Time (UTC+05:30, no daylight saving) and the NSE session grid."""

import datetime

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
BAR = datetime.timedelta(minutes=5)
SESSION_BARS = 75  # 09:15 to 15:30 in 5-minute bars


def now():
    return datetime.datetime.now(IST)


def from_ts(ts):
    return datetime.datetime.fromtimestamp(ts, IST)


def at(day, hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def session_open(day):
    return datetime.datetime.combine(day, OPEN, IST)


def bar_index(dt):
    return int((dt - session_open(dt.date())) // BAR)


def bar_start(day, i):
    return session_open(day) + i * BAR


def floor5(dt):
    return bar_start(dt.date(), bar_index(dt))


def is_trading_day(day, holidays):
    return day.weekday() < 5 and day.isoformat() not in set(holidays)
