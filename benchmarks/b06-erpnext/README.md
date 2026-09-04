# Benchmark 06 — ERPNext

The second codebase nobody here has worked on, and the first one whose
knowledge is **business rules** rather than device quirks: accounting,
stock valuation, manufacturing, bank statement formats.

`frappe/erpnext` — 17 200 definitions across 13 176 distinct names.
Published whole, like [b05](../b05-home-assistant/): the repository is
public and the findings come from its own merged pull requests.

## Result

```
FALSE AUTHORITATIVE ASSERTIONS   0    (target 0)
SUPPORTED KNOWLEDGE RECOVERED    16/16   (100%)
```

| Outcome | |
|---|---:|
| correct match | 16 |
| correct ambiguity | 2 |
| correct no-assertion | 2 |
| wrong match / unsupported assertion | **0** |

The 20 findings are the kind of thing an ERP carries and code cannot
explain: a quality inspection rejected because the site's number format uses
a comma decimal separator; a landed cost voucher losing its quantity because
the next stock reconciliation was found by a legacy batch number; purchase
order invoice amounts reused across receipts when FIFO allocation spans
several; a division by zero in a batchwise valuation fallback that only
PostgreSQL reaches; "skip material transfer" not meaning "in process".

`execute` is defined **631 times** here — once per report — and a finding
naming it without saying which report was refused as ambiguous, as it should
be. `get_columns`, 143 definitions, likewise.

## The one wrong match, and why it was the gold's fault

The first run reported one **wrong match**, the grave category. It was not.

A finding cited `get_exploded_items` in the BOM Explorer report, because the
pull request's diff hunk header named that function. At HEAD the name is
defined in three *other* files and not in the report at all — a hunk header
names the enclosing or called function, which need not live in the changed
file. So the symbol was genuinely ambiguous and the cited path could not
resolve it; TraceLink refused the symbol and anchored the file the author
had named, which is exactly what the author wrote.

The expectation was corrected and **marked as corrected**, with the reason,
in [`gold.json`](gold.json). The extraction method was wrong, not the tool —
and the lesson is worth more than the number: *a diff tells you what
changed, not what a name means today.* Anchors have to be verified against
the file that is supposed to define them, not merely against the repository.
