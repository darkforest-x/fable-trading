// V2.8 independent 5m event replay; parameters are frozen in research config.
// Signal/ATR arrays contain completed-bar values only. New stops are effective
// on the next 5m bar. The entry bar's earlier OHLC cannot affect its trade.
#include <algorithm>
#include <cmath>

extern "C" void replay(
    const double* bars, const double* atr, const double* hour, const double* events,
    int count, double target_r, double arm_r, double atr_mult, double tick,
    double* result) {
  for (int e = 0; e < count; ++e) {
    const double* ev = events + e * 5;
    int first = static_cast<int>(ev[0]), side = static_cast<int>(ev[1]);
    int last = static_cast<int>(ev[4]);
    double entry = ev[2], stop = ev[3], risk = side * (entry - stop);
    double target = entry + side * target_r * risk;
    target = (side == 1 ? std::ceil(target/tick - 1e-8)
                        : std::floor(target/tick + 1e-8)) * tick;
    double peak = 0, exit_price = entry;
    const double eps = tick * 1e-7;
    int armed = -1, exit_i = last, reason = 0, ambiguous = 0, steps = 0;
    for (int j = first + 1; j <= last; ++j) {
      ++steps;
      double o = bars[j*4], h = bars[j*4+1], l = bars[j*4+2], c = bars[j*4+3];
      bool stop_hit = side == 1 ? l <= stop+eps : h >= stop-eps;
      bool target_hit = target_r > 0 && (side == 1 ? h >= target-eps : l <= target+eps);
      bool stop_gap = side == 1 ? o < stop-eps : o > stop+eps;
      bool target_gap = target_r > 0 && side * (o - target) >= -eps;
      if (stop_hit && target_hit && !stop_gap && !target_gap) ++ambiguous;
      if (stop_gap || (stop_hit && !target_gap)) {
        exit_price = side == 1 ? std::min(o, stop) : std::max(o, stop);
        reason = armed >= 0 ? 2 : 1;
        exit_i = j;
        break;
      }
      if (target_hit) {
        exit_price = target;
        peak = std::max(peak, side * (target-entry)/risk);
        reason = 4;
        exit_i = j;
        break;
      }
      peak = std::max(peak, side * ((side == 1 ? h : l) - entry) / risk);
      if (target_r == 0 && std::isfinite(atr[j]) && atr[j] > 0) {
        // Inclusive R boundary in price units, matching native tick tolerance.
        if (armed < 0 && side * (c-entry) + eps >= arm_r * risk) armed = j;
        if (armed >= 0) {
          double raw = c - side * atr_mult * atr[j];
          double candidate = (side == 1 ? std::floor(raw/tick + 1e-8)
                                       : std::ceil(raw/tick - 1e-8)) * tick;
          stop = side == 1 ? std::max(stop, candidate) : std::min(stop, candidate);
        }
      }
      if (target_r == 0 && armed >= 0 && hour[j] == -side) {
        exit_price = c; reason = 3; exit_i = j; break;
      }
      exit_price = c;
    }
    double* out = result + e * 8;
    out[0] = exit_i; out[1] = exit_price; out[2] = reason; out[3] = peak;
    out[4] = armed; out[5] = stop; out[6] = ambiguous; out[7] = steps;
  }
}
