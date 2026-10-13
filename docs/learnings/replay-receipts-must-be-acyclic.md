# Keep replay receipts acyclic when nesting per-symbol audits

- **Problem**: A whole-universe replay can finish downloading every symbol and still fail before producing its frozen receipt. This happened when a per-symbol audit pointed to its snapshot while the snapshot embedded that same audit.
- **Dead end**: Retrying the network fetch without checking the object graph repeats expensive reads; hand-building a partial receipt afterward can omit the source hashes and retry history needed to validate the snapshot.
- **Effective path**: Store a narrow audit projection inside each snapshot (`monthly_archives` and `october_attempts`), then let the collector result point to the snapshot. The nested value must not point back to the collector result. Preserve a failed receipt attempt separately and rerun after the serialization fix.
- **General rule**: Treat a durable receipt as a tree of values, not a graph of mutually linked runtime objects. Before a costly acquisition, ensure the final receipt can be JSON-serialized without recursive references.
- **Related**: `yoyo/evaluation/grade_a_two_month_replay.py`, especially the hybrid fetcher's per-symbol `source_audit` and `fetch_receipt.json` writer.
