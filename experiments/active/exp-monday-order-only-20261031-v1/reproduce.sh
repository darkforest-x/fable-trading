#!/bin/sh
set -eu
cd /Users/zhangzc/fable-trading
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.monday_order_only run --workers 4
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.monday_order_only summary
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.monday_order_only validate
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.monday_order_only diagnostics
