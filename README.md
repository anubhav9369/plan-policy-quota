# plan-policy-quota

A binding pre-dispatch **quota envelope** for InspectablePlans — part of the
trustworthy agentic AI prototype for BRJP26100123 (Week 3: policy validation).

## Why

A policy pack says what a plan *may* do. A quota envelope says what a plan
may *spend* before it is dispatched: total cost, step count, irreversible
steps, external sends, and per-tool call caps. The envelope is checked before
any tool runs, so a plan that does not fit is never sent to execution — cost
is gated on execution, not on submission, and this is the gate that stops a
surprise bill. It is the usage-quota block (REQ-042 style) from the
Q-TeleScope work: quota envelope first, then health scores.

## Use

```bash
python3 policy_quota.py examples/plan.json --envelope examples/envelope.json
python3 policy_quota.py examples/plan.json --envelope examples/envelope.json --json
python3 policy_quota.py examples/plan.json --envelope examples/envelope.json --out report.md
```

Exit codes: `0` = WITHIN envelope, `1` = OVER envelope (a hard limit breached),
`2` = usage error (bad JSON, missing fields, envelope with no limits).

## Envelope

`examples/envelope.json` is the default envelope: $5 total cost, 10 steps,
2 irreversible steps, 1 external send, per-tool caps (search 5, save 3).

## Rules

- A limit is a warning at ≥ 80% of the cap and a breach above 100%.
- Unpriced steps are **unknown cost, not zero**: the total is a lower bound,
  so the cost limit can never be proven within the envelope — it reports
  UNKNOWN, never a pass on that limit.
- An envelope with no limits, or a limit that is not a positive number, is a
  config error: a silent envelope would approve everything.
- Irreversible = `"reversible": false`. External send = the step's leading
  verb is send / publish / share / post / email / message. A tool is the
  step's `"tool"` field, else the leading verb of its action.

## Examples

- `examples/plan.json` — within the envelope (verdict WITHIN).
- `examples/plan-over.json` — over cost and over external sends (verdict OVER).
- `examples/quota-report.md` / `examples/quota-report-over.md` — the
  generated reports for the two plans.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

10 stdlib tests, no dependencies.
