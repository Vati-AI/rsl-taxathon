"""Frappe-free tests for the portal's RSL settings (TAX-125, ADR-043)."""

from __future__ import annotations

import unittest

from vati_tax.portal import rsl


class SimulatorTests(unittest.TestCase):
    def test_the_simulator_is_off_unless_the_site_turns_it_on(self) -> None:
        self.assertFalse(rsl.simulator_enabled({}))
        for value in (False, None, "true", "1", 0, "yes"):
            with self.subTest(value=value):
                self.assertFalse(rsl.simulator_enabled({rsl.SIMULATOR_KEY: value}))
        self.assertTrue(rsl.simulator_enabled({rsl.SIMULATOR_KEY: True}))
        self.assertTrue(rsl.simulator_enabled({rsl.SIMULATOR_KEY: 1}))


class LinkTests(unittest.TestCase):
    def test_only_a_configured_https_link_to_rsl_is_offered(self) -> None:
        self.assertIsNone(rsl.online_refunds_url({}))
        self.assertIsNone(rsl.online_refunds_url({rsl.LINKS_KEY: "https://x.example.test"}))
        for url in ("http://x.example.test", "https://u:p@x.example.test", ""):
            with self.subTest(url=url):
                conf = {rsl.LINKS_KEY: {"online_refunds_dashboard": url}}
                self.assertIsNone(rsl.online_refunds_url(conf))
        conf = {rsl.LINKS_KEY: {"online_refunds_dashboard": "https://refunds.example.test/"}}
        self.assertEqual(rsl.online_refunds_url(conf), "https://refunds.example.test/")


if __name__ == "__main__":
    unittest.main()
