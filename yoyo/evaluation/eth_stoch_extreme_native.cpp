// Task62 frozen minute-path execution for a finite stop/exit parameter search.
// Inputs contain completed-bar admissions and pre-rounded price barriers.
// Future OHLC is used exclusively as outcomes. No third-party dependencies.
#include <cstdint>
#include <algorithm>
#include <limits>

extern "C" void replay_extreme(
    const double* o, const double* h, const double* l, const double* c,
    const int64_t* start, const int64_t* end, const int8_t* side,
    const double* entry, const double* initial_stop, const double* targets,
    const double* destinations, const double* triggers, int64_t n, int64_t pcount,
    int32_t* exits, double* prices, uint8_t* reasons, uint8_t* activated,
    uint8_t* ambiguous) {
    for (int64_t i=0; i<n; ++i) {
        const int s=side[i];
        for (int64_t p=0; p<pcount; ++p) {
            const int64_t z=i*pcount+p;
            if (s*(entry[i]-initial_stop[i])<=0) {
                exits[z]=-1; prices[z]=std::numeric_limits<double>::quiet_NaN();
                reasons[z]=255; activated[z]=0; ambiguous[z]=0;
                continue;
            }
            double stop=initial_stop[i];
            const double target=targets[z], dest=destinations[z], trigger=triggers[z];
            int32_t x=static_cast<int32_t>(end[i]-1);
            double price=c[x];
            uint8_t reason=0, protect=0, amb=0;
            for (int64_t j=start[i]; j<end[i]; ++j) {
                if (s*(o[j]-stop)<=0) { x=j; price=o[j]; reason=2; break; }
                if (s*(o[j]-target)>=0) { x=j; price=o[j]; reason=4; break; }
                const bool stopped=s==1 ? l[j]<=stop : h[j]>=stop;
                const bool targeted=s==1 ? h[j]>=target : l[j]<=target;
                if (stopped) { x=j; price=stop; reason=1; amb=targeted; break; }
                if (targeted) { x=j; price=target; reason=3; break; }
                if (trigger>=0 && !protect && s*(c[j]-entry[i])>=trigger) {
                    stop=s==1 ? std::max(stop,dest) : std::min(stop,dest);
                    protect=1;
                }
            }
            exits[z]=x; prices[z]=price; reasons[z]=reason;
            activated[z]=protect; ambiguous[z]=amb;
        }
    }
}
