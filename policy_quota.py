#!/usr/bin/env python3
"""plan-policy-quota: a binding pre-dispatch quota envelope for InspectablePlans.

A quota envelope is a set of hard limits a plan must fit inside *before* it is
dispatched: total cost, step count, irreversible steps, external sends, and
per-tool call caps. It is the usage-quota block (REQ-042 style) that sits in
front of execution — a plan that does not fit the envelope is never sent to a
tool, so cost can never be billed by surprise.

Usage:
    python3 policy_quota.py plan.json --envelope envelope.json
    python3 policy_quota.py plan.json --envelope envelope.json --json
    python3 policy_quota.py plan.json --envelope envelope.json --out report.md

Exit codes: 0 = WITHIN envelope, 1 = OVER envelope (a hard limit breached),
2 = usage error (bad JSON, missing fields, envelope with no limits).

Rules:
- Unpriced steps are UNKNOWN cost, not zero: the total is reported as a lower
  bound and the cost limit cannot be proven within the envelope, so it is a
  warning, never a pass on that limit.
- A limit is a warning at >= 80% of the cap and a breach above 100%.
- Per-tool caps count steps by tool: an explicit "tool" field, else the
  leading verb of the action ("send email" -> "send").
- An irreversible step is one with "reversible": false.
- An external send is a step whose leading verb is send / publish / share /
  post / email / message.
"""

import argparse
import json
import sys

NEAR_LIMIT = 0.8
SEND_VERBS = {"send", "publish", "share", "post", "email", "message"}


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def leading_word(action):
    return (action or "").strip().split(" ", 1)[0].lower()


def step_tool(step):
    return step.get("tool") or leading_word(step.get("action", ""))


def is_irreversible(step):
    return step.get("reversible") is False


def is_external_send(step):
    return leading_word(step.get("action", "")) in SEND_VERBS


def measure(plan):
    """Count what a plan would consume if it ran."""
    steps = plan.get("steps")
    if not isinstance(steps, list):
        raise ValueError("plan has no 'steps' list")
    usage = {
        "steps": len(steps),
        "irreversible": 0,
        "external_sends": 0,
        "cost_usd": 0.0,
        "unpriced_steps": [],
        "calls_per_tool": {},
    }
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("plan step is not an object")
        if is_irreversible(step):
            usage["irreversible"] += 1
        if is_external_send(step):
            usage["external_sends"] += 1
        tool = step_tool(step)
        usage["calls_per_tool"][tool] = usage["calls_per_tool"].get(tool, 0) + 1
        estimate = step.get("estimate_usd")
        if estimate is None:
            usage["unpriced_steps"].append(step.get("id", "?"))
        else:
            usage["cost_usd"] += float(estimate)
    return usage


def validate_envelope(envelope):
    """An envelope with no limits, or a limit that is not a positive number,
    is a config error — a silent envelope would approve everything."""
    limits = envelope.get("limits")
    if not isinstance(limits, dict) or not limits:
        raise ValueError("envelope has no 'limits' object")
    for key, value in limits.items():
        if key == "max_calls_per_tool":
            if not isinstance(value, dict) or not value:
                raise ValueError("max_calls_per_tool must be a non-empty object")
            for tool, cap in value.items():
                if not isinstance(cap, (int, float)) or cap <= 0:
                    raise ValueError(f"per-tool cap for {tool!r} is not positive")
        elif not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"limit {key!r} is not a positive number")
    return limits


