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

    def test_export_fills_optom_list_from_wholesale(self):
        from accounts.views import (
            _export_cell_value,
            _fill_list_prices_from_catalog,
            _map_product,
        )

        p = _map_product(
            {
                "id": "mayo",
                "name": "CHIMBOY",
                "price": 17000,
                "wholesale_price": 16400,
                "unit": "4 шт",
            }
        )
        pl_id = "1ebc3771-a015-4917-9454-53efdef82d01"
        price_lists = [{"id": pl_id, "name": "Optom", "is_selling": False}]
        _fill_list_prices_from_catalog(p, price_lists)
        by_id = {pl_id: price_lists[0]}
        self.assertAlmostEqual(
            float(_export_cell_value(p, f"pl_{pl_id}", by_id)),
            16400,
        )
        self.assertEqual(_export_cell_value(p, "unit", by_id), "шт")
        self.assertAlmostEqual(float(_export_cell_value(p, "wholesale_price", by_id)), 16400)

    def test_export_fills_selling_list_from_price(self):
        from accounts.views import (
            _export_cell_value,
            _fill_list_prices_from_catalog,
            _map_product,
        )

        p = _map_product({"id": "1", "name": "A", "price": 12000})
        pl_id = "sell-uuid"
        price_lists = [{"id": pl_id, "name": "Sotuv", "is_selling": True}]
        _fill_list_prices_from_catalog(p, price_lists)
        by_id = {pl_id: price_lists[0]}
        self.assertAlmostEqual(float(_export_cell_value(p, f"pl_{pl_id}", by_id)), 12000)


class CatalogPagingTests(SimpleTestCase):
    def test_twenty_item_page_is_not_the_end(self):
        from accounts.tezpos_api import catalog_has_more

        self.assertTrue(
            catalog_has_more(actual=20, requested=100, has_next=False, total=0, loaded=20)
        )
        self.assertTrue(
            catalog_has_more(actual=20, requested=100, has_next=True, total=20, loaded=20)
        )
        self.assertFalse(
            catalog_has_more(actual=7, requested=100, has_next=False, total=0, loaded=7)
        )
        self.assertFalse(
            catalog_has_more(actual=0, requested=100, has_next=False, total=1500, loaded=0)
        )
        self.assertTrue(
            catalog_has_more(actual=20, requested=100, has_next=False, total=1500, loaded=20)
        )

    def test_product_count_helper_exists(self):
        from accounts.tezpos_api import get_product_count

        self.assertTrue(callable(get_product_count))


