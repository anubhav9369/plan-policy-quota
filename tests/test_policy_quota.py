"""Tests for policy_quota.py — quota envelope checks for InspectablePlans."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = os.path.dirname(HERE)
sys.path.insert(0, DAY)

from policy_quota import check, measure, validate_envelope  # noqa: E402

ENVELOPE = {
    "id": "default", "version": "1.0.0",
    "limits": {
        "max_total_cost": 5.0, "max_steps": 10, "max_irreversible": 2,
        "max_external_sends": 1,
        "max_calls_per_tool": {"search": 5, "save": 3},
    },
}

GOOD = {
    "id": "research-summary", "version": 3,
    "steps": [
        {"id": "s1", "action": "search papers on arXiv", "tool": "search",
         "estimate_usd": 0.4, "reversible": True},
        {"id": "s2", "action": "summarise the top 5 papers",
         "estimate_usd": 1.1, "reversible": True},
        {"id": "s3", "action": "save summary to the shared drive",
         "tool": "save", "estimate_usd": 0.2, "reversible": True},
    ],
}


def finding(result, limit_id):
    return next(f for f in result["findings"] if f["limit"] == limit_id)


class TestMeasure(unittest.TestCase):
    def test_counts_steps_irreversible_sends_and_tools(self):
        plan = {"id": "p", "steps": [
            {"id": "a", "action": "search papers", "estimate_usd": 1.0,
             "reversible": True},
            {"id": "b", "action": "send summary to the team",
             "estimate_usd": 0.5, "reversible": False},
        ]}
        usage = measure(plan)
        self.assertEqual(usage["steps"], 2)
        self.assertEqual(usage["irreversible"], 1)
        self.assertEqual(usage["external_sends"], 1)
        self.assertEqual(usage["calls_per_tool"], {"search": 1, "send": 1})
        self.assertAlmostEqual(usage["cost_usd"], 1.5)

    def test_unpriced_steps_are_listed_not_zero(self):
        plan = {"id": "p", "steps": [
            {"id": "a", "action": "search papers", "reversible": True},
        ]}
        usage = measure(plan)
        self.assertEqual(usage["unpriced_steps"], ["a"])
        self.assertEqual(usage["cost_usd"], 0.0)

    def test_plan_without_steps_is_an_error(self):
        with self.assertRaises(ValueError):
            measure({"id": "p"})


class TestCheck(unittest.TestCase):
    def test_good_plan_is_within(self):
        result = check(GOOD, ENVELOPE)
        self.assertEqual(result["verdict"], "WITHIN")
        self.assertEqual(finding(result, "max_total_cost")["status"], "ok")
        self.assertAlmostEqual(result["remaining"]["max_total_cost"], 3.3)

    def test_over_cost_and_over_sends(self):
        plan = {"id": "blast", "version": 1, "steps": [
            {"id": "s1", "action": "search papers", "tool": "search",
             "estimate_usd": 2.4, "reversible": True},
            {"id": "s2", "action": "search more", "tool": "search",
             "estimate_usd": 2.0, "reversible": True},
            {"id": "s3", "action": "summarise papers", "estimate_usd": 1.8,
             "reversible": True},
            {"id": "s4", "action": "send summary to the team",
             "estimate_usd": 0.3, "reversible": False},
            {"id": "s5", "action": "publish summary to the blog",
             "estimate_usd": 0.3, "reversible": False},
        ]}
        result = check(plan, ENVELOPE)
        self.assertEqual(result["verdict"], "OVER")
        self.assertEqual(finding(result, "max_total_cost")["status"], "over")
        self.assertEqual(finding(result, "max_external_sends")["status"],
                         "over")
        self.assertEqual(finding(result, "max_irreversible")["status"], "near")

    def test_near_limit_warns_at_80_percent(self):
        plan = {"id": "p", "steps": [
            {"id": "s1", "action": "search papers", "tool": "search",
             "estimate_usd": 4.0, "reversible": True},
        ]}
        result = check(plan, ENVELOPE)
        self.assertEqual(result["verdict"], "WITHIN")
        self.assertEqual(finding(result, "max_total_cost")["status"], "near")

    def test_unpriced_step_makes_cost_unknown(self):
        plan = {"id": "p", "steps": [
            {"id": "s1", "action": "search papers", "tool": "search",
             "estimate_usd": 1.0, "reversible": True},
            {"id": "s2", "action": "summarise papers", "reversible": True},
        ]}
        result = check(plan, ENVELOPE)
        self.assertEqual(finding(result, "max_total_cost")["status"],
                         "unknown")
        self.assertEqual(result["verdict"], "WITHIN")

    def test_per_tool_cap_breach(self):
        plan = {"id": "p", "steps": [
            {"id": f"s{i}", "action": "search papers", "tool": "search",
             "estimate_usd": 0.1, "reversible": True} for i in range(6)
        ]}
        result = check(plan, ENVELOPE)
        self.assertEqual(result["verdict"], "OVER")
        self.assertEqual(
            finding(result, "max_calls_per_tool:search")["status"], "over")

    def test_envelope_with_no_limits_is_an_error(self):
        with self.assertRaises(ValueError):
            validate_envelope({"id": "empty", "limits": {}})


class TestCLI(unittest.TestCase):
    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "good.json")
            over = os.path.join(tmp, "over.json")
            env = os.path.join(tmp, "env.json")
            for path, data in ((good, GOOD), (env, ENVELOPE)):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(data, handle)
            bad = {"id": "blast", "steps": [
                {"id": "s1", "action": "send summary", "estimate_usd": 9.0,
                 "reversible": False}]}
            with open(over, "w", encoding="utf-8") as handle:
                json.dump(bad, handle)
            tool = os.path.join(DAY, "policy_quota.py")
            ok = subprocess.run([sys.executable, tool, good,
                                 "--envelope", env],
                                capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0)
            self.assertIn("WITHIN", ok.stdout)
            too_much = subprocess.run([sys.executable, tool, over,
                                       "--envelope", env],
                                      capture_output=True, text=True)
            self.assertEqual(too_much.returncode, 1)
            self.assertIn("OVER", too_much.stdout)


if __name__ == "__main__":
    unittest.main()
