# -*- coding: utf-8 -*-
"""
app.py —— AI 时间任务管理助理（Streamlit 界面）
运行：streamlit run app.py
"""
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta, date, time

from llm_client import parse_tasks, parse_command, apply_actions
from scheduler import parse_intervals, build_free_slots, schedule, build_ics

st.set_page_config(page_title="AI 时间任务管理助理", page_icon="📅", layout="wide")
st.title("📅 AI 时间任务管理助理")

# ---- session_state：持久化当前周表上的日程 ----
if "plan" not in st.session_state:
    st.session_state.plan = []   # 元素: (start_dt, end_dt, title, priority, source)
if "analysis_log" not in st.session_state:
    st.session_state.analysis_log = []   # 保留 AI 的历次分析说明


def slot_of(dt: datetime) -> str:
    h = dt.hour
    if h < 12:
        return "上午"
    if h < 18:
        return "下午"
    return "晚上"


WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def render_week_table(plan):
    """把已排日程渲染成卡片式周表：左列时段，右边按真实日期归列。"""
    if not plan:
        st.info("表上还没有日程。在下面输入任务让 AI 安排，或用「手动添加」直接加。")
        return
    dates = sorted({s.date() for s, *_ in plan})
    rows = ["上午", "下午", "晚上"]

    html = ['<table style="width:100%;border-collapse:collapse;table-layout:fixed;">']
    # 表头：空角 + 各日期
    html.append('<tr><th style="width:90px;"></th>')
    for d in dates:
        html.append(
            f'<th style="border:1px solid #bbb;padding:6px;background:#f0f0f0;">'
            f'{WEEK_CN[d.weekday()]}<br><small>{d.strftime("%m-%d")}</small></th>')
    html.append('</tr>')
    for r in rows:
        cells, any_content = [], False
        for d in dates:
            items = [it for it in plan if it[0].date() == d and slot_of(it[0]) == r]
            if items:
                any_content = True
            block = ""
            for s, e, title, prio, src in items:
                color = "#ffd9d9" if src == "固定时间" else "#d6e8ff"
                mark = "📌" if src == "固定时间" else ""
                block += (f'<div style="background:{color};border-radius:6px;'
                          f'padding:5px 7px;margin:3px 0;font-size:13px;">'
                          f'{mark}{s.strftime("%H:%M")} {title}</div>')
            cells.append(block)
        if not any_content:
            continue  # 该时段整周无日程，整行不显示
        html.append(
            f'<tr><td style="border:1px solid #bbb;text-align:center;font-weight:bold;'
            f'background:#fafafa;">{r}</td>')
        for block in cells:
            html.append(f'<td style="border:1px solid #bbb;vertical-align:top;">{block}</td>')
        html.append('</tr>')
    html.append('</table>')
    st.markdown("".join(html), unsafe_allow_html=True)


# ============ 侧边栏：时间安排 ============
with st.sidebar:
    st.header("⏰ 我的时间安排")
    start = st.date_input("计划开始日", value=date.today())
    end = st.date_input("计划结束日", value=date.today() + timedelta(days=6))
    intervals_text = st.text_input("每日可用时段", "09:00-12:00, 14:00-22:00",
                                  help='格式：HH:MM-HH:MM，逗号分隔多个时段')
    st.divider()
    if st.button("🗑️ 清空表上所有日程", use_container_width=True):
        st.session_state.plan = []
        st.session_state.analysis_log = []
        st.rerun()

# ============ 主区：周表 ============
st.subheader("🗓️ 本周日程表（周一 ~ 周五）")
if st.session_state.analysis_log:
    st.info("🤖 " + st.session_state.analysis_log[-1], icon="🧠")
render_week_table(st.session_state.plan)

# 统计
if st.session_state.plan:
    total_min = int(sum((e - s).total_seconds() // 60 for s, e, *_ in st.session_state.plan))
    c1, c2 = st.columns(2)
    c1.metric("本周日程数", len(st.session_state.plan))
    c2.metric("本周已排时长", f"{total_min // 60}小时{total_min % 60}分")
    st.download_button(
        "⬇️ 导出为日历文件 (.ics)",
        data=build_ics(st.session_state.plan).encode("utf-8"),
        file_name="my_schedule.ics", mime="text/calendar",
    )

st.divider()

# ============ AI 分析 ============
st.subheader("🤖 AI 安排新任务")
task_text = st.text_area("✍️ 我需要做的事（自然语言）", height=130,
                         placeholder="例如：\n明天上午写实训报告，下午3点开例会，晚上7点健身1小时，周五前交作业")

if st.button("🚀 AI 分析并填入表", type="primary", use_container_width=True):
    if not task_text.strip():
        st.warning("请先输入你要做的事。")
    else:
        intervals = parse_intervals(intervals_text)
        if not intervals:
            st.error("可用时段格式不对，示例：09:00-12:00, 14:00-22:00")
        else:
            with st.spinner("AI 正在解析并排程…"):
                tasks, source, analysis = parse_tasks(task_text, ref_date=start)
                slots = build_free_slots(start, end, intervals)
                plan, overflow = schedule(tasks, slots)
            st.toast(source, icon="✅")
            if analysis:
                st.session_state.analysis_log.append(analysis)
            st.session_state.plan.extend(plan)   # 追加到周表
            if overflow:
                for t in overflow:
                    st.warning(f"⚠️ 空闲时间不足，未排入：{t['title']}")
            st.rerun()

# ============ 用自然语言让 AI 删除/修改日程 ============
st.subheader("✏️ 给 AI 下指令，修改或删除日程")
cmd = st.text_input("", placeholder="例如：把周三的健身删掉 ／ 把写报告改到周五下午3点 ／ 把例会延长到1小时")
if st.button("🎯 执行指令", use_container_width=True):
    if not cmd.strip():
        st.warning("请先输入指令。")
    elif not st.session_state.plan:
        st.warning("表上还没有日程。")
    else:
        plan_text = "\n".join(
            f"{s.strftime('%m-%d %H:%M')}-{e.strftime('%H:%M')} {title}"
            for s, e, title, *_ in st.session_state.plan)
        with st.spinner("AI 正在理解指令…"):
            actions, msg = parse_command(cmd, plan_text)
            new_plan, done = apply_actions(st.session_state.plan, actions)
        st.session_state.plan = new_plan
        if done:
            for d in done:
                st.success(d)
        else:
            st.info(msg + "（没有匹配到可执行的改动）")
        st.rerun()

# ============ 保留 AI 的分析说明 ============
if st.session_state.analysis_log:
    with st.expander(f"🤖 AI 的历次分析说明（共 {len(st.session_state.analysis_log)} 条）",
                     expanded=False):
        for i, a in enumerate(reversed(st.session_state.analysis_log), 1):
            st.write(f"**第 {len(st.session_state.analysis_log) - i + 1} 次分析**：{a}")

# ============ 手动添加小功能 ============
with st.expander("➕ 手动添加一个事项（不用 AI，直接写进表）"):
    m1, m2 = st.columns(2)
    m_title = m1.text_input("事项名称", placeholder="如：周三班会")
    m_date = m2.date_input("日期", value=date.today())
    m3, m4 = st.columns(2)
    m_time = m3.time_input("开始时间", value=time(19, 0))
    m_dur = m4.number_input("时长(分钟)", 15, 480, 60, 15)
    if st.button("添加到表"):
        if not m_title.strip():
            st.warning("请填写事项名称。")
        else:
            s = datetime.combine(m_date, m_time)
            e = s + timedelta(minutes=int(m_dur))
            st.session_state.plan.append((s, e, m_title.strip(), 3, "手动"))
            st.rerun()