class SalesPagingTests(SimpleTestCase):
    def test_sale_rows_from_results_items_and_list(self):
        from accounts.tezpos_api import _sale_rows

        self.assertEqual(len(_sale_rows({"results": [{"id": 1}]})), 1)
        self.assertEqual(len(_sale_rows({"items": [{"id": 2}]})), 1)
        self.assertEqual(len(_sale_rows({"sales": [{"id": 3}]})), 1)
        self.assertEqual(len(_sale_rows([{"id": 4}])), 1)

    def test_all_true_list_is_complete_even_at_twenty(self):
        from unittest.mock import patch
        from accounts.tezpos_api import get_sales

        with patch("accounts.tezpos_api.api_request") as mock_req:
            mock_req.return_value = [{"id": str(i)} for i in range(20)]
            rows = get_sales("t", "s", date_from="2026-08-19", date_to="2026-08-19")
        self.assertEqual(len(rows), 20)
        self.assertEqual(mock_req.call_count, 1)

    def test_paged_twenty_is_not_the_end(self):
        from unittest.mock import patch
        from accounts.tezpos_api import get_sales

        def fake(_method, _path, **kwargs):
            q = kwargs.get("query") or {}
            if q.get("all") == "true":
                return {
                    "results": [{"id": f"a{i}"} for i in range(20)],
                    "next": None,
                    "count": 45,
                }
            page = int(q.get("page") or 1)
            if page == 2:
                return {
                    "results": [{"id": f"b{i}"} for i in range(20)],
                    "next": "x",
                    "count": 45,
                }
            if page == 3:
                return {
                    "results": [{"id": f"c{i}"} for i in range(5)],
                    "next": None,
                    "count": 45,
                }
            return {"results": []}

        with patch("accounts.tezpos_api.api_request", side_effect=fake):
            rows = get_sales("t", "s", date_from="2026-08-19", date_to="2026-08-19")
        self.assertEqual(len(rows), 45)

    def test_get_sales_for_day_pages_beyond_one(self):
        from unittest.mock import patch
        from accounts.tezpos_api import get_sales_for_day

        calls = []

        def fake(_method, _path, **kwargs):
            q = kwargs.get("query") or {}
            calls.append(q)
            if q.get("all") == "true":
                return {
                    "results": [{"id": f"d{i}"} for i in range(20)],
                    "next": "x",
                    "count": 25,
                }
            page = int(q.get("page") or 1)
            if page == 2:
                return {
                    "results": [{"id": f"e{i}"} for i in range(5)],
                    "next": None,
                    "count": 25,
                }
            return {"results": []}

        with patch("accounts.tezpos_api.api_request", side_effect=fake):
            rows = get_sales_for_day("t", "s", "2026-08-19")
        self.assertEqual(len(rows), 25)
        self.assertGreaterEqual(len(calls), 2)

    def test_merge_shift_summary_fills_totals(self):
        from accounts.tezpos_api import merge_shift_summary

        sh = merge_shift_summary(
            {"id": "1", "opened_at": "2026-08-19T08:00:00Z"},
            {"sales_count": 12, "sales_total": "150000"},
        )
        self.assertEqual(sh["sales_count"], 12)
        self.assertEqual(sh["sales_total"], "150000")


