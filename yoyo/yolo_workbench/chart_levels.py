"""A stored detection owns its Monday week, never the browser's current clock."""
from yoyo.monitor.monday_chart_levels import MondayChartLevels


class ChartLevels(MondayChartLevels):
    def read(self, snapshot, detection):
        box = next((b for b in snapshot.get('episodes', []) + snapshot.get('raw_detections', [])
                    if b['id'] == detection), None)
        if box is None:
            raise KeyError(detection)
        at = box['detected_at_ms']
        return dict(super().read(snapshot['symbol'], at), detection_id=detection, as_of_ms=at)
