import unittest

from polymarket_weekly_consensus import (
    Position,
    Trader,
    extract_open_positions,
    extract_top_traders,
    find_consensus_positions,
)


class ExtractTopTradersTests(unittest.TestCase):
    def test_extracts_and_sorts_by_rank(self):
        payload = {
            "data": [
                {"rank": 2, "proxyWallet": "0xbbb", "name": "B"},
                {"rank": 1, "proxyWallet": "0xaaa", "name": "A"},
                {"rank": 3, "proxyWallet": "0xccc", "name": "C"},
            ]
        }

        traders = extract_top_traders(payload, limit=2)

        self.assertEqual([t.address for t in traders], ["0xaaa", "0xbbb"])


class ExtractOpenPositionsTests(unittest.TestCase):
    def test_filters_closed_and_missing_price(self):
        payload = {
            "positions": [
                {
                    "tokenId": "1",
                    "question": "Will X win?",
                    "outcome": "Yes",
                    "avgPrice": "0.52",
                    "size": "100",
                    "isOpen": True,
                },
                {
                    "tokenId": "2",
                    "question": "Will Y win?",
                    "outcome": "No",
                    "avgPrice": "0.49",
                    "size": "100",
                    "status": "closed",
                },
                {
                    "tokenId": "3",
                    "question": "Will Z win?",
                    "outcome": "No",
                    "size": "100",
                    "isOpen": True,
                },
            ]
        }

        positions = extract_open_positions(payload)

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].key, "1::Yes")


class ConsensusPositionTests(unittest.TestCase):
    def test_requires_three_traders_within_ten_cents(self):
        traders = [
            Trader(address="0x1", name="T1"),
            Trader(address="0x2", name="T2"),
            Trader(address="0x3", name="T3"),
            Trader(address="0x4", name="T4"),
        ]
        positions_by_trader = {
            "0x1": [Position(key="market-1", market="Will Team A win?", outcome="Yes", average_price_paid=0.50)],
            "0x2": [Position(key="market-1", market="Will Team A win?", outcome="Yes", average_price_paid=0.55)],
            "0x3": [Position(key="market-1", market="Will Team A win?", outcome="Yes", average_price_paid=0.60)],
            "0x4": [Position(key="market-1", market="Will Team A win?", outcome="Yes", average_price_paid=0.90)],
        }

        result = find_consensus_positions(
            traders=traders,
            positions_by_trader=positions_by_trader,
            min_traders=3,
            max_deviation=0.10,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["holders_count"], 3)
        self.assertEqual({h["address"] for h in result[0]["holders"]}, {"0x1", "0x2", "0x3"})


if __name__ == "__main__":
    unittest.main()