class CostedProfitTests(SimpleTestCase):
    def test_profit_is_selling_minus_purchase(self):
        from decimal import Decimal
        from accounts.views import (
            _estimate_sale_profit,
            _map_product,
            _products_by_name,
        )

        p = _map_product(
            {"id": "1", "name": "Choy", "price": 1200, "cost_price": 1000}
        )
        sale = {
            "id": "s1",
            "total": "2400",
            "items": [
                {
                    "product_id": "1",
                    "quantity": 2,
                    "unit_price": 1200,
                    "total": 2400,
                }
            ],
        }
        cost, profit = _estimate_sale_profit(
            sale, {"1": p}, Decimal("2400"), _products_by_name([p])
        )
        self.assertEqual(float(cost), 2000)
        self.assertEqual(float(profit), 400)

    def test_items_without_cost_do_not_inflate_profit(self):
        from decimal import Decimal
        from accounts.views import (
            _aggregate_price_list_stats,
            _estimate_sale_profit,
            _map_product,
            _products_by_name,
        )

        priced = _map_product(
            {"id": "1", "name": "A", "price": 1200, "cost_price": 1000}
        )
        free = _map_product({"id": "2", "name": "B", "price": 5000, "cost_price": 0})
        by_id = {"1": priced, "2": free}
        by_name = _products_by_name([priced, free])
        sale = {
            "id": "s2",
            "total": "6200",
            "items": [
                {"product_id": "1", "quantity": 1, "unit_price": 1200, "total": 1200},
                {"product_id": "2", "quantity": 1, "unit_price": 5000, "total": 5000},
            ],
        }
        cost, profit = _estimate_sale_profit(
            sale, by_id, Decimal("6200"), by_name
        )
        self.assertEqual(float(cost), 1000)
        self.assertEqual(float(profit), 200)

        rows = _aggregate_price_list_stats([sale], by_id, by_name, [])
        jami = next(r for r in rows if r.get("is_total"))
        self.assertAlmostEqual(jami["revenue"], 6200)
        self.assertAlmostEqual(jami["cost"], 1000)
        self.assertAlmostEqual(jami["profit"], 200)
        self.assertLess(jami["markup"], 50)

    def test_price_list_retail_and_optom_profit_per_unit(self):
        from accounts.views import (
            SELLING_LIST_ID,
            _aggregate_price_list_stats,
            _map_product,
            _products_by_name,
        )

        p = _map_product(
            {
                "id": "1",
                "name": "Test",
                "price": 12000,
                "cost_price": 10000,
                "wholesale_price": 11000,
            }
        )
        by_id = {"1": p}
        by_name = _products_by_name([p])
        sales = [
            {
                "id": "r1",
                "items": [
                    {
                        "product_id": "1",
                        "quantity": 1,
                        "unit_price": 12000,
                        "total": 12000,
                    }
                ],
            },
            {
                "id": "o1",
                "items": [
                    {
                        "product_id": "1",
                        "quantity": 1,
                        "unit_price": 11000,
                        "total": 11000,
                    }
                ],
            },
        ]
        rows = _aggregate_price_list_stats(sales, by_id, by_name, [])
        retail = next(r for r in rows if r.get("id") == SELLING_LIST_ID)
        optom = next(r for r in rows if r.get("id") != SELLING_LIST_ID and not r.get("is_total"))
        self.assertAlmostEqual(retail["profit"], 2000)
        self.assertAlmostEqual(optom["profit"], 1000)
        self.assertAlmostEqual(retail["revenue"] - retail["cost"], retail["profit"])
        self.assertAlmostEqual(optom["revenue"] - optom["cost"], optom["profit"])

    def test_top_stats_retail_line_at_wholesale_price_uses_selling(self):
        from decimal import Decimal
        from datetime import date

        from accounts.views import _map_product, _product_sales_stats

        p = _map_product(
            {
                "id": "1",
                "name": "BON DEBUT",
                "price": 49000,
                "cost_price": 48300,
                "wholesale_price": 48500,
            }
        )
        by_id = {"1": p}
        sale = {
            "id": "s1",
            "completed_at": "2026-09-01T12:00:00+05:00",
            "price_list_id": "selling",
            "items": [
                {
                    "product_id": "1",
                    "quantity": 79,
                    "unit_price": 48500,
                    "total": 79 * 48500,
                }
            ],
        }
        rows, summary = _product_sales_stats(
            {"s1": sale},
            by_id,
            {},
            [],
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 1),
            prefer_txn_total=False,
        )
        row = next(r for r in rows if r.get("sold_in_period"))
        self.assertEqual(row["qty_selling"], 79.0)
        self.assertEqual(row["qty_wholesale"], 0.0)
        self.assertAlmostEqual(row["revenue"], 79 * 49000)
        self.assertAlmostEqual(row["profit"], 79 * 700)
        self.assertAlmostEqual(row["profit_selling"], 79 * 700)

    def test_top_stats_txn_total_matches_sale_line_total(self):
        """Analitika: mahsulot yig‘indisi = chekdagi haqiqiy total."""
        from datetime import date

        from accounts.views import _map_product, _product_sales_stats

        p = _map_product(
            {
                "id": "1",
                "name": "BON DEBUT",
                "price": 49000,
                "cost_price": 48300,
                "wholesale_price": 48500,
            }
        )
        by_id = {"1": p}
        sale = {
            "id": "s1",
            "completed_at": "2026-09-08T12:00:00+05:00",
            "total": 79 * 48500,
            "price_list_id": "selling",
            "items": [
                {
                    "product_id": "1",
                    "quantity": 79,
                    "unit_price": 48500,
                    "total": 79 * 48500,
                    "batch_allocations": [
                        {"quantity": 79, "unit_cost": 48300},
                    ],
                }
            ],
        }
        rows, summary = _product_sales_stats(
            {"s1": sale},
            by_id,
            {},
            [],
            period_start=date(2026, 9, 8),
            period_end=date(2026, 9, 8),
            prefer_txn_total=True,
        )
        row = next(r for r in rows if r.get("sold_in_period"))
        self.assertAlmostEqual(row["revenue"], 79 * 48500)
        self.assertAlmostEqual(summary["total_revenue"], 79 * 48500)
        self.assertEqual(summary["checks"], 1)
        self.assertAlmostEqual(row["profit"], 79 * 200)

    def test_top_stats_wholesale_list_retail_price_counts_as_selling(self):
        from datetime import date

        from accounts.views import _map_product, _product_sales_stats

        p = _map_product(
            {
                "id": "mayo",
                "name": "CHIMBOY MAYANEZ",
                "price": 17000,
                "cost_price": 15900,
                "wholesale_price": 16400,
            }
        )
        by_id = {"mayo": p}
        sales = {
            "s1": {
                "id": "s1",
                "completed_at": "2026-09-02T12:00:00+05:00",
                "price_list_id": "optom",
                "items": [
                    {
                        "product_id": "mayo",
                        "quantity": 5,
                        "unit_price": 17000,
                        "total": 85000,
                    },
                    {
                        "product_id": "mayo",
                        "quantity": 13,
                        "unit_price": 16400,
                        "total": 213200,
                    },
                ],
            },
        }
        rows, summary = _product_sales_stats(
            sales,
            by_id,
            {},
            [{"id": "optom", "name": "Optom", "type": "wholesale"}],
            period_start=date(2026, 9, 2),
            period_end=date(2026, 9, 2),
        )
        row = next(r for r in rows if r.get("sold_in_period"))
        self.assertAlmostEqual(row["qty_selling"], 5)
        self.assertAlmostEqual(row["qty_wholesale"], 13)
        self.assertAlmostEqual(row["revenue_selling"], 85000)
        self.assertAlmostEqual(row["revenue_wholesale"], 213200)
        self.assertAlmostEqual(summary["qty_selling"], 5)
        self.assertAlmostEqual(summary["qty_wholesale"], 13)


