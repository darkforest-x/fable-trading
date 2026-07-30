The worker contract, compact status, and next-iteration packet are embedded
below. Do not call `read_file` for these embedded runtime files. Follow the
contract exactly. Reconcile uncommitted completed work first, then execute up to
three evidence-driven atomic iterations. After each iteration, test the real
surface, compare with baseline, commit/push when tracked files changed, and
update compact status/evidence/next-task. Consult the full plan only if the
embedded packet is stale or incomplete. Be concise.
