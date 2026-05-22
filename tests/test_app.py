import unittest

from app import assign_self_sender, compute_metrics, parse_csv_content, parse_txt


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


if __name__ == "__main__":
    unittest.main()