class ProfitMarginRegressionTests(SimpleTestCase):
    """Foyda/marja: profit = tushum - tannarx; marja = foyda / tushum × 100."""

    def _maxev_product(self):
        from accounts.views import _map_product

        return _map_product(
            {
                "id": "maxev",
                "name": "Maxev",
                "price": 14000,
                "cost_price": 13000,
                "wholesale_price": 13500,
            }
        )

    def _line_fin(self, product, unit_price, qty=1):
        from accounts.views import _compute_line_financials, _products_by_name

        by_id = {product.id: product}
        by_name = _products_by_name([product])
        item = {
            "product_id": product.id,
            "quantity": qty,
            "unit_price": unit_price,
            "total": unit_price * qty,
        }
        return _compute_line_financials(
            item,
            sale_pl=None,
            products_by_id=by_id,
            products_by_name=by_name,
            price_lists=[],
            selling_list_ids=set(),
        )

    def test_1_retail_profit_and_margin(self):
        from accounts.views import _margin_on_revenue

        p = self._maxev_product()
        qty, rev, cost, profit, _pl = self._line_fin(p, 14000, 1)
        self.assertEqual(float(qty), 1)
        self.assertAlmostEqual(float(rev), 14000)
        self.assertAlmostEqual(float(cost), 13000)
        self.assertAlmostEqual(float(profit), 1000)
        self.assertAlmostEqual(_margin_on_revenue(profit, rev), 1000 / 14000 * 100)

    def test_2_wholesale_profit_and_margin(self):
        from accounts.views import _margin_on_revenue

        p = self._maxev_product()
        qty, rev, cost, profit, _pl = self._line_fin(p, 13500, 1)
        self.assertAlmostEqual(float(rev), 13500)
        self.assertAlmostEqual(float(profit), 500)
        self.assertAlmostEqual(_margin_on_revenue(profit, rev), 500 / 13500 * 100, places=5)

    def test_3_wholesale_qty_10(self):
        from accounts.views import _margin_on_revenue

        p = self._maxev_product()
        qty, rev, cost, profit, _pl = self._line_fin(p, 13500, 10)
        self.assertAlmostEqual(float(rev), 135000)
        self.assertAlmostEqual(float(cost), 130000)
        self.assertAlmostEqual(float(profit), 5000)
        self.assertAlmostEqual(_margin_on_revenue(profit, rev), 5000 / 135000 * 100, places=5)

    def test_4_negative_profit(self):
        from accounts.views import _margin_on_revenue

        p = self._maxev_product()
        _qty, rev, _cost, profit, _pl = self._line_fin(p, 12000, 1)
        self.assertAlmostEqual(float(profit), -1000)
        self.assertAlmostEqual(_margin_on_revenue(profit, rev), -1000 / 12000 * 100, places=4)

    def test_5_zero_revenue_margin(self):
        from accounts.views import _margin_on_revenue

        self.assertEqual(_margin_on_revenue(0, 0), 0.0)
        self.assertEqual(_margin_on_revenue(100, 0), 0.0)

    def test_6_mixed_retail_and_wholesale_dashboard(self):
        from accounts.views import (
            SELLING_LIST_ID,
            _aggregate_price_list_stats,
            _margin_on_revenue,
            _products_by_name,
        )

        p = self._maxev_product()
        by_id = {p.id: p}
        by_name = _products_by_name([p])
        sales = [
            {
                "id": "r1",
                "items": [
                    {
                        "product_id": p.id,
                        "quantity": 1,
                        "unit_price": 14000,
                        "total": 14000,
                    }
                ],
            },
            {
                "id": "w1",
                "items": [
                    {
                        "product_id": p.id,
                        "quantity": 2,
                        "unit_price": 13500,
                        "total": 27000,
                    }
                ],
            },
        ]
        rows = _aggregate_price_list_stats(sales, by_id, by_name, [])
        retail = next(r for r in rows if r.get("id") == SELLING_LIST_ID)
        optom = next(r for r in rows if r.get("id") != SELLING_LIST_ID and not r.get("is_total"))
        jami = next(r for r in rows if r.get("is_total"))
        self.assertAlmostEqual(retail["revenue"], 14000)
        self.assertAlmostEqual(optom["revenue"], 27000)
        self.assertAlmostEqual(jami["revenue"], 41000)
        self.assertAlmostEqual(jami["cost"], 39000)
        self.assertAlmostEqual(jami["profit"], 2000)
        self.assertAlmostEqual(jami["margin"], _margin_on_revenue(2000, 41000), places=5)
        self.assertAlmostEqual(jami["checks"], 2)


