"""Synthetic algebra checks against direct population-BB recomputation.

No files containing market prices are read. Past inputs are fixed random
synthetic closes. Independent oracle uses statistics.pstdev on the full window.
"""
import json
import math
import random
import statistics
from pathlib import Path

rng = random.Random(20260916)
cases = 0
max_error = 0.0
for n, mult in [(20, 2.0), (200, 2.0), (50, 1.5), (10, 2.5)]:
    for trial in range(100):
        past = [rng.uniform(90, 110) for _ in range(n - 1)]
        mean, sd = statistics.mean(past), statistics.pstdev(past)
        for side in [1, -1]:
            trigger = mean + side * mult * sd * math.sqrt(n / (n - 1 - mult * mult))
            for delta in [-0.01, 0, 0.01]:
                price = trigger + delta
                full = past + [price]
                band = statistics.mean(full) + side * mult * statistics.pstdev(full)
                if delta == 0:
                    max_error = max(max_error, abs(price - band))
                    assert abs(price - band) < 1e-10
                else:
                    assert (side * (price - band) > 0) == (side * delta > 0)
                cases += 1
            tick = 0.01
            rounded = (math.ceil(trigger / tick) if side == 1 else math.floor(trigger / tick)) * tick
            assert -1e-12 <= side * (rounded - trigger) < tick + 1e-12
            prev_tick = rounded - side * tick
            prev_band = statistics.mean(past + [prev_tick]) + side * mult * statistics.pstdev(past + [prev_tick])
            assert side * (prev_tick - prev_band) < 1e-10
# Flat preceding closes reduce both developing-band boundaries to the mean.
assert statistics.pstdev([100] * 199) == 0
result = {'synthetic_comparisons': cases, 'max_boundary_error': max_error, 'tick_rounding_checks': 800, 'market_data_read': False, 'result': 'PASS'}
Path(__file__).with_name('algebra_validation.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
