# 关系互动分析器 V0

零依赖本地原型：上传 TXT / CSV 聊天记录，选择“我是谁”，查看消息预览、基础统计和趋势图。

## 运行

```bash
python3 app.py
```

打开：

```text
http://127.0.0.1:8000
```

## 支持格式

TXT：

```text
[2026-05-22 20:30] Alice: 你好
2026-05-22 20:31 Bob: 你好呀
Alice: 没有时间也可以
```

CSV 字段别名：

- 时间：`timestamp`、`time`、`date`
- 发送者：`sender`、`name`、`from`
- 内容：`text`、`message`、`content`

## 测试

```bash
python3 -m unittest discover tests
```
