# -*- coding: utf-8 -*-
"""
scheduler.py
本地确定性排程引擎（不调用大模型）：
1) 固定时间任务（会议等）先占位，并从空闲槽中扣除；
2) 弹性任务按「优先级高 -> 截止早」排序，贪心填入剩余连续空档；
3) 排不下的任务放进 overflow，由界面提示用户顺延或拆分。
"""
import re
import uuid
from datetime import datetime, timedelta, date, time


def parse_intervals(text):
    """
    解析 "09:00-12:00, 14:00-22:00" -> [(time, time), ...]
    """
    out = []
    for chunk in re.split(r"[,，;；]+", text or ""):
        m = re.match(r"\s*(\d{1,2}):(\d{2})\s*[-~—到]\s*(\d{1,2}):(\d{2})\s*$", chunk)
        if m:
            h1, m1, h2, m2 = map(int, m.groups())
            out.append((time(h1, m1), time(h2, m2)))
    return out


def build_free_slots(start: date, end: date, intervals):
    """根据起止日期 + 每日时间段，生成每一天的空闲区间 [(datetime, datetime), ...]"""
    slots = []
    d = start
    while d <= end:
        for s_t, e_t in intervals:
            s = datetime.combine(d, s_t)
            e = datetime.combine(d, e_t)
            if e > s:
                slots.append((s, e))
        d += timedelta(days=1)
    return slots


def _cut(free, s, e):
    """从空闲区间列表 free 中扣除 [s, e]。"""
    out = []
    for a, b in free:
        if e <= a or s >= b:
            out.append((a, b))           # 无重叠
        else:
            if s > a:
                out.append((a, s))       # 左段保留
            if e < b:
                out.append((e, b))       # 右段保留
    return out


def schedule(tasks, free_slots):
    """
    tasks: llm_client 返回的结构化任务列表
    free_slots: build_free_slots 的结果
    返回: (plan, overflow)
      plan: [(start_dt, end_dt, title, priority, source)]  已按开始时间排序
      overflow: [task, ...]  没排下的弹性任务
    """
    free = [(s, e) for s, e in free_slots if e > s]
    plan = []
    overflow = []

    # 1) 固定时间任务先占位
    fixed = sorted([t for t in tasks if t.get("fixed_dt")],
                   key=lambda t: t["fixed_dt"])
    for t in fixed:
        s = t["fixed_dt"]
        e = s + timedelta(minutes=max(15, t["estimate_minutes"]))
        plan.append((s, e, t["title"], t["priority"], "固定时间"))
        free = _cut(free, s, e)

    # 2) 弹性任务：优先级降序，截止升序，贪心填槽
    flexible = sorted(
        [t for t in tasks if not t.get("fixed_dt")],
        key=lambda t: (-t["priority"], t.get("due") or datetime.max),
    )
    for t in flexible:
        dur = timedelta(minutes=max(15, t["estimate_minutes"]))
        placed = False
        new_free = []
        for s, e in free:
            if not placed and (e - s) >= dur:
                plan.append((s, s + dur, t["title"], t["priority"], "AI排程"))
                new_free.append((s + dur, e))
                placed = True
            else:
                new_free.append((s, e))
        if placed:
            free = new_free
        else:
            overflow.append(t)

    plan.sort(key=lambda x: x[0])
    return plan, overflow


def format_plan(plan, overflow):
    """把排程结果转成易读文本，方便在终端/报告里展示。"""
    lines = []
    for s, e, title, prio, src in plan:
        lines.append(f"{s.strftime('%m-%d %H:%M')}-{e.strftime('%H:%M')}  "
                     f"[{src}|优先级{prio}] {title}")
    if overflow:
        lines.append("--- 以下任务空闲时间不足，建议顺延或拆分 ---")
        for t in overflow:
            lines.append(f"  ⚠ {t['title']}（预计{t['estimate_minutes']}分钟，优先级{t['priority']}）")
    return "\n".join(lines)


def build_ics(plan):
    """把排程结果导出为标准 iCalendar(.ics) 文本，可导入系统日历/Outlook/Google日历。"""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AI Schedule Assistant//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    now_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for s, e, title, prio, src in plan:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uuid.uuid4()}@aischedule",
            f"DTSTAMP:{now_utc}",
            f"DTSTART:{s.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND:{e.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{title}",
            f"DESCRIPTION:来源:{src}; 优先级:{prio}",
            "BEGIN:VALARM",
            "TRIGGER:-PT15M",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{title}",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


if __name__ == "__main__":
    from llm_client import parse_tasks
    text = "下午3点开例会，晚上7点健身1小时，写实训报告，背英语单词"
    tasks, src, _ = parse_tasks(text)
    print("来源:", src)
    slots = build_free_slots(date.today(), date.today(),
                             parse_intervals("09:00-12:00, 14:00-22:00"))
    plan, overflow = schedule(tasks, slots)
    print(format_plan(plan, overflow))
