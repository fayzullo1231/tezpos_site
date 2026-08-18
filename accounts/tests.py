from django.test import SimpleTestCase, TestCase

from accounts.telegram_bot import (
    build_shift_excel,
    format_money,
    format_money_som,
    format_qty,
    format_sale_product_line,
    parse_recipient_line,
    parse_recipients,
)
from accounts.views import (
    _map_product,
    _parse_list_prices,
    _payment_label,
    _receipt_number,
    _sale_items,
)


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


class ExcelMoneyFormatTests(SimpleTestCase):
    def test_dot_thousands(self):
        self.assertEqual(format_money(1000), "1.000")
        self.assertEqual(format_money(10000), "10.000")
        self.assertEqual(format_money(100000), "100.000")
        self.assertEqual(format_money(1000000), "1.000.000")
        self.assertEqual(format_money(19000), "19.000")
        self.assertEqual(format_money_som(19000), "19.000 so'm")

    def test_product_line_in_one_cell(self):
        line = format_sale_product_line("bon saryog", 10, "шт", 33000, 330000)
        self.assertEqual(line, "bon saryog 10 шт x 33.000 = 330.000")
        self.assertEqual(format_qty(10.5), "10.5")


class ShiftExcelLayoutTests(SimpleTestCase):
    def test_daily_sales_sheet_has_receipt_products_payment_profit(self):
        from io import BytesIO

        from openpyxl import load_workbook

        products = (
            "bon saryog 10 шт x 33.000 = 330.000\n"
            "bon saryog 10 кг x 33.000 = 330.000"
        )
        raw = build_shift_excel(
            business_name="Kulol Optom",
            shift={"status_label": "Yopilgan", "checks": 1, "gross": 330000, "profit": 80000},
            sales_rows=[
                {
                    "receipt_no": "142",
                    "time": "19.08.2026 10:17",
                    "customer": "Ali",
                    "products_text": products,
                    "payment": "Naqt",
                    "total": 330000,
                    "profit": 80000,
                }
            ],
            price_lists=[],
            credit_rows=[],
            sold_product_rows=[
                {
                    "name": "bon saryog",
                    "barcode": "123",
                    "qty": 10,
                    "unit": "шт",
                    "revenue": 330000,
                    "profit": 80000,
                },
                {"name": "zero", "qty": 0, "unit": "dona", "revenue": 0, "profit": 0},
            ],
        )
        wb = load_workbook(BytesIO(raw))
        daily = wb["Kunlik sotuv"]
        self.assertEqual(
            [c.value for c in daily[1]],
            ["Chek raqami", "Vaqt", "Mijoz", "Mahsulotlar", "To‘lov", "Summa", "Foyda"],
        )
        self.assertEqual(daily["A2"].value, "142")
        self.assertEqual(daily["B2"].value, "19.08.2026 10:17")
        self.assertEqual(daily["C2"].value, "Ali")
        self.assertIn("bon saryog 10 шт x 33.000 = 330.000", daily["D2"].value)
        self.assertIn("\n", daily["D2"].value)
        self.assertEqual(daily["E2"].value, "Naqt")
        self.assertEqual(daily["F2"].value, "330.000 so'm")
        self.assertEqual(daily["G2"].value, "80.000 so'm")

        sold = wb["Sotilgan mahsulotlar"]
        self.assertEqual(sold["B2"].value, "bon saryog")
        self.assertEqual(sold["D2"].value, "10")
        self.assertEqual(sold["F2"].value, "330.000 so'm")


class SaleExcelHelpersTests(SimpleTestCase):
    def test_receipt_and_items_and_payment(self):
        sale = {"id": "abc", "receipt_number": 142, "payment_method": "cash"}
        detail = {
            "items": [
                {
                    "product_name": "bon saryog",
                    "quantity": 10,
                    "unit": "шт",
                    "unit_price": 33000,
                    "total": 330000,
                }
            ]
        }
        self.assertEqual(_receipt_number(sale, detail), "142")
        self.assertEqual(len(_sale_items(detail)), 1)
        self.assertEqual(_payment_label("credit"), "Qarz")
        self.assertEqual(_payment_label("card"), "Karta")
        self.assertEqual(_payment_label("cash"), "Naqt")


class WarehouseProductMapTests(SimpleTestCase):
    def test_cost_stock_and_dict_list_prices(self):
        p = _map_product(
            {
                "id": "1",
                "name": "Bon",
                "quantity": 10,
                "price": 33000,
                "cost_price": 20000,
                "list_prices": {"optom": 25000},
            }
        )
        self.assertEqual(float(p.stock_qty), 10)
        self.assertEqual(float(p.cost_price), 20000)
        self.assertEqual(float(p.selling_price), 33000)
        self.assertEqual(float(p.list_prices["optom"]), 25000)
        self.assertEqual(float(p.stock_qty) * float(p.cost_price), 200000)
        self.assertEqual(float(p.stock_qty) * float(p.selling_price) - float(p.stock_qty) * float(p.cost_price), 130000)

    def test_purchase_price_and_list_array(self):
        p = _map_product(
            {
                "id": "2",
                "name": "X",
                "stock_qty": 3,
                "selling_price": 1000,
                "purchase_price": 400,
                "list_prices": [{"price_list_id": "optom", "price": 800}],
            }
        )
        self.assertEqual(float(p.cost_price), 400)
        self.assertEqual(float(p.stock_qty), 3)
        self.assertEqual(float(p.selling_price), 1000)
        self.assertEqual(_parse_list_prices([{"price_list_id": "optom", "price": 800}])["optom"], p.list_prices["optom"])
        self.assertEqual(float(p.stock_qty) * float(p.list_prices["optom"]), 2400)
