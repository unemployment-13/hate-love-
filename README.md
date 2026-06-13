# 关系互动分析器 V2

本地聊天关系分析原型。V2 在 V1 的微信截图 OCR、第三方导出文件适配、消息校对、关系趋势报告基础上，新增“微信本地导入”框架：检测本机微信账号目录，通过独立 sidecar 协议读取会话和消息，并导入到现有分析流程。

## 运行

```bash
python3 app.py
```

打开：

```text
http://127.0.0.1:8000
```

## V2 工作流

1. 导入聊天数据
   - 微信本地导入：检测本机微信账号目录，选择账号，输入数据库密钥，读取会话并导入。
   - 截图 OCR：上传微信聊天截图、长截图或多张截图。
   - 导出文件：上传第三方工具导出的 TXT / CSV / JSON / HTML。
2. 选择“我是谁”
3. 校对消息
   - 修改发送者、身份、时间、消息类型、内容。
   - 删除误识别消息。
   - 合并相邻或重复消息。
4. 查看统计和关系趋势报告
5. 导出标准 TXT / CSV / HTML 报告

## 微信本地导入

V2 的微信本地导入采用“主应用 + sidecar”的架构：

- Python 主应用负责页面、API、分析和报告。
- `wechat/` 模块负责检测微信目录、调用 sidecar、标准化消息。
- `native/wechat_reader/wechat_reader.py` 定义稳定的 CLI/JSON 协议。

当前 V2.0 已完成上层框架和协议，不复制、不捆绑 WeFlow 的源码或二进制。真实 WCDB native reader 需要后续用 Rust/C++ 或其他可审计实现替换 sidecar。

页面流程：

1. 打开“微信本地”导入页。
2. 点击“检测微信目录”。
3. 选择检测到的账号目录。
4. 输入数据库密钥。
5. 点击“读取会话列表”。
6. 选择会话并导入。

V2.0 已包含：

- macOS / Windows / Linux 的微信目录结构检测。
- sidecar 协议和 fixture 测试模式。
- 微信消息标准化为项目 `Message`。
- 导入后的校对、统计、报告、导出。

V2.0 暂不包含：

- 自动从微信进程获取密钥。
- 真实 WCDB 加密数据库读取器。
- 图片、语音、视频、表情包解密。
- 防撤回或实时监听。

sidecar 协议：

```bash
python3 native/wechat_reader/wechat_reader.py detect
python3 native/wechat_reader/wechat_reader.py list-accounts --root <wechat_root>
python3 native/wechat_reader/wechat_reader.py list-sessions --account <account_dir> --key <db_key>
python3 native/wechat_reader/wechat_reader.py export-messages --account <account_dir> --key <db_key> --session <session_id>
```

开发演示可使用 fixture：

```bash
CHAT_ANALYZER_WECHAT_FIXTURE=samples/wechat_reader_fixture.json \
python3 native/wechat_reader/wechat_reader.py list-sessions --account /tmp/demo --key demo
```

## 截图 OCR

截图 OCR 默认使用 macOS Vision OCR，本地识别，不上传云端。需要 macOS 自带的 Swift 运行环境：

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

V2 使用规则模型，不接入 LLM，不输出“好感分”。报告包含：

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
PYTHONPYCACHEPREFIX=/private/tmp/chat_analyzer_pycache python3 -m py_compile app.py tests/test_app.py native/wechat_reader/wechat_reader.py wechat/detector.py wechat/collector.py wechat/normalizer.py
```
