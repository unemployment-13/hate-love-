import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import (
    assign_self_sender,
    compute_metrics,
    export_csv,
    export_txt,
    generate_report,
    import_file,
    import_screenshots,
    import_wechat_records,
    parse_csv_content,
    parse_datetime,
    parse_html_content,
    parse_json_content,
    parse_txt,
)
from wechat.collector import export_messages, list_sessions
from wechat.detector import detect_wechat, find_accounts


class ParserTests(unittest.TestCase):
    def test_txt_three_supported_formats_and_error_line(self):
        content = "\n".join(
            [
                "[2026-05-22 20:30] Alice: 你好",
                "2026-05-22 20:31 Bob: 你好呀",
                "Alice: 没有时间也可以",
                "bad line",
            ]
        )
        messages, errors = parse_txt(content)

        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0].id, "m_000001")
        self.assertEqual(messages[1].timestamp, "2026-05-22T20:31:00")
        self.assertIsNone(messages[2].timestamp)
        self.assertEqual(len(errors), 1)

    def test_csv_field_aliases(self):
        content = "date,from,message\n2026-05-22 20:30,Alice,你好\n2026-05-22 20:32,Bob,收到\n"
        messages, errors = parse_csv_content(content)

        self.assertEqual(errors, [])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].sender, "Alice")
        self.assertEqual(messages[0].text, "你好")

    def test_csv_bad_row_does_not_fail_entire_file(self):
        content = "timestamp,sender,text\n2026-05-22 20:30,Alice,你好\n2026-05-22 20:31,,缺少发送者\n"
        messages, errors = parse_csv_content(content)

        self.assertEqual(len(messages), 1)
        self.assertEqual(len(errors), 1)

    def test_json_export_import(self):
        content = '{"messages":[{"timestamp":"2026-05-22 20:30","sender":"Alice","text":"你好"}]}'
        messages, errors = parse_json_content(content)

        self.assertEqual(errors, [])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].source, "json_export")

    def test_html_export_import(self):
        content = "<html><body><p>[2026-05-22 20:30] Alice: 你好</p><p>Bob: 收到</p></body></html>"
        messages, errors = parse_html_content(content)

        self.assertEqual(errors, [])
        self.assertEqual(len(messages), 2)

    def test_file_import_supports_manual_csv_mapping(self):
        content = "when,who,body\n2026-05-22 20:30,Alice,你好\n"
        result = import_file(
            "export.csv",
            content.encode("utf-8"),
            mapping={"time_field": "when", "sender_field": "who", "text_field": "body"},
        )

        self.assertEqual(len(result.messages), 1)
        self.assertEqual(result.messages[0].sender, "Alice")

    def test_screenshot_import_uses_ocr_blocks(self):
        blocks = [
            {"image": "a.jpg", "image_index": 1, "text": "4月30日 上午10:29", "confidence": 0.98, "x": 0.42, "y": 0.8, "width": 0.16, "height": 0.03},
            {"image": "a.jpg", "image_index": 1, "text": "你好", "confidence": 0.91, "x": 0.72, "y": 0.7, "width": 0.1, "height": 0.04},
            {"image": "a.jpg", "image_index": 1, "text": "收到", "confidence": 0.88, "x": 0.12, "y": 0.6, "width": 0.12, "height": 0.04},
        ]
        with patch("app.run_vision_ocr", return_value=(blocks, [])):
            result = import_screenshots([("a.jpg", b"fake")])

        self.assertEqual(len(result.messages), 2)
        self.assertEqual(result.messages[0].sender, "我")
        self.assertEqual(result.messages[1].sender, "对方")
        self.assertEqual(result.messages[0].timestamp, "2026-04-30T10:29:00")


