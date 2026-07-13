"""
Assemble a compact ALL-CLASSES bundle for the artifact's per-class panel.

Merges, per class (intact/moderate/severe):
  - accuracy curves + ideal thresholds  (results/threshold_accuracy_{intact_3band,moderate,severe}.json)
  - area curves                          (results/threshold_area_{intact_check,moderate,severe}.json)

Emits results/allclasses_artifact.json -> injected into <script id="ALLCLASSES"> in
../threshold_sensitivity.html.
"""
import os, json

HERE = os.path.dirname(os.path.abspath(__file__))
def res_path(name): return os.path.join(HERE, 'results', name)

ACC_FILE = {'intact': 'threshold_accuracy_intact_3band.json',
            'moderate': 'threshold_accuracy_moderate.json',
            'severe': 'threshold_accuracy_severe.json'}
AREA_FILE = {'intact': 'threshold_area_intact_check.json',
             'moderate': 'threshold_area_moderate.json',
             'severe': 'threshold_area_severe.json'}
PROB_COL = {'intact': 'p_intact', 'moderate': 'p_moderate', 'severe': 'p_severe'}


def main():
    out = {'classes': {}, 'order': ['intact', 'moderate', 'severe']}
    for name in out['order']:
        acc = json.load(open(res_path(ACC_FILE[name])))
        area = json.load(open(res_path(AREA_FILE[name])))
        yj = acc['ideal']['by_youden']
        out['classes'][name] = {
            'prob_band': PROB_COL[name],
            'positive_class': acc['positive_class'],
            'n_positive': acc['n_positive'],
            'n_negative': acc['n_negative'],
            'prevalence': round(acc['n_positive'] / acc['n_points'], 4),
            'roc_auc': acc['roc_auc'],
            'ideal': acc['ideal'],
            # full 0.01-resolution curves for the small-multiples chart
            'thresholds': acc['thresholds'],
            'youden_J': acc['youden_J'],
            'f1': acc['f1'],
            'recall': acc['recall'],
            'specificity': acc['specificity'],
            'overall_accuracy': acc['overall_accuracy'],
            'area_km2': area['area_km2'],
            'area_at_ideal_km2': area['area_at_ideal_km2'],
            'total_valid_area_km2': area['total_valid_area_km2'],
            'area_scale_m': area['area_scale_m'],
        }
    out['n_points'] = acc['n_points']
    out['generated_on'] = __import__('time').strftime('%Y-%m-%d')
    with open(res_path('allclasses_artifact.json'), 'w') as fh:
        json.dump(out, fh)
    # human-readable recap
    print('class     n_pos  AUC    tau*(J)  J      F1     sens   spec   area@tau*(km2)')
    for name in out['order']:
        c = out['classes'][name]; yj = c['ideal']['by_youden']
        print(f"{name:9s} {c['n_positive']:5d}  {c['roc_auc']:.3f}  "
              f"{yj['threshold']:.2f}     {yj['youden_J']:.3f}  {yj['f1']:.3f}  "
              f"{yj['recall']:.3f}  {yj['specificity']:.3f}  {c['area_at_ideal_km2']:.0f}")
    print('\nWrote results/allclasses_artifact.json')


if __name__ == '__main__':
    main()
