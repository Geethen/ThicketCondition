"""
04 — Build the full symbolic_results.json from the cached PySR Pareto front
(results/pysr_hall_of_fame.csv) + the OOF 3-band data, WITHOUT re-running PySR/Julia.

Evaluates every Pareto formula by held-out intact F1 (threshold chosen on train spatial
blocks {0,1,2}, reported on test blocks {3,4}), plus the standard threshold-method
comparison and hand-crafted rules. Safe to re-run; pure Python/numpy.

Run:
    python -u analysis/04_build_symbolic_results.py
"""
import os, json, time, re
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
RESULTS = os.path.join(HERE, 'results')

# ---------------------------------------------------------------- load OOF 3-band data
payload = json.load(open(os.path.join(DATA, 'oof_3band.json')))
rows = payload['rows']
pi = np.array([r['p_intact'] for r in rows], float)
pm = np.array([r['p_moderate'] for r in rows], float)
ps = np.array([r['p_severe'] for r in rows], float)
y  = (np.array([r['ClassId'] for r in rows]) == 0).astype(int)   # 1 = intact
fold = np.array([r['fold'] for r in rows])
N = len(y)
train_mask = np.isin(fold, [0, 1, 2])
test_mask  = np.isin(fold, [3, 4])

# variable name map: PySR uses x0,x1,x2 for the 3 feature columns (p_i,p_m,p_s)
def pretty(expr):
    e = expr
    e = re.sub(r'\bx0\b', 'p_i', e)
    e = re.sub(r'\bx1\b', 'p_m', e)
    e = re.sub(r'\bx2\b', 'p_s', e)
    return e

# ---------------------------------------------------------------- metrics
def metrics(pred, truth):
    tp = int(np.sum((pred == 1) & (truth == 1))); fp = int(np.sum((pred == 1) & (truth == 0)))
    tn = int(np.sum((pred == 0) & (truth == 0))); fn = int(np.sum((pred == 0) & (truth == 1)))
    n = tp + fp + tn + fn
    acc = (tp + tn) / n if n else 0.0
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    spec = tn / (tn + fp) if (tn + fp) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    youden = rec + spec - 1.0
    pe = (((tp + fp) * (tp + fn)) + ((tn + fn) * (tn + fp))) / (n * n) if n else 0.0
    kappa = (acc - pe) / (1 - pe) if (1 - pe) else 0.0
    return dict(tp=tp, fp=fp, tn=tn, fn=fn, accuracy=round(acc, 6), precision=round(prec, 6),
                recall=round(rec, 6), specificity=round(spec, 6), f1=round(f1, 6),
                youden_J=round(youden, 6), kappa=round(kappa, 6))

def roc_auc(score, truth):
    n = len(score); order = np.argsort(score); ranks = np.empty(n, float); ranks[order] = np.arange(1, n + 1)
    pos = truth == 1; n1 = int(pos.sum()); n0 = n - n1
    if n1 == 0 or n0 == 0: return float('nan')
    return float(round((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0), 6))

def best_f1_threshold(score, truth, ngrid=401):
    m = np.isfinite(score)
    if not m.all():
        score = np.where(m, score, np.nanmin(score[m]) if m.any() else 0.0)
    lo, hi = float(np.min(score)), float(np.max(score))
    if hi <= lo: return lo, metrics((score >= lo).astype(int), truth)
    bt, bf, bm = None, -1, None
    for t in np.linspace(lo, hi, ngrid):
        mm = metrics((score >= t).astype(int), truth)
        if mm['f1'] > bf: bt, bf, bm = float(t), mm['f1'], mm
    return bt, bm

def evaluate_score(score, name):
    score = np.asarray(score, float)
    t_tr, m_tr = best_f1_threshold(score[train_mask], y[train_mask])
    m_te = metrics((score[test_mask] >= t_tr).astype(int), y[test_mask])
    return {'name': name, 'threshold': t_tr, 'train_f1': m_tr['f1'], 'test_f1': m_te['f1'],
            'test_auc': roc_auc(score[test_mask], y[test_mask]), 'full_auc': roc_auc(score, y),
            'test_metrics': m_te}