class TopStatsDailyTests(SimpleTestCase):
    def _product(self):
        from accounts.views import _map_product

        return _map_product(
            {
                "id": "cola",
                "name": "Coca Cola 1.5L",
                "barcode": "8600123456789",
                "price": 10000,
                "cost_price": 8000,
                "wholesale_price": 9000,
            }
        )

    def test_daily_retail_and_wholesale_split(self):
        from datetime import date

        from accounts.views import _product_sales_stats

        p = self._product()
        by_id = {p.id: p}
        sales = {
            "s1": {
                "id": "s1",
                "completed_at": "2026-09-01T12:00:00+05:00",
                "items": [
                    {
                        "product_id": p.id,
                        "quantity": 20,
                        "unit_price": 9000,
                        "total": 180000,
                    },
                    {
                        "product_id": p.id,
                        "quantity": 15,
                        "unit_price": 10000,
                        "total": 150000,
                    },
                ],
            },
            "s2": {
                "id": "s2",
                "completed_at": "2026-09-02T12:00:00+05:00",
                "items": [
                    {
                        "product_id": p.id,
                        "quantity": 10,
                        "unit_price": 9000,
                        "total": 90000,
                    },
                    {
                        "product_id": p.id,
                        "quantity": 25,
                        "unit_price": 10000,
                        "total": 250000,
                    },
                ],
            },
        }
        rows, summary = _product_sales_stats(
            sales,
            by_id,
            {},
            [],
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 2),
        )
        row = next(r for r in rows if r.get("sold_in_period"))
        self.assertEqual(row["barcode"], "8600123456789")
        self.assertAlmostEqual(row["qty"], 70)
        self.assertAlmostEqual(row["qty_wholesale"], 30)
        self.assertAlmostEqual(row["qty_selling"], 40)
        self.assertAlmostEqual(row["revenue_wholesale"], 270000)
        self.assertAlmostEqual(row["revenue_selling"], 400000)
        self.assertAlmostEqual(row["revenue"], 670000)
        self.assertAlmostEqual(row["cost_total"], 560000)
        self.assertAlmostEqual(row["profit"], 110000)
        self.assertEqual(len(row["daily"]), 2)
        d1 = row["daily"][0]
        self.assertEqual(d1["date"], "2026-09-01")
        self.assertAlmostEqual(d1["wholesale_quantity"], 20)
        self.assertAlmostEqual(d1["retail_quantity"], 15)
        self.assertAlmostEqual(d1["total_quantity"], 35)
        self.assertAlmostEqual(summary["total_quantity"], 70)
        self.assertEqual(summary["checks"], 2)
        self.assertEqual(summary["checks_count"], 2)
        self.assertGreaterEqual(summary["checks_selling"], 1)
        self.assertGreaterEqual(summary["checks_wholesale"], 1)
        self.assertIn("profit_selling", summary)
        self.assertIn("profit_wholesale", summary)
        self.assertIn("cost_amount", summary)

    def test_channel_wholesale_only(self):
        from datetime import date

        from accounts.views import _product_sales_stats

        p = self._product()
        by_id = {p.id: p}
        sales = {
            "s1": {
                "id": "s1",
                "completed_at": "2026-09-01T12:00:00+05:00",
                "items": [
                    {
                        "product_id": p.id,
                        "quantity": 50,
                        "unit_price": 9000,
                        "total": 450000,
                    },
                    {
                        "product_id": p.id,
                        "quantity": 100,
                        "unit_price": 10000,
                        "total": 1000000,
                    },
                ],
            },
        }
        rows, summary = _product_sales_stats(
            sales,
            by_id,
            {},
            [],
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 1),
            channel="wholesale",
        )
        row = next(r for r in rows if r.get("sold_in_period"))
        self.assertAlmostEqual(row["qty"], 50)
        self.assertAlmostEqual(row["revenue"], 450000)
        self.assertAlmostEqual(summary["revenue_wholesale"], 450000)

    def test_cancelled_sale_excluded(self):
        from datetime import date

        from accounts.views import _is_countable_sale, _product_sales_stats

        self.assertFalse(_is_countable_sale({"status": "cancelled"}))
        p = self._product()
        by_id = {p.id: p}
        sales = {
            "ok": {
                "id": "ok",
                "completed_at": "2026-09-01T12:00:00+05:00",
                "items": [
                    {
                        "product_id": p.id,
                        "quantity": 10,
                        "unit_price": 10000,
                        "total": 100000,
                    }
                ],
            },
            "bad": {
                "id": "bad",
                "status": "cancelled",
                "completed_at": "2026-09-01T13:00:00+05:00",
                "items": [
                    {
                        "product_id": p.id,
                        "quantity": 99,
                        "unit_price": 10000,
                        "total": 990000,
                    }
                ],
            },
        }
        rows, _summary = _product_sales_stats(
            sales,
            by_id,
            {},
            [],
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 1),
        )
        row = next(r for r in rows if r.get("sold_in_period"))
        self.assertAlmostEqual(row["qty"], 10)


