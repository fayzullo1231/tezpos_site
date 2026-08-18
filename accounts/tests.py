from django.test import SimpleTestCase, TestCase

from accounts.telegram_bot import parse_recipient_line, parse_recipients


class HealthEndpointTests(SimpleTestCase):
    def test_health_is_fast_ok(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("ok"), True)
        self.assertEqual(resp.json().get("service"), "tezpos-site")


class TelegramRecipientParseTests(SimpleTestCase):
    def test_numeric_and_invite_on_same_line(self):
        got = parse_recipients("@fayzullo_tech -1004303306685")
        self.assertIn("@fayzullo_tech", got)
        self.assertIn("-1004303306685", got)

    def test_invite_link(self):
        self.assertEqual(
            parse_recipient_line("https://t.me/+bF1iAizyQkA3M2Qy"),
            "invite:bF1iAizyQkA3M2Qy",
        )

    def test_supergroup_id(self):
        self.assertEqual(parse_recipient_line("-1004303306685"), "-1004303306685")


class TelegramSyncEndpointTests(TestCase):
    def test_cron_without_secret_forbidden(self):
        resp = self.client.get("/accounts/cabinet/telegram-cron/")
        self.assertEqual(resp.status_code, 403)

    def test_cabinet_telegram_sync_requires_login(self):
        resp = self.client.get("/accounts/cabinet/telegram-sync/")
        self.assertIn(resp.status_code, (302, 401, 403))
