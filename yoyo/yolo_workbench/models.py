"""Reviewed inference profiles, not an ACTIVE pointer or a training permission.

The close baseline is retained by the paired Grade-A calibration evidence:
analysis/p1_15m_grade_a_owner_calibration_20260907.md. A new recommendation
requires a matching renderer/input contract and comparative evidence here;
filesystem modification times and unrelated validation mAP never select it.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = 'grade-a-close-1280'
MODELS = {
    DEFAULT_MODEL: dict(
        id=DEFAULT_MODEL, name='Grade-A · Close · 1280', source='close', recommended=True,
        path='analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt',
        sha256='862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838',
        experiment='exp-15m-ma-launch-owner-grade-a8000-neg24000-train1280-full40-v1',
        earliest_recall=126/155, negative_fires=41, best_epoch=4,
        verdict='默认研究基线；HL2 配对比较未证明整体改善。尚无跨全部周期的最佳模型裁决。'),
    'grade-a-hl2-1280': dict(
        id='grade-a-hl2-1280', name='Grade-A · HL2 · 对照版本', source='hl2', recommended=False,
        path='analysis/output/ma_launch_owner_grade_a8000_neg24000_hl2_v1/ma_launch_owner_grade_a8000_neg24000_hl2_v1_y11s_ft1280_full40/weights/best.pt',
        sha256='0533d48f209bfeb73b10f147a38f245de1765599ad222770af394c4f0f68ab52',
        experiment='exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1',
        earliest_recall=135/155, negative_fires=51, best_epoch=6,
        verdict='最早事件检出增加，但误触增加；联合比较未通过，不替换默认基线。'),
}
TIMEFRAMES = {'5m':300000, '15m':900000, '30m':1800000, '1H':3600000,
              '4H':14400000, '1Dutc':86400000}
PARAMETERS = dict(imgsz=1280, conf=.25, iou=.70, verbose=False, rect=True,
                  half=False, agnostic_nms=False, max_det=300, save=False)


def catalog():
    return dict(default_model=DEFAULT_MODEL, timeframes=list(TIMEFRAMES),
                items=[dict(m, available=(ROOT/m['path']).is_file(), production_eligible=False,
                            trained_timeframe='15m', input_windows=[18,19]) for m in MODELS.values()],
                training=dict(base='YOLO11s', epochs=40, imgsz=1280, optimizer='AdamW',
                              positive_images=8000, negative_images=24000, positive_events=1043,
                              train_images=27200, val_images=4800, train_positive_events=888,
                              val_positive_events=155, ma_periods=[20,60,120],
                              augmentation='翻转 / mosaic / mixup / HSV 关闭',
                              note='Grade-A 是这批样本的筛选等级，不是模型已经达到实盘 A 级的认证。'))


def freeze_spec(spec):
    """Pin the selected profile at submission, including its input contract."""
    import hashlib
    return dict(spec, model_sha256=MODELS[spec['model_id']]['sha256'],
                renderer_sha256=hashlib.sha256((ROOT/'yoyo/layers/l1_detection/render.py').read_bytes()).hexdigest(),
                parameters=PARAMETERS, adapter_version='yolo-workbench-v1')