class TopRowsViewPrepareTests(SimpleTestCase):
    def test_prepare_respects_limit_filter_and_sort(self):
        from accounts.views import _prepare_top_rows_for_view

        rows = [
            {
                "id": "a",
                "name": "A",
                "qty": 5,
                "revenue": 50,
                "qty_selling": 5,
                "qty_wholesale": 0,
                "revenue_selling": 50,
                "revenue_wholesale": 0,
                "profit_selling": 10,
                "profit_wholesale": 0,
                "sold_in_period": True,
                "status": "sold",
            },
            {
                "id": "b",
                "name": "B",
                "qty": 0,
                "revenue": 0,
                "qty_selling": 0,
                "qty_wholesale": 0,
                "revenue_selling": 0,
                "revenue_wholesale": 0,
                "profit_selling": 0,
                "profit_wholesale": 0,
                "sold_in_period": False,
                "status": "never",
            },
            {
                "id": "c",
                "name": "C",
                "qty": 9,
                "revenue": 90,
                "qty_selling": 9,
                "qty_wholesale": 0,
                "revenue_selling": 90,
                "revenue_wholesale": 0,
                "profit_selling": 20,
                "profit_wholesale": 0,
                "sold_in_period": True,
                "status": "sold",
            },
        ]
        top2 = _prepare_top_rows_for_view(rows, limit=2, sort="qty_desc")
        self.assertEqual([r["id"] for r in top2], ["c", "a"])
        unsold = _prepare_top_rows_for_view(rows, status_filter="unsold", limit=50)
        self.assertEqual([r["id"] for r in unsold], ["b"])
        all_rows = _prepare_top_rows_for_view(rows, status_filter="all", limit=50000)
        self.assertEqual(len(all_rows), 3)


