"""Tests for human-readable asset-name catalogs."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.database import search_asset_names
from src.name_catalog import (
    import_column_name_catalog,
    import_name_catalog
)


class NameCatalogTests(unittest.TestCase):
    def test_imports_decimal_and_hexadecimal_uids(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            catalog = root / "names.csv"
            database = root / "assets.sqlite"

            catalog.write_text(
                "\n".join(
                    [
                        "UID,Name,Category,Source,Confidence",
                        (
                            "1311768467463790320,"
                            "Rook Character Body,"
                            "character,community,70"
                        ),
                        (
                            "0x000000009B2CAF32,"
                            "Rook Armor Pack,"
                            "model,manual,100"
                        ),
                        ",,,,",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = import_name_catalog(catalog, database)

            self.assertEqual(result.rows, 3)
            self.assertEqual(result.imported, 2)
            self.assertEqual(result.skipped, 1)

            matches = search_asset_names(database, "rook")

            self.assertEqual(len(matches), 2)

            self.assertEqual(matches[0].uid, 0x000000009B2CAF32)
            self.assertEqual(matches[0].name, "Rook Armor Pack")
            self.assertEqual(matches[0].confidence, 100)

            self.assertEqual(matches[1].uid, 0x123456789ABCDEF0)
            self.assertEqual(matches[1].category, "character")

    def test_imports_column_oriented_community_catalog(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            catalog = root / "community.csv"
            database = root / "assets.sqlite"

            catalog.write_text(
                "\n".join(
                    [
                        (
                            "Aruni Default body:,"
                            "Valkyrie Elite headgear:,,"
                            "Twitch Default body:"
                        ),
                        "281035272186,91997028720,,145010600770",
                        "309301454265,,,368009829660",
                    ]
                ) + "\n",
                encoding="utf-8",
            )

            result = import_column_name_catalog(
                catalog,
                database,
                default_source="r6-uid-sheet-2022",
                default_category="character-model",
                default_confidence=50,
            )

            self.assertEqual(result.rows, 5)
            self.assertEqual(result.imported, 5)
            self.assertEqual(result.skipped, 0)

            matches = search_asset_names(database, "Aruni Default body")

            self.assertEqual({match.uid for match in matches}, {281035272186, 309301454265})
            self.assertTrue(all(match.name == "Aruni Default body" for match in matches))
            self.assertTrue(all(match.category == "character-model" for match in matches))
            self.assertTrue(all(match.confidence == 50 for match in matches))


if __name__ == "__main__":
    unittest.main()