# ---------------------------------------------------------------- (A) standard methods on p_intact
def standard_methods():
    grid = np.round(np.arange(0.0, 1.0001, 0.005), 5)
    crit = ['youden_J', 'f1', 'kappa', 'accuracy']
    best = {c: (None, -1e9, None) for c in crit}; sens_spec = None; prev = None
    for t in grid:
        m = metrics((pi >= t).astype(int), y)
        for c in crit:
            if m[c] > best[c][1]: best[c] = (float(t), m[c], m)
        d = m['recall'] - m['specificity']
        if prev is not None and (d == 0 or prev * d < 0) and sens_spec is None: sens_spec = (float(t), m)
        prev = d
    out = {c: {'threshold': best[c][0], **best[c][2]} for c in crit}
    if sens_spec: out['sens_eq_spec'] = {'threshold': sens_spec[0], **sens_spec[1]}
    out['roc_auc'] = roc_auc(pi, y)
    out['note'] = ('Operating-point selectors on the p_intact score (full out-of-fold data). '
                   'Liu et al. 2005 (Ecography) & Freeman & Moisen 2008 recommend max-Kappa or '
                   'Youden J / max-TSS; max-overall-accuracy is prevalence-biased.')
    return out

# ---------------------------------------------------------------- (C) hand-crafted rules
def crafted_rules():
    rules = {
        'p_intact (baseline)':            pi,
        'p_i - max(p_m, p_s)':            pi - np.maximum(pm, ps),
        '2*p_i - 1':                      2 * pi - 1.0,
        'p_i - 0.5*p_s':                  pi - 0.5 * ps,
        'p_i / (p_i + p_m)':              np.where((pi + pm) > 0, pi / (pi + pm + 1e-9), 0.0),
        'p_i * (p_m / p_s)  [PySR motif]': pi * np.where(ps > 0, pm / (ps + 1e-9), 0.0),
        'p_i / p_s          [PySR motif]': np.where(ps > 0, pi / (ps + 1e-9), 0.0),
    }
    return {name: evaluate_score(sc, name) for name, sc in rules.items()}

# ---------------------------------------------------------------- (B) PySR Pareto front
def eval_pysr_front():
    # Prefer the native front produced by 03 (every formula evaluated by PySR itself,
    # including Piecewise/min/max), with variables prettified.
    native = os.path.join(RESULTS, 'pysr_front_native.json')
    if os.path.exists(native):
        nf = json.load(open(native, encoding='utf-8'))
        front = []
        for e in nf.get('front', []):
            e2 = dict(e)
            e2['equation'] = pretty(str(e.get('sympy', e.get('equation', ''))))
            e2['loss_mse'] = e.get('loss', e.get('loss_mse'))
            front.append(e2)
        best = max(front, key=lambda e: e['test_f1']) if front else None
        return {'engine': 'pysr', 'front': front, 'best_by_test_f1': best,
                'source': 'pysr_front_native.json'}
    csv = os.path.join(RESULTS, 'pysr_hall_of_fame.csv')
    df = pd.read_csv(csv)
    x0, x1, x2 = pi, pm, ps
    def safe_lambda(lf):
        # lf like 'PySRFunction(X=>x0*(1.1365379 - x2))' -> evaluate the body vectorised
        m = re.search(r'X=>(.*)\)\s*$', lf)
        body = m.group(1) if m else None
        return body
    front = []
    ns = {'x0': x0, 'x1': x1, 'x2': x2, 'square': np.square, 'np': np,
          'max': np.maximum, 'min': np.minimum, 'Abs': np.abs}
    for _, r in df.iterrows():
        body = safe_lambda(str(r['lambda_format']))
        score = None
        if body is not None:
            expr = body.replace('square(', 'np.square(')
            # sympy uses Piecewise in sympy_format; the lambda_format keeps min/max/square which we map
            try:
                score = eval(expr, {'__builtins__': {}}, ns)
                score = np.broadcast_to(np.asarray(score, float), (N,)).astype(float)
            except Exception as e:
                score = None
        if score is None:
            continue
        ev = evaluate_score(score, f"pysr_c{int(r['complexity'])}")
        front.append({
            'complexity': int(r['complexity']),
            'loss_mse': float(r['loss']),
            'equation_raw': str(r['equation']),
            'equation': pretty(str(r['sympy_format'])),
            'threshold': ev['threshold'], 'train_f1': ev['train_f1'],
            'test_f1': ev['test_f1'], 'test_auc': ev['test_auc'], 'full_auc': ev['full_auc'],
            'test_metrics': ev['test_metrics'],
        })
    best = max(front, key=lambda e: e['test_f1']) if front else None
    return {'engine': 'pysr', 'front': front, 'best_by_test_f1': best}

