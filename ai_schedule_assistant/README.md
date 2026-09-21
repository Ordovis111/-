# AI 时间任务管理助理

输入你要做的事（自然语言）和你的可用时间，AI 帮你解析任务、评估优先级并排进日程。
大模型（百度文心一言）只负责把自然语言翻译成结构化任务，排程由本地确定性算法完成，稳定、可复现、成本低。

## 功能
- 自然语言任务输入：「明天上午写报告，下午3点例会，晚上7点健身」
- 设置日期范围 + 每日可用时段（如 `09:00-12:00, 14:00-22:00`）
- 固定时间任务（会议等）自动占位，弹性任务按「优先级高 → 截止早」贪心填空档
- 排不下的任务给出顺延 / 拆分提示
- 一键导出 `.ics` 日历文件，可导入系统日历 / Outlook / Google 日历

## 目录结构
```
ai_schedule_assistant/
├── app.py            # Streamlit 界面（入口）
├── llm_client.py     # 调用百度千帆(文心一言)解析任务（无 Key 自动降级演示模式）
├── scheduler.py      # 本地排程引擎 + .ics 导出
├── requirements.txt
├── .env.example      # 复制为 .env 填 Key
└── README.md
```

## 安装与运行
```bash
pip install -r requirements.txt
# （可选）配置文心一言 Key：复制 .env.example 为 .env，填入 LLM_API_KEY / LLM_MODEL
streamlit run app.py
```
> 不配 Key 也能直接运行：会自动进入「演示模式」，用本地规则解析任务，方便先看效果、录视频。

## 启用真实文心一言
1. 注册百度智能云，进入「千帆大模型平台」控制台；
2. 左侧「API 管理」创建一个 API Key；
3. 在「模型广场」选一个轻量模型（如 `ernie-speed-8k` / `ernie-3.5-8k`）并开通；
4. 在 `.env` 填入：
   ```
   LLM_API_KEY=你的Key
   LLM_MODEL=ernie-speed-8k
   ```
本项目通过 OpenAI 兼容接口调用，`base_url=https://qianfan.baidubce.com/v2`。
> 想换回豆包(火山方舟)或其他 OpenAI 兼容服务，只改 `.env` 里的 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 即可，代码不用动。

## 离线自测
```bash
python llm_client.py     # 看任务解析
python scheduler.py      # 看排程结果
```