class SalesShareTests(SimpleTestCase):
    def test_product_share_of_period_revenue(self):
        from datetime import date

        from accounts.views import _map_product, _product_sales_stats

        a = _map_product(
            {"id": "a", "name": "A", "price": 1000, "cost_price": 500}
        )
        b = _map_product(
            {"id": "b", "name": "B", "price": 1000, "cost_price": 500}
        )
        sales = {
            "s1": {
                "id": "s1",
                "completed_at": "2026-09-08T10:00:00+05:00",
                "items": [
                    {
                        "product_id": a.id,
                        "quantity": 1,
                        "unit_price": 1000,
                        "total": 1000,
                    },
                    {
                        "product_id": b.id,
                        "quantity": 3,
                        "unit_price": 1000,
                        "total": 3000,
                    },
                ],
            }
        }
        rows, _summary = _product_sales_stats(
            sales,
            {a.id: a, b.id: b},
            {},
            [],
            period_start=date(2026, 9, 8),
            period_end=date(2026, 9, 8),
            sold_only=True,
        )
        by_id = {r["id"]: r for r in rows}
        self.assertAlmostEqual(by_id["a"]["share"], 25.0, places=1)
        self.assertAlmostEqual(by_id["b"]["share"], 75.0, places=1)
        self.assertAlmostEqual(by_id["b"]["margin_percent"], 75.0, places=1)


