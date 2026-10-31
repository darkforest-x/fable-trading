#!/bin/sh
set -eu
cd /Users/zhangzc/fable-trading
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.monday_order_only run --config experiments/active/exp-monday-order-only-originalscore-20261031-v1/config.json --workers 4
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.monday_order_only summary --config experiments/active/exp-monday-order-only-originalscore-20261031-v1/config.json
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.monday_order_only validate --config experiments/active/exp-monday-order-only-originalscore-20261031-v1/config.json
/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.monday_order_only diagnostics --config experiments/active/exp-monday-order-only-originalscore-20261031-v1/config.json
