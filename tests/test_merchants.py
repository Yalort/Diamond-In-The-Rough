from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ctp500_app.merchant_store import MerchantStore
from ctp500_app.merchants import MerchantRecord, MerchantStockLine, MerchantTag, MerchantTransaction, PriceRule, STOCK_QUANTITY_RANGES, bundle_as_merchant, final_price, generate_stock, haggle, reconciliation_lines
from ctp500_app.models import GearItem


def item(name: str, category: str = "Weapons", manufacturer: str = "Acme", price: str = "100", rank: str = "2", row: int = 2) -> GearItem:
    return GearItem(category, 1, row, (("Name", name), ("Manufacturer", manufacturer), ("Price", price), ("Rank", rank)), name, manufacturer, "Rifle", rank, price)


class MerchantTests(unittest.TestCase):
    def test_hard_tags_and_seeded_generation(self) -> None:
        stock = (item("A"), item("B", category="Apparel", row=3), item("C", manufacturer="Bad", row=4))
        record = MerchantRecord(seed="same", tags=[MerchantTag("Guarantee", "Category", "Weapons"), MerchantTag("Forbid", "Manufacturer", "Bad")])
        self.assertIn("Only 1 eligible", generate_stock(record, stock, "digest", count=3)[0])
        self.assertEqual([line.name for line in record.stock], ["A"])
        self.assertTrue(all(line.category == "Weapons" for line in record.stock))

    def test_wealth_targets_and_fourth_tier_includes_all_eligible(self) -> None:
        stock = tuple(item(f"Item {index}", row=index + 2) for index in range(180))
        wealth_one = MerchantRecord(seed="one", wealth_tier=1)
        generate_stock(wealth_one, stock, "digest")
        self.assertEqual(len(wealth_one.stock), 50)
        wealth_three = MerchantRecord(seed="three", wealth_tier=3)
        generate_stock(wealth_three, stock, "digest")
        self.assertEqual(len(wealth_three.stock), 150)
        wealth_four = MerchantRecord(seed="four", wealth_tier=4)
        generate_stock(wealth_four, stock[:120], "digest")
        self.assertEqual(len(wealth_four.stock), 120)

    def test_multi_value_tag_uses_or_and_category_quantities_are_bounded(self) -> None:
        stock = (item("Weapon", "Weapons", row=2), item("Suit", "Apparel", row=3), item("Med", "Gene Treatments", row=4))
        record = MerchantRecord(seed="ranges", tags=[MerchantTag("Guarantee", "Category", values=["Weapons", "Apparel"])])
        generate_stock(record, stock, "digest", count=3)
        self.assertEqual({line.category for line in record.stock}, {"Weapons", "Apparel"})
        for line in record.stock:
            low, high = STOCK_QUANTITY_RANGES[line.category]
            self.assertLessEqual(low, line.quantity); self.assertLessEqual(line.quantity, high)

    def test_layered_price_and_haggling(self) -> None:
        line = MerchantStockLine.from_item(item("A"))
        record = MerchantRecord(wealth_tier=2, price_rules=[PriceRule("Global", "", "Percent", 10), PriceRule("Category", "Weapons", "Fixed", 5)])
        self.assertEqual(final_price(record, line), 115)
        dc, outcome, price = haggle(record, line, 30)
        self.assertEqual(dc, 18); self.assertIn("30%", outcome); self.assertEqual(price, 80.5)

    def test_bundle_is_independent_and_store_round_trips(self) -> None:
        bundle = MerchantRecord(record_type="Bundle", name="Weapons", stock=[MerchantStockLine.from_item(item("A"))])
        merchant = bundle_as_merchant(bundle, "Shop")
        merchant.stock[0].quantity = 0
        self.assertEqual(bundle.stock[0].quantity, 1)
        with tempfile.TemporaryDirectory() as directory:
            store = MerchantStore(Path(directory) / "data.db")
            store.save(merchant)
            self.assertEqual(store.get(merchant.id).name, "Shop")

    def test_saving_new_merchant_with_transaction_creates_parent_first(self) -> None:
        merchant = MerchantRecord(name="New shop")
        transaction = MerchantTransaction(item_name="A", quantity=1, final_price=100)
        with tempfile.TemporaryDirectory() as directory:
            store = MerchantStore(Path(directory) / "data.db")
            store.save_with_transaction(merchant, transaction)
            self.assertEqual(store.get(merchant.id).name, "New shop")
            self.assertEqual([entry.id for entry in store.transactions(merchant.id)], [transaction.id])

    def test_reconciliation_detects_changed_snapshot(self) -> None:
        record = MerchantRecord(catalog_digest="old", stock=[MerchantStockLine.from_item(item("A"))])
        self.assertEqual(len(reconciliation_lines(record, (item("A", price="200"),), "new")), 1)