class CatalogEnrichTopsTests(SimpleTestCase):
    def test_api_top_items_get_name_and_prices_from_catalog(self):
        from accounts.views import _map_product, _top_products_from_api_items

        p = _map_product(
            {
                "id": "ef2d2718-aaaa-bbbb-cccc-ddddeeee0011",
                "name": "Pepsi 1L",
                "price": 12000,
                "cost_price": 9000,
                "wholesale_price": 10000,
                "list_prices": {"optom-list": 10000},
            }
        )
        rows = _top_products_from_api_items(
            [{"product_id": p.id, "quantity": 10}],
            {p.id: p},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Pepsi 1L")
        self.assertAlmostEqual(rows[0]["selling"], 12000)
        self.assertAlmostEqual(rows[0]["wholesale"], 10000)
        self.assertAlmostEqual(rows[0]["cost"], 9000)
        self.assertAlmostEqual(rows[0]["revenue"], 120000)

    def test_unsold_product_keeps_catalog_prices(self):
        from datetime import date

        from accounts.views import _map_product, _product_sales_stats

        sold = _map_product(
            {
                "id": "sold1",
                "name": "Sold Drink",
                "price": 11000,
                "cost_price": 8000,
                "wholesale_price": 9500,
            }
        )
        idle = _map_product(
            {
                "id": "idle1",
                "name": "Idle Snack",
                "price": 5000,
                "cost_price": 3000,
                "wholesale_price": 4000,
                "list_prices": {"optom": 4000},
            }
        )
        by_id = {sold.id: sold, idle.id: idle}
        sales = {
            "s1": {
                "id": "s1",
                "completed_at": "2026-09-08T12:00:00+05:00",
                "items": [
                    {
                        "product_id": sold.id,
                        "product_name": sold.name,
                        "quantity": 2,
                        "unit_price": 11000,
                        "total": 22000,
                    }
                ],
            }
        }
        rows, summary = _product_sales_stats(
            sales,
            by_id,
            {},
            [{"id": "optom", "name": "Optom", "is_active": True}],
            period_start=date(2026, 9, 8),
            period_end=date(2026, 9, 8),
            sold_only=False,
        )
        idle_row = next(r for r in rows if r["id"] == idle.id)
        self.assertFalse(idle_row["sold_in_period"])
        self.assertEqual(idle_row["name"], "Idle Snack")
        self.assertAlmostEqual(idle_row["selling"], 5000)
        self.assertAlmostEqual(idle_row["wholesale"], 4000)
        self.assertAlmostEqual(idle_row["cost"], 3000)
        self.assertGreaterEqual(summary["unsold"], 1)

    def test_placeholder_name_detection(self):
        from accounts.views import _is_placeholder_product_name

        self.assertTrue(_is_placeholder_product_name("Mahsulot ef2d2718"))
        self.assertTrue(_is_placeholder_product_name("Mahsulot"))
        self.assertFalse(_is_placeholder_product_name("Pepsi 1L"))


class BarcodeExcelTemplateTests(SimpleTestCase):
    def test_all_codes_go_in_one_cell_with_comma_and_newline(self):
        from accounts.views import (
            _export_cell_value,
            _map_product,
            format_barcodes_excel_cell,
            parse_barcodes_cell,
        )

        codes = [f"478{i:010d}" for i in range(1, 31)]
        cell = format_barcodes_excel_cell(codes)
        self.assertIn(",\n", cell)
        self.assertTrue(cell.endswith(","))
        self.assertEqual(parse_barcodes_cell(cell), codes)

        p = _map_product(
            {
                "id": "1",
                "name": "Choy",
                "barcode": codes[0],
                "barcodes": [{"code": c} for c in codes],
            }
        )
        self.assertEqual(p.barcode_list, codes)
        exported = _export_cell_value(p, "barcode", {})
        self.assertEqual(parse_barcodes_cell(exported), codes)
        self.assertEqual(exported.split("\n")[0], f"{codes[0]},")
        self.assertGreaterEqual(len(exported.split("\n")), 30)

    def test_comma_separated_barcode_field_splits(self):
        from accounts.views import _map_product, parse_barcodes_cell

        p = _map_product(
            {
                "id": "2",
                "name": "X",
                "barcode": "8003407192271,8003407261168,8003407261175",
            }
        )
        self.assertEqual(
            p.barcode_list,
            ["8003407192271", "8003407261168", "8003407261175"],
        )
        self.assertEqual(len(parse_barcodes_cell("8001,\n8002,")), 2)

    def test_import_row_reads_template_cell(self):
        from accounts.views import _product_payload_from_import_row, format_barcodes_excel_cell

        cell = format_barcodes_excel_cell(["111", "222", "333"])
        payload, err = _product_payload_from_import_row(
            {"name": "Choy", "barcode": cell, "selling_price": 1200}
        )
        self.assertIsNone(err)
        self.assertEqual(payload["barcodes"], ["111", "222", "333"])
        self.assertEqual(payload["barcode"], "111")


class ProductExportSourceTests(SimpleTestCase):
    def test_snapshot_preferred_over_capped_page(self):
        snap = [{"id": str(i)} for i in range(1516)]
        paged = [{"id": str(i)} for i in range(200)]
        picked = snap if len(snap) > 200 else paged
        self.assertEqual(len(picked), 1516)
