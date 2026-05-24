# 关系互动分析器 V1

本地聊天关系分析原型。V1 在 V0 的 TXT/CSV 基础上，新增微信截图 OCR、第三方导出文件适配、消息校对、关系趋势报告和导出能力。

## 运行

```bash
python3 app.py
```

打开：

```text
http://127.0.0.1:8000
```

## V1 工作流

1. 导入聊天数据
   - 截图 OCR：上传微信聊天截图、长截图或多张截图。
   - 导出文件：上传第三方工具导出的 TXT / CSV / JSON / HTML。
2. 选择“我是谁”
3. 校对消息
   - 修改发送者、身份、时间、消息类型、内容。
   - 删除误识别消息。
   - 合并相邻或重复消息。
4. 查看统计和关系趋势报告
5. 导出标准 TXT / CSV / HTML 报告

## 截图 OCR

V1 默认使用 macOS Vision OCR，本地识别，不上传云端。需要 macOS 自带的 Swift 运行环境：

```bash
which swift
```

截图识别规则：

- 右侧气泡默认识别为“我”
- 左侧气泡默认识别为“对方”
- 中间时间文字作为时间锚点
- 置信度较低或位置不明确的消息会标记为“需校对”

如果 OCR 结果不完美，请在“校对消息”表格中修正后再分析。

## 第三方导出文件

支持：

- `.csv`
- `.json`
- `.html` / `.htm`
- `.txt`

CSV 默认字段别名：

- 时间：`timestamp`、`time`、`date`、`datetime`、`created_at`、`send_time`
- 发送者：`sender`、`name`、`from`、`speaker`、`user`、`nickname`
- 内容：`text`、`message`、`content`、`body`、`msg`、`msg_text`

如果第三方 CSV 字段不符合这些名字，可以在导入页面手动填写列名。

## 报告说明

V1 使用规则模型，不接入 LLM，不输出“好感分”。报告包含：

- 关系趋势：升温 / 稳定 / 降温 / 单方投入 / 数据不足
- 互动热度分
- 回应投入分
- 互动对等分
- 关系推进分
- 风险信号分
- 支持证据和反向证据

报告只分析聊天中的互动信号，不等同于对方真实心理或关系承诺。

## 测试

```bash
python3 -m unittest discover tests
```

如遇 Python 缓存目录权限问题，可使用：

```bash
PYTHONPYCACHEPREFIX=/private/tmp/chat_analyzer_pycache python3 -m py_compile app.py tests/test_app.py
```