def check(plan, envelope):
    """Check a plan against an envelope.

    Returns a dict with the verdict ("WITHIN" / "OVER"), one finding per
    limit (status ok / near / over), the usage, and what quota is left.
    """
    limits = validate_envelope(envelope)
    usage = measure(plan)
    findings = []
    remaining = {}

    def add_finding(limit_id, used, cap, label):
        if used > cap:
            status = "over"
        elif used >= NEAR_LIMIT * cap:
            status = "near"
        else:
            status = "ok"
        findings.append({
            "limit": limit_id, "label": label, "used": used, "cap": cap,
            "status": status,
        })
        remaining[limit_id] = round(cap - used, 2)

    if "max_total_cost" in limits:
        cap = limits["max_total_cost"]
        if usage["unpriced_steps"]:
            # Cost is a lower bound; the envelope cannot be proven.
            findings.append({
                "limit": "max_total_cost",
                "label": f"total cost ${usage['cost_usd']:.2f} of ${cap:.2f} "
                         f"(lower bound — {len(usage['unpriced_steps'])} "
                         "unpriced step(s): "
                         f"{', '.join(usage['unpriced_steps'])})",
                "used": round(usage["cost_usd"], 2), "cap": cap,
                "status": "near" if usage["cost_usd"] > cap else "unknown",
            })
            remaining["max_total_cost"] = round(cap - usage["cost_usd"], 2)
        else:
            add_finding("max_total_cost", round(usage["cost_usd"], 2), cap,
                        f"total cost ${usage['cost_usd']:.2f} of ${cap:.2f}")
    if "max_steps" in limits:
        add_finding("max_steps", usage["steps"], limits["max_steps"],
                    f"{usage['steps']} steps of {limits['max_steps']}")
    if "max_irreversible" in limits:
        add_finding("max_irreversible", usage["irreversible"],
                    limits["max_irreversible"],
                    f"{usage['irreversible']} irreversible step(s) "
                    f"of {limits['max_irreversible']}")
    if "max_external_sends" in limits:
        add_finding("max_external_sends", usage["external_sends"],
                    limits["max_external_sends"],
                    f"{usage['external_sends']} external send(s) "
                    f"of {limits['max_external_sends']}")
    if "max_calls_per_tool" in limits:
        for tool, cap in sorted(limits["max_calls_per_tool"].items()):
            used = usage["calls_per_tool"].get(tool, 0)
            add_finding(f"max_calls_per_tool:{tool}", used, cap,
                        f"{used} call(s) to tool {tool!r} of {cap}")

    verdict = "OVER" if any(f["status"] == "over" for f in findings) else "WITHIN"
    return {
        "plan": plan.get("id", "?"),
        "version": plan.get("version", "?"),
        "envelope": envelope.get("id", "?"),
        "envelope_version": envelope.get("version", "?"),
        "verdict": verdict,
        "usage": usage,
        "findings": findings,
        "remaining": remaining,
    }


def render_markdown(result):
    lines = [
        f"# Quota envelope check — {result['plan']} v{result['version']}",
        "",
        f"Envelope: {result['envelope']} v{result['envelope_version']}",
        f"Verdict: **{result['verdict']}**",
        "",
        "## Usage",
        "",
        f"- Steps: {result['usage']['steps']}",
        f"- Irreversible steps: {result['usage']['irreversible']}",
        f"- External sends: {result['usage']['external_sends']}",
        f"- Total cost: ${result['usage']['cost_usd']:.2f}"
        + (" (lower bound — unpriced steps: "
           + ", ".join(result['usage']['unpriced_steps']) + ")"
           if result['usage']['unpriced_steps'] else ""),
        "- Calls per tool: "
        + (", ".join(f"{t} x{n}" for t, n in
                     sorted(result['usage']['calls_per_tool'].items()))
           or "none"),
        "",
        "## Limits",
        "",
    ]
    for finding in result["findings"]:
        mark = {"ok": "OK", "near": "NEAR", "over": "OVER",
                "unknown": "UNKNOWN"}[finding["status"]]
        lines.append(f"- [{mark}] {finding['label']}")
    lines += [
        "",
        "## Remaining quota",
        "",
    ]
    for limit_id, left in sorted(result["remaining"].items()):
        lines.append(f"- {limit_id}: {left}")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check an InspectablePlan against a quota envelope.")
    parser.add_argument("plan", help="plan JSON file")
    parser.add_argument("--envelope", required=True,
                        help="quota envelope JSON file")
    parser.add_argument("--json", action="store_true",
                        help="print the result as JSON")
    parser.add_argument("--out", help="write a Markdown report to this file")
    args = parser.parse_args(argv)

    try:
        plan = load_json(args.plan)
        envelope = load_json(args.envelope)
        result = check(plan, envelope)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_markdown(result))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(result))
    return 0 if result["verdict"] == "WITHIN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