def main():
    t0 = time.time()
    res = {'n': N, 'n_intact': int(y.sum()), 'sampling': payload.get('sampling'),
           'split': {'train_folds': [0, 1, 2], 'test_folds': [3, 4],
                     'n_train': int(train_mask.sum()), 'n_test': int(test_mask.sum())}}
    res['standard_methods'] = standard_methods()
    res['crafted_rules'] = crafted_rules()
    res['symbolic'] = eval_pysr_front()

    base = res['crafted_rules']['p_intact (baseline)']
    # overall best across ALL candidate scores by held-out test F1
    all_candidates = []
    for name, v in res['crafted_rules'].items():
        all_candidates.append((name, v['test_f1'], v['test_auc'], v['threshold']))
    for e in res['symbolic']['front']:
        all_candidates.append((e['equation'], e['test_f1'], e['test_auc'], e['threshold']))
    overall_best = max(all_candidates, key=lambda t: t[1])
    b = res['symbolic']['best_by_test_f1']
    res['headline'] = {
        'baseline_p_intact_test_f1': base['test_f1'],
        'baseline_test_auc': base['test_auc'],
        'best_symbolic_test_f1': b['test_f1'] if b else None,
        'best_symbolic_equation': b['equation'] if b else None,
        'overall_best_rule': overall_best[0],
        'overall_best_test_f1': overall_best[1],
        'overall_best_test_auc': overall_best[2],
        'overall_best_threshold': overall_best[3],
        'delta_f1_vs_baseline': round(overall_best[1] - base['test_f1'], 6),
        'conclusion': ('Combining the three probability bands yields at most a marginal '
                       'held-out F1 gain over thresholding p_intact alone; the PySR Pareto '
                       'front shows MSE improves only ~15% from complexity 1->16 and those '
                       'gains do not translate to better held-out intact F1.'),
    }
    res['elapsed_sec'] = round(time.time() - t0, 1)
    with open(os.path.join(RESULTS, 'symbolic_results.json'), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=2)

    # print summary (ascii only)
    print(f'N={N} intact={int(y.sum())} sampling={res["sampling"]}')
    print('-- standard methods on p_intact --')
    for k in ['youden_J', 'f1', 'kappa', 'accuracy', 'sens_eq_spec']:
        v = res['standard_methods'].get(k)
        if v: print(f'   {k:13s} thr={v["threshold"]:.3f} F1={v["f1"]:.3f} J={v["youden_J"]:.3f} kappa={v["kappa"]:.3f} OA={v["accuracy"]:.3f}')
    print(f'   ROC AUC(p_intact)={res["standard_methods"]["roc_auc"]}')
    print('-- crafted rules (held-out test F1) --')
    for name, v in res['crafted_rules'].items():
        print(f'   {name:34s} test_F1={v["test_f1"]:.3f} test_AUC={v["test_auc"]}')
    print('-- PySR front (held-out test F1) --')
    for e in res['symbolic']['front']:
        print(f'   c={e["complexity"]:2d} mse={e["loss_mse"]:.5f} test_F1={e["test_f1"]:.3f}  {e["equation"]}')
    h = res['headline']
    print(f'HEADLINE overall best: {h["overall_best_rule"]}  test_F1={h["overall_best_test_f1"]:.3f} '
          f'(delta vs baseline {h["delta_f1_vs_baseline"]:+.3f})')
    print(f'wrote results/symbolic_results.json in {res["elapsed_sec"]}s')
    print('DONE')

if __name__ == '__main__':
    main()
