// Independent V2.8 exit replay with a single 3R cost-breakeven intervention.
// Close-derived protection applies only on the following 5m bar.
#include <algorithm>
#include <cmath>

static void replay_core(
    const double* bars, const double* atr, const double* hour,
    const double* events, int count, int mode, double cost, double tick,
    double arm_r, double atr_multiple, double be_r, bool reverse_exit,
    double target_r, bool stop_first,
    double* output, int time_delay = 0, const double* be_clock = nullptr) {
  for (int e = 0; e < count; ++e) {
    const double* ev = events + e * 5;
    int first = static_cast<int>(ev[0]), side = static_cast<int>(ev[1]);
    int last = static_cast<int>(ev[4]);
    double entry = ev[2], stop = ev[3], risk = side * (entry-stop);
    double raw_be = (entry + side * cost * entry) / tick;
    double be_price = (side == 1 ? std::ceil(raw_be-1e-8)
                                 : std::floor(raw_be+1e-8)) * tick;
    double raw_tp = (entry + side*target_r*risk)/tick;
    double target = (side == 1 ? std::ceil(raw_tp-1e-8)
                               : std::floor(raw_tp+1e-8))*tick;
    const double eps = tick*1e-7;
    double peak = 0, exit_price = entry;
    int trail_i = -1, be_trigger = -1, be_i = -1, stop_kind = 0;
    int reason = 0, exit_i = last, steps = 0, deferred = 0, ambiguous = 0;
    for (int j = first+1; j <= last; ++j) {
      ++steps;
      double o=bars[j*4], h=bars[j*4+1], l=bars[j*4+2], c=bars[j*4+3];
      double adverse = side == 1 ? l : h;
      double favorable = side == 1 ? h : l;
      bool sl = side*(adverse-stop) <= eps;
      bool tp = target_r > 0 && target > 0 && side*(favorable-target) >= -eps;
      bool sl_open = side*(o-stop) <= eps;
      bool tp_open = tp && side*(o-target) >= -eps;
      ambiguous = sl && tp;
      // Native O-H-L-C visits the long target before the long stop; the
      // short stop before the short target. Open gaps take precedence.
      bool take_tp = tp && !sl_open && (tp_open || !sl || (side == 1 && !stop_first));
      if (take_tp) {
        // Installed Nautilus 1.231.0 resting limits fill at their limit price,
        // including favorable opening gaps (verified with native fixtures).
        exit_price = target;
        reason = 4; exit_i = j; break;
      }
      if (sl) {
        exit_price = side == 1 ? std::min(o, stop) : std::max(o, stop);
        reason = stop_kind == 1 ? 5 : (trail_i >= 0 ? 2 : 1);
        exit_i = j;
        break;
      }
      peak = std::max(peak, side*(favorable-entry)/risk);
      if (time_delay > 0 && be_clock[j] > 0) {
        if (be_trigger < 0 && j-first >= time_delay) be_trigger = j;
        if (be_trigger >= 0 && be_i < 0) {
          if (side*(c-be_price) >= tick-eps) {
            be_i = j;
            if (side*(be_price-stop) > eps) { stop=be_price; stop_kind=1; }
          } else { ++deferred; }
        }
      }
      if (std::isfinite(atr[j]) && atr[j] > 0) {
        bool beyond5 = mode == 0 ? side*(c-entry)/risk >= arm_r
                                : side*(c-entry) >= arm_r*risk-eps;
        if (target_r <= 0 && trail_i < 0 && beyond5) trail_i = j;
        if (be_r > 0) {
          if (be_trigger < 0 && side*(c-entry) >= be_r*risk-eps) be_trigger = j;
          if (be_trigger >= 0 && be_i < 0) {
            if (side*(c-be_price) >= tick-eps) {
              be_i = j;
              if (side*(be_price-stop) > eps) { stop = be_price; stop_kind = 1; }
            } else {
              ++deferred;
            }
          }
        }
        if (trail_i >= 0) {
          double raw = (c-side*atr_multiple*atr[j])/tick;
          double candidate = (side == 1 ? std::floor(raw+1e-8)
                                        : std::ceil(raw-1e-8))*tick;
          if (side*(candidate-stop) > 0) { stop = candidate; stop_kind = 2; }
        }
      }
      if (reverse_exit && trail_i >= 0 && hour[j] == -side) {
        exit_price = c; reason = 3; exit_i = j; break;
      }
      exit_price = c;
    }
    double* out = output + e*13;
    out[0]=exit_i; out[1]=exit_price; out[2]=reason; out[3]=peak;
    out[4]=trail_i; out[5]=stop; out[6]=ambiguous; out[7]=steps;
    out[8]=be_trigger; out[9]=be_i; out[10]=stop_kind;
    out[11]=be_price; out[12]=deferred;
  }
}

// Preserve the established entry point and historical baseline/BE3 semantics.
extern "C" void replay_be(
    const double* bars, const double* atr, const double* hour,
    const double* events, int count, int mode, double cost, double tick,
    double arm_r, double atr_multiple, double* output) {
  replay_core(bars,atr,hour,events,count,mode,cost,tick,arm_r,atr_multiple,
              mode == 2 ? 3. : -1.,true,0.,false,output);
}

extern "C" void replay_search(
    const double* bars, const double* atr, const double* hour,
    const double* events, int count, double cost, double tick,
    double arm_r, double atr_multiple, double be_r, int reverse_exit,
    double target_r, int stop_first, double* output) {
  replay_core(bars,atr,hour,events,count,1,cost,tick,arm_r,atr_multiple,
              be_r,reverse_exit,target_r,stop_first,output);
}

extern "C" void replay_timed(
    const double* bars, const double* atr, const double* hour,
    const double* be_clock, const double* events, int count,
    double cost, double tick, int delay, double* output) {
  replay_core(bars,atr,hour,events,count,1,cost,tick,5.,4.,
              -1.,true,0.,false,output,delay,be_clock);
}