class DataAndMetricTests(unittest.TestCase):
    def test_assign_self_sender_marks_other_side(self):
        messages, _ = parse_txt("[2026-05-22 20:30] Alice: 你好\n[2026-05-22 20:31] Bob: 你好\n")

        assign_self_sender(messages, "Alice")

        self.assertEqual(messages[0].sender_role, "self")
        self.assertEqual(messages[1].sender_role, "other")

    def test_metrics_conversation_start_and_reply_intervals(self):
        messages, _ = parse_txt(
            "\n".join(
                [
                    "[2026-05-22 08:00] Alice: 早",
                    "[2026-05-22 08:10] Bob: 早呀",
                    "[2026-05-22 08:20] Alice: 今天忙吗",
                    "[2026-05-22 15:00] Bob: 下午才看到",
                ]
            )
        )
        assign_self_sender(messages, "Alice")

        metrics = compute_metrics(messages)

        self.assertEqual(metrics["total_messages"], 4)
        self.assertEqual(metrics["role_counts"]["self"], 2)
        self.assertEqual(metrics["role_counts"]["other"], 2)
        self.assertEqual(metrics["conversation_starts"], 2)
        self.assertEqual(metrics["reply_interval_count"], 3)
        self.assertEqual(metrics["daily_message_counts"][0]["total"], 4)

    def test_untimed_messages_are_previewable_but_not_in_trends(self):
        messages, _ = parse_txt("Alice: 没有时间\nBob: 也没有时间\n")
        assign_self_sender(messages, "Alice")

        metrics = compute_metrics(messages)

        self.assertEqual(metrics["total_messages"], 2)
        self.assertEqual(metrics["untimed_message_count"], 2)
        self.assertEqual(metrics["daily_message_counts"], [])
        self.assertEqual(metrics["conversation_starts"], 0)

    def test_report_and_exports(self):
        messages, _ = parse_txt(
            "\n".join(
                [
                    "[2026-05-22 08:00] Alice: 早，今天有空一起吃饭吗",
                    "[2026-05-22 08:05] Bob: 可以呀，我晚上有空",
                    "[2026-05-22 08:08] Alice: 那我订位置",
                    "[2026-05-22 08:10] Bob: 好，我等你消息",
                ]
            )
        )
        assign_self_sender(messages, "Alice")

        report = generate_report(messages)
        txt = export_txt(messages)
        csv_text = export_csv(messages)

        self.assertIn("scores", report)
        self.assertIn("claims", report)
        self.assertIn("[2026-05-22 08:00:00] Alice", txt)
        self.assertIn("sender_role", csv_text)


class WeChatV2Tests(unittest.TestCase):
    def test_wechat_detector_finds_account_dirs(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp_dir:
            root = Path(temp_dir) / "xwechat_files"
            account = root / "wxid_demo_1234"
            (account / "db_storage").mkdir(parents=True)

            accounts = find_accounts(root)
            detection = detect_wechat(str(root))

        self.assertEqual(len(accounts), 1)
        self.assertTrue(detection["success"])
        self.assertEqual(detection["accounts"][0]["account_name"], "wxid_demo_1234")

    def test_wechat_sidecar_fixture_lists_sessions_and_messages(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp_dir:
            account = Path(temp_dir) / "wxid_demo_1234"
            (account / "db_storage").mkdir(parents=True)
            fixture = {
                "sessions": [
                    {
                        "session_id": "wxid_friend",
                        "display_name": "Alice",
                        "message_count": 2,
                        "last_timestamp": 1738713660,
                    }
                ],
                "messages": {
                    "wxid_friend": [
                        {
                            "id": "wx_1",
                            "session_id": "wxid_friend",
                            "sender": "我",
                            "is_self": True,
                            "timestamp": 1738713600,
                            "raw_type": 1,
                            "content": "你好",
                        }
                    ]
                },
            }
            (account / "wechat_reader_fixture.json").write_text(json.dumps(fixture), encoding="utf-8")

            sessions = list_sessions(str(account), "manual-key")
            messages = export_messages(str(account), "manual-key", "wxid_friend")

        self.assertEqual(sessions[0]["display_name"], "Alice")
        self.assertEqual(messages[0]["content"], "你好")

    def test_wechat_records_normalize_to_project_messages(self):
        result = import_wechat_records(
            [
                {
                    "id": "wx_1",
                    "session_id": "wxid_friend",
                    "sender": "我",
                    "is_self": True,
                    "timestamp": 1738713600,
                    "raw_type": 1,
                    "content": "你好",
                },
                {
                    "id": "wx_2",
                    "session_id": "wxid_friend",
                    "sender": "Alice",
                    "is_self": False,
                    "timestamp": 1738713660,
                    "raw_type": 34,
                    "content": "",
                },
            ]
        )

        self.assertEqual(len(result.messages), 2)
        self.assertEqual(result.messages[0].sender_role, "self")
        self.assertEqual(result.messages[1].sender_role, "other")
        self.assertEqual(result.messages[1].message_type, "voice")
        self.assertEqual(result.messages[1].text, "[语音消息]")
        self.assertEqual(result.messages[0].source, "wechat_local")

    def test_parse_datetime_accepts_unix_timestamp(self):
        parsed = parse_datetime("1738713600")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.year, 2025)


if __name__ == "__main__":
    unittest.main()
