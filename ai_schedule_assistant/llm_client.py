# -*- coding: utf-8 -*-
"""
llm_client.py
负责把用户的自然语言任务描述，调用火山方舟「豆包」解析成结构化任务列表。
- 配置齐全(ARK_API_KEY + ARK_MODEL)时走真实豆包；
- 未配置或调用失败时，自动降级为本地规则解析(mock)，保证无网/无 Key 也能演示排程流程。
"""
import os
import re
import json
from datetime import datetime, date, timedelta

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # 读取同目录 .env

# 默认走百度千帆（文心一言）；也可在 .env 里通过 LLM_BASE_URL 换成任意 OpenAI 兼容服务
BASE_URL = os.getenv("LLM_BASE_URL", "https://qianfan.baidubce.com/v2")

SYSTEM_PROMPT = """你是任务解析助手。把用户的自然语言任务描述解析成严格 JSON，不要输出任何解释文字。
输出格式：
{"analysis":"用一两句话说明你如何理解这些任务、判断优先级和预估时长的依据",
"tasks":[{"title":"任务名","due":"截止时间ISO8601或null","estimate_minutes":整数,
"priority":1到5(5最高)","fixed":布尔,"fixed_time":"固定开始时间ISO8601或null"}]}
规则：
1. 凡是"几点必须做/会议/上课/约定"的，fixed=true 并把 fixed_time 写成当天 ISO 时间(如 2026-09-22T15:00:00)；当天无明确日期时，默认绑定今天。
2. estimate_minutes 没明说就按常理合理估计(如健身60、写报告120)。
3. 紧急/重要/临近截止的 priority 给 4~5，普通事项给 2~3。
4. due 是"截止日期"，没有就给 null。
"""

COMMAND_SYSTEM = """你是日程表操作助手。根据用户指令，对当前日程表执行删除或修改。
严格输出JSON：{"actions":[...]}
action 格式：
- 删除：{"op":"delete","match":"要删除日程的标题关键词"}
- 修改：{"op":"modify","match":"要修改日程的标题关键词","new_title":"新标题或null","new_start":"新开始ISO8601时间或null","new_minutes":"新时长分钟或null"}
规则：
1. match 用日程标题里能唯一识别它的关键词即可。
2. modify 只改你给出的字段，没提到的保持不变。
3. 用户没说或你找不到对应日程时，该 action 就不要加；不要编造日程。
"""

WEEK_FULL = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _today_context():
    now = datetime.now()
    return f"今天是 {now.strftime('%Y-%m-%d')}（{WEEK_FULL[now.weekday()]}）。" \
           f"用户提到的「周X/星期X」指本周对应的那一天；若该日已过则指下周。" \
           f"请务必把周几换算成上面基准的具体日期时间。"


def _to_dt(s):
    """尽力把字符串解析成 datetime；支持 ISO、'YYYY-MM-DD HH:MM'、'HH:MM'。失败返回 None。"""
    if not s:
        return None
    s = str(s).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt == "%H:%M":  # 只有时间，绑定到今天
                now = datetime.now()
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
            return dt
        except ValueError:
            continue
    return None


def _extract_json(text):
    """从模型返回文本里容错提取 JSON 对象（容忍 ```json 代码块、多余解释文字）。"""
    s = text.find("{")
    e = text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("返回内容里没有 JSON")
    return json.loads(text[s:e + 1])


def _normalize(raw, ref_date):
    """把 LLM/原始 dict 统一成内部结构。"""
    fixed_dt = _to_dt(raw.get("fixed_time"))
    due = _to_dt(raw.get("due"))
    if fixed_dt and fixed_dt.date() == date.today():
        pass  # 已经是今天
    return {
        "title": raw.get("title", "未命名任务").strip(),
        "estimate_minutes": int(raw.get("estimate_minutes") or 60),
        "priority": int(raw.get("priority") or 3),
        "due": due,
        "fixed": bool(raw.get("fixed")),
        "fixed_dt": fixed_dt,
    }


