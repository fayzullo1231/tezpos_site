from django.test import SimpleTestCase, TestCase


class HealthEndpointTests(SimpleTestCase):
    def test_health_is_fast_ok(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("ok"), True)
        self.assertEqual(resp.json().get("service"), "tezpos-site")


class TelegramSyncEndpointTests(TestCase):
    def test_cron_without_secret_forbidden(self):
        resp = self.client.get("/accounts/cabinet/telegram-cron/")
        self.assertEqual(resp.status_code, 403)

    def test_cabinet_telegram_sync_requires_login(self):
        resp = self.client.get("/accounts/cabinet/telegram-sync/")
        self.assertIn(resp.status_code, (302, 401, 403))
