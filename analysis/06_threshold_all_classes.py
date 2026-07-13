"""
Threshold sensitivity for ALL THREE condition classes (intact / moderate / severe).

Extends 01_threshold_sensitivity.py from the single intact class to a per-class,
one-vs-rest threshold sweep. For class C the positive label is (ClassId == C) and the
score is p_C (that class's MULTIPROBABILITY band). Same spatial 5-fold OOF predictions,
same metrics, same "ideal threshold" pickers (Youden J / max-F1 / max-OA) as script 01.

Inputs (already cached, no Earth Engine call needed for the accuracy curves):
  data/oof_3band.json  -> rows {ClassId, p_intact, p_moderate, p_severe, efg_id, fold}

Outputs:
  results/threshold_accuracy_moderate.json
  results/threshold_accuracy_severe.json
  results/threshold_accuracy_intact_3band.json   (recomputed on same 3-band OOF as a check)
  results/threshold_all_classes_summary.json
"""
import os, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
def data_path(name): return os.path.join(HERE, 'data', name)
def res_path(name): return os.path.join(HERE, 'results', name)

CLASS_NAME = {0: 'intact', 1: 'moderate', 2: 'severe'}
PROB_COL = {0: 'p_intact', 1: 'p_moderate', 2: 'p_severe'}


def threshold_accuracy(class_ids, probs, positive_class):
    """One-vs-rest threshold sweep for `positive_class`.

    Identical metric definitions to 01_threshold_sensitivity.threshold_accuracy,
    generalised: positive = (ClassId == positive_class), score = probs (p_class)."""
    y_true = (np.asarray(class_ids) == positive_class).astype(int)
    p = np.asarray(probs, dtype=float)
    N = len(p)
    n_pos = int(y_true.sum())
    n_neg = int(N - n_pos)

    taus = np.round(np.arange(0.0, 1.0001, 0.01), 4)
    res = {k: [] for k in ['thresholds', 'overall_accuracy', 'precision', 'recall',
                           'specificity', 'f1', 'balanced_accuracy', 'youden_J']}
    for tau in taus:
        pred_pos = (p >= tau).astype(int)
        tp = int(np.sum((pred_pos == 1) & (y_true == 1)))
        fp = int(np.sum((pred_pos == 1) & (y_true == 0)))
        tn = int(np.sum((pred_pos == 0) & (y_true == 0)))
        fn = int(np.sum((pred_pos == 0) & (y_true == 1)))
        acc = (tp + tn) / N if N else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0        # sensitivity
        spec = tn / (tn + fp) if (tn + fp) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        bal = (rec + spec) / 2.0
        youden = rec + spec - 1.0
        res['thresholds'].append(float(tau))
        res['overall_accuracy'].append(round(acc, 6))
        res['precision'].append(round(prec, 6))
        res['recall'].append(round(rec, 6))
        res['specificity'].append(round(spec, 6))
        res['f1'].append(round(f1, 6))
        res['balanced_accuracy'].append(round(bal, 6))
        res['youden_J'].append(round(youden, 6))

    def pick(metric):
        arr = np.asarray(res[metric])
        i = int(np.argmax(arr))
        t = res['thresholds'][i]
        return {'threshold': t, 'youden_J': res['youden_J'][i], 'f1': res['f1'][i],
                'overall_accuracy': res['overall_accuracy'][i], 'recall': res['recall'][i],
                'specificity': res['specificity'][i], 'precision': res['precision'][i],
                'balanced_accuracy': res['balanced_accuracy'][i]}

    # ROC AUC (trapezoid over recall vs (1-specificity))
    fpr = 1.0 - np.asarray(res['specificity'])
    tpr = np.asarray(res['recall'])
    order = np.argsort(fpr)
    _trap = getattr(np, 'trapezoid', None) or np.trapz  # NumPy 2.x renamed trapz->trapezoid
    auc = float(_trap(tpr[order], fpr[order]))

    res['ideal'] = {'by_youden': pick('youden_J'),
                    'by_f1': pick('f1'),
                    'by_overall_accuracy': pick('overall_accuracy')}
    res['roc_auc'] = round(abs(auc), 6)
    res['positive_class'] = positive_class
    res['positive_class_name'] = CLASS_NAME[positive_class]
    res['n_points'] = int(N)
    res['n_positive'] = n_pos
    res['n_negative'] = n_neg
    return res


def main():
    with open(data_path('oof_3band.json')) as fh:
        rows = json.load(fh)['rows']
    class_ids = [r['ClassId'] for r in rows]
    print(f'Loaded {len(rows)} OOF rows (3-band).')

    summary = {'n_points': len(rows), 'per_class': {}}
    for cid in (0, 1, 2):
        probs = [r[PROB_COL[cid]] for r in rows]
        acc = threshold_accuracy(class_ids, probs, cid)
        name = CLASS_NAME[cid]
        suffix = name if cid != 0 else 'intact_3band'
        with open(res_path(f'threshold_accuracy_{suffix}.json'), 'w') as fh:
            json.dump(acc, fh, indent=2)

        yj = acc['ideal']['by_youden']
        f1 = acc['ideal']['by_f1']
        summary['per_class'][name] = {
            'positive_class': cid,
            'n_positive': acc['n_positive'],
            'n_negative': acc['n_negative'],
            'prevalence': round(acc['n_positive'] / acc['n_points'], 4),
            'roc_auc': acc['roc_auc'],
            'ideal_threshold_youden': yj,
            'ideal_threshold_f1': f1,
            'ideal_threshold_overall_accuracy': acc['ideal']['by_overall_accuracy'],
        }
        print(f"\n== {name.upper()} (positive = ClassId {cid}, score = {PROB_COL[cid]}) ==")
        print(f"  n_pos={acc['n_positive']}  n_neg={acc['n_negative']}  ROC AUC={acc['roc_auc']}")
        print(f"  ideal tau (Youden J): {yj['threshold']:.2f}  "
              f"J={yj['youden_J']:.3f}  F1={yj['f1']:.3f}  OA={yj['overall_accuracy']:.3f}  "
              f"sens={yj['recall']:.3f}  spec={yj['specificity']:.3f}")
        print(f"  ideal tau (max-F1)  : {f1['threshold']:.2f}  "
              f"F1={f1['f1']:.3f}  prec={f1['precision']:.3f}  rec={f1['recall']:.3f}")

    with open(res_path('threshold_all_classes_summary.json'), 'w') as fh:
        json.dump(summary, fh, indent=2)
    print('\nWrote results/threshold_all_classes_summary.json')


if __name__ == '__main__':
    main()