def _mock_parse(text):
    """无 Key 时的兜底解析：按句子切分，正则提取"X点"。"""
    sentences = [s for s in re.split(r"[，。；;\n,]+", text) if s.strip()]
    tasks = []
    now = datetime.now()
    for i, sent in enumerate(sentences):
        m = re.search(r"(\d{1,2})\s*[:：点]\s*(\d{1,2})?", sent)
        fixed_dt = None
        fixed = False
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2)) if m.group(2) else 0
            # 处理中文时段词，修正上下午
            if ("下午" in sent or "晚上" in sent or "傍晚" in sent) and hh < 12:
                hh += 12
            elif "中午" in sent and hh < 12:
                hh = 12
            fixed_dt = now.replace(hour=hh % 24, minute=mm, second=0, microsecond=0)
            fixed = True
        tasks.append({
            "title": sent.strip(),
            "estimate_minutes": 60,
            "priority": 5 if fixed else 3,
            "due": None,
            "fixed": fixed,
            "fixed_dt": fixed_dt,
        })
    return tasks


def parse_tasks(text, ref_date=None):
    """
    对外主接口：自然语言 -> (结构化任务列表, 来源说明)
    """
    ref_date = ref_date or date.today()
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")

    if not api_key or not model:
        tasks = _mock_parse(text)
        return tasks, "演示模式（未配置 LLM_API_KEY / LLM_MODEL，使用本地规则解析）", \
            "当前为本地规则解析：按句子切分并提取时间点，优先级/时长为默认值。"

    try:
        client = OpenAI(base_url=BASE_URL, api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + "\n" + _today_context()},
                {"role": "user", "content": text},
            ],
            temperature=0.1,
            extra_body={"enable_thinking": False},  # ernie-5.1 关闭思考模式
        )
        data = _extract_json(resp.choices[0].message.content)
        analysis = data.get("analysis", "")
        tasks = [_normalize(t, ref_date) for t in data.get("tasks", [])]
        return tasks, "文心一言大模型解析成功", analysis
    except Exception as e:  # 网络/额度/格式问题一律降级，保证程序不崩
        tasks = _mock_parse(text)
        return tasks, f"大模型调用失败，已降级本地解析（{type(e).__name__}: {e}）", \
            "调用失败，已用本地规则兜底。"


def parse_command(cmd, plan_text):
    """
    根据用户指令(删除/修改日程)，让模型输出结构化操作列表。
    返回 (actions, 来源说明)。
    """
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    if not api_key or not model:
        return [], "演示模式未配置模型，暂不支持 AI 指令修改"
    try:
        client = OpenAI(base_url=BASE_URL, api_key=api_key)
        user = f"当前日程表：\n{plan_text}\n\n用户指令：{cmd}"
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": COMMAND_SYSTEM + "\n" + _today_context()},
                {"role": "user", "content": user},
            ],
            temperature=0,
            extra_body={"enable_thinking": False},
        )
        data = _extract_json(resp.choices[0].message.content)
        return data.get("actions", []), "指令已解析"
    except Exception as e:
        return [], f"指令解析失败（{type(e).__name__}: {e}）"


def apply_actions(plan, actions):
    """
    本地执行模型返回的操作列表，返回 (新plan, 已执行说明列表)。
    plan 元素: (start_dt, end_dt, title, priority, source)
    """
    items = [list(x) for x in plan]   # 转可变 list
    done = []
    for act in actions:
        op = act.get("op")
        key = (act.get("match") or "").strip()
        if not key:
            continue
        if op == "delete":
            remain = [it for it in items if key not in it[2]]
            removed = len(items) - len(remain)
            if removed:
                done.append(f"已删除 {removed} 条含「{key}」的日程")
            items = remain
        elif op == "modify":
            for it in items:
                if key in it[2]:
                    if act.get("new_title"):
                        it[2] = act["new_title"]
                    new_start = _to_dt(act.get("new_start"))
                    if new_start:
                        dur = it[1] - it[0]
                        it[0], it[1] = new_start, new_start + dur
                    if act.get("new_minutes"):
                        it[1] = it[0] + timedelta(minutes=int(act["new_minutes"]))
                    done.append(f"已修改「{it[2]}」")
    return [tuple(x) for x in items], done


if __name__ == "__main__":
    # 简单自测：python llm_client.py
    demo = "下午3点开例会，晚上7点健身1小时，明天上午写实训报告"
    result, src, analysis = parse_tasks(demo)
    print("来源:", src)
    print("分析:", analysis)
    for t in result:
        print(t)
