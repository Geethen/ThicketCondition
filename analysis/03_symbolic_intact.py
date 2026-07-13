"""
03 — Discover the best combination of the three probability bands for detecting INTACT
thicket, and compare standard remote-sensing threshold-selection methods.

Objective: maximise F1 of the intact class (positive class = intact, ClassId 0).
Features: p_i = p_intact, p_m = p_moderate, p_s = p_severe  (constrained p_i+p_m+p_s = 1).

Symbolic search: PySR (Pareto front of accuracy-vs-complexity formulas). Each formula is a
closed-form expression in (p_i, p_m, p_s) that can be dropped straight into Earth Engine.
For every formula we sweep a threshold to maximise intact-F1 on the TRAIN spatial blocks and
report the held-out F1 on the TEST spatial blocks — so the reported gain is out-of-sample.

Input : data/oof_3band.json   (from 02_sample_oof_3band.py)
Output: results/symbolic_results.json
        results/pysr_hall_of_fame.csv (PySR's raw Pareto front)

Run:
    python -u analysis/03_symbolic_intact.py

Notes for a resuming agent:
  * PySR needs its Julia backend; first ever import precompiles (~6 min) then caches.
  * If PySR is unavailable, set USE_PYSR=False to fall back to gplearn (pip install gplearn).
  * The out-of-fold probabilities are already spatially cross-validated, so they are honest;
    the extra train/test block split here guards the *formula* against over-fitting.
"""
import os, json, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
RESULTS = os.path.join(HERE, 'results')
os.makedirs(RESULTS, exist_ok=True)

USE_PYSR = True
RNG = 42
np.random.seed(RNG)

# ---------------------------------------------------------------- load OOF 3-band data
payload = json.load(open(os.path.join(DATA, 'oof_3band.json')))
rows = payload['rows']
pi = np.array([r['p_intact'] for r in rows], float)
pm = np.array([r['p_moderate'] for r in rows], float)
ps = np.array([r['p_severe'] for r in rows], float)
y  = (np.array([r['ClassId'] for r in rows]) == 0).astype(int)   # 1 = intact
fold = np.array([r['fold'] for r in rows])
N = len(y)
X = np.column_stack([pi, pm, ps])

# spatial split: whole blocks (folds) -> no leakage between formula-fit and validation
train_mask = np.isin(fold, [0, 1, 2])
test_mask  = np.isin(fold, [3, 4])

# ---------------------------------------------------------------- metric helpers
def metrics(pred, truth):
    tp = int(np.sum((pred == 1) & (truth == 1)))
    fp = int(np.sum((pred == 1) & (truth == 0)))
    tn = int(np.sum((pred == 0) & (truth == 0)))
    fn = int(np.sum((pred == 0) & (truth == 1)))
    n = tp + fp + tn + fn
    acc = (tp + tn) / n if n else 0.0
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    spec = tn / (tn + fp) if (tn + fp) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    youden = rec + spec - 1.0
    po = acc
    pe = (((tp + fp) * (tp + fn)) + ((tn + fn) * (tn + fp))) / (n * n) if n else 0.0
    kappa = (po - pe) / (1 - pe) if (1 - pe) else 0.0
    return dict(tp=tp, fp=fp, tn=tn, fn=fn, accuracy=round(acc, 6), precision=round(prec, 6),
                recall=round(rec, 6), specificity=round(spec, 6), f1=round(f1, 6),
                youden_J=round(youden, 6), kappa=round(kappa, 6))

def roc_auc(score, truth):
    n = len(score)
    order = np.argsort(score); ranks = np.empty(n, float); ranks[order] = np.arange(1, n + 1)
    pos = truth == 1; n1 = int(pos.sum()); n0 = n - n1
    if n1 == 0 or n0 == 0: return float('nan')
    return float(round((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0), 6))

def best_f1_threshold(score, truth, ngrid=401):
    lo, hi = float(np.min(score)), float(np.max(score))
    if hi <= lo:
        return lo, metrics((score >= lo).astype(int), truth)
    grid = np.linspace(lo, hi, ngrid)
    bt, bf, bm = None, -1, None
    for t in grid:
        m = metrics((score >= t).astype(int), truth)
        if m['f1'] > bf:
            bt, bf, bm = float(t), m['f1'], m
    return bt, bm

def evaluate_score(score, name):
    """Threshold chosen on TRAIN blocks (max F1), reported on TEST blocks. Full-data AUC too."""
    t_tr, m_tr = best_f1_threshold(score[train_mask], y[train_mask])
    m_te = metrics((score[test_mask] >= t_tr).astype(int), y[test_mask])
    return {'name': name, 'threshold': t_tr,
            'train_f1': m_tr['f1'], 'test_f1': m_te['f1'],
            'test_auc': roc_auc(score[test_mask], y[test_mask]),
            'full_auc': roc_auc(score, y),
            'test_metrics': m_te}

# ---------------------------------------------------------------- (A) standard threshold methods on p_intact
def standard_methods_on_full():
    grid = np.round(np.arange(0.0, 1.0001, 0.005), 5)
    crit = ['youden_J', 'f1', 'kappa', 'accuracy']
    best = {c: (None, -1e9, None) for c in crit}
    sens_spec = None; prev = None
    for t in grid:
        m = metrics((pi >= t).astype(int), y)
        for c in crit:
            if m[c] > best[c][1]:
                best[c] = (float(t), m[c], m)
        d = m['recall'] - m['specificity']
        if prev is not None and (d == 0 or prev * d < 0) and sens_spec is None:
            sens_spec = (float(t), m)
        prev = d
    out = {c: {'threshold': best[c][0], **best[c][2]} for c in crit}
    if sens_spec: out['sens_eq_spec'] = {'threshold': sens_spec[0], **sens_spec[1]}
    out['roc_auc'] = roc_auc(pi, y)
    out['note'] = ('Standard operating-point selectors on the p_intact score (full out-of-fold '
                   'data). Liu et al. 2005 recommend max-Kappa or Youden J / max-TSS over '
                   'max-overall-accuracy (prevalence-biased).')
    return out

# ---------------------------------------------------------------- (C) hand-crafted band-combo rules (context/baseline)
def crafted_rules():
    rules = {
        'p_intact (baseline)':            pi,
        'p_i - max(p_m, p_s)':            pi - np.maximum(pm, ps),
        '2*p_i - 1  (= p_i - p_m - p_s)': 2 * pi - 1.0,
        'p_i - 0.5*p_s':                  pi - 0.5 * ps,
        'p_i / (p_i + p_m)':              np.where((pi + pm) > 0, pi / (pi + pm + 1e-9), 0.0),
    }
    return {name: evaluate_score(sc, name) for name, sc in rules.items()}

# ---------------------------------------------------------------- (B) symbolic model
def run_pysr():
    from pysr import PySRRegressor
    model = PySRRegressor(
        niterations=60,
        binary_operators=['+', '-', '*', '/', 'max', 'min'],
        unary_operators=['square'],
        maxsize=18,
        populations=24,
        population_size=40,
        ncycles_per_iteration=400,
        model_selection='best',
        elementwise_loss='loss(prediction, target) = (prediction - target)^2',
        constraints={'/': (-1, 4), 'max': (-1, -1), 'min': (-1, -1)},
        variable_names=['p_i', 'p_m', 'p_s'],
        random_state=RNG, deterministic=True, parallelism='serial',
        temp_equation_file=False, verbosity=0, progress=False,
    )
    model.fit(X[train_mask], y[train_mask])
    eqs = model.equations_
    # persist raw front
    try:
        eqs.to_csv(os.path.join(RESULTS, 'pysr_hall_of_fame.csv'), index=False)
    except Exception:
        pass
    front = []
    for i in range(len(eqs)):
        sc_all = np.asarray(model.predict(X, index=i)).astype(float)
        ev = evaluate_score(sc_all, f'pysr_{i}')
        front.append({
            'complexity': int(eqs.iloc[i]['complexity']),
            'loss': float(eqs.iloc[i]['loss']),
            'equation': str(eqs.iloc[i]['equation']),
            'sympy': str(model.sympy(i)),
            'threshold': ev['threshold'], 'train_f1': ev['train_f1'],
            'test_f1': ev['test_f1'], 'test_auc': ev['test_auc'], 'full_auc': ev['full_auc'],
            'test_metrics': ev['test_metrics'],
        })
    # best by held-out test F1
    best = max(front, key=lambda e: e['test_f1'])
    return {'engine': 'pysr', 'front': front, 'best_by_test_f1': best}

def run_gplearn():
    from gplearn.genetic import SymbolicRegressor
    sr = SymbolicRegressor(population_size=3000, generations=30,
                           function_set=('add', 'sub', 'mul', 'div', 'max', 'min'),
                           parsimony_coefficient=0.008, max_samples=0.9, random_state=RNG,
                           feature_names=['p_i', 'p_m', 'p_s'], const_range=(-2.0, 2.0),
                           p_crossover=0.7, p_subtree_mutation=0.1, p_hoist_mutation=0.05,
                           p_point_mutation=0.1, verbose=0, n_jobs=1)
    sr.fit(X[train_mask], y[train_mask])
    sc_all = np.asarray(sr.predict(X)).astype(float)
    ev = evaluate_score(sc_all, 'gplearn_best')
    return {'engine': 'gplearn',
            'best_by_test_f1': {'equation': str(sr._program), 'sympy': str(sr._program),
                                'threshold': ev['threshold'], 'train_f1': ev['train_f1'],
                                'test_f1': ev['test_f1'], 'test_auc': ev['test_auc'],
                                'full_auc': ev['full_auc'], 'test_metrics': ev['test_metrics']},
            'front': []}

def main():
    t0 = time.time()
    print(f'N={N} intact={int(y.sum())} sampling={payload.get("sampling")}', flush=True)
    print(f'spatial split: train(folds 0,1,2)={int(train_mask.sum())}  test(folds 3,4)={int(test_mask.sum())}', flush=True)

    res = {'n': N, 'n_intact': int(y.sum()), 'sampling': payload.get('sampling'),
           'split': {'train_folds': [0, 1, 2], 'test_folds': [3, 4],
                     'n_train': int(train_mask.sum()), 'n_test': int(test_mask.sum())}}

    print('== (A) standard threshold methods on p_intact ==', flush=True)
    res['standard_methods'] = standard_methods_on_full()
    for k in ['youden_J', 'f1', 'kappa', 'accuracy', 'sens_eq_spec']:
        v = res['standard_methods'].get(k)
        if v:
            print(f'  {k:13s} thr={v["threshold"]:.3f} F1={v["f1"]:.3f} J={v["youden_J"]:.3f} '
                  f'kappa={v["kappa"]:.3f} OA={v["accuracy"]:.3f}', flush=True)
    print(f'  ROC AUC(p_intact) = {res["standard_methods"]["roc_auc"]}', flush=True)

    print('== (C) hand-crafted band-combination rules (held-out F1) ==', flush=True)
    res['crafted_rules'] = crafted_rules()
    for name, v in res['crafted_rules'].items():
        print(f'  {name:32s} test_F1={v["test_f1"]:.3f} test_AUC={v["test_auc"]}', flush=True)

    print('== (B) symbolic model search ==', flush=True)
    try:
        res['symbolic'] = run_pysr() if USE_PYSR else run_gplearn()
    except Exception as e:
        print(f'  PySR failed ({str(e).splitlines()[0][:80]}); falling back to gplearn', flush=True)
        res['symbolic'] = run_gplearn()
    # persist the native front separately so 04 can merge it with crafted rules
    with open(os.path.join(RESULTS, 'pysr_front_native.json'), 'w', encoding='utf-8') as fh:
        json.dump(res['symbolic'], fh, indent=2)

    b = res['symbolic']['best_by_test_f1']
    print(f'  engine={res["symbolic"]["engine"]}', flush=True)
    print(f'  BEST formula: {b.get("sympy", b.get("equation"))}', flush=True)
    print(f'    threshold={b["threshold"]:.4f}  held-out test F1={b["test_f1"]:.3f}  test AUC={b["test_auc"]}', flush=True)

    # headline comparison: baseline vs best symbolic, on identical held-out test set
    base = res['crafted_rules']['p_intact (baseline)']
    res['headline'] = {
        'baseline_p_intact_test_f1': base['test_f1'],
        'best_symbolic_test_f1': b['test_f1'],
        'delta_f1': round(b['test_f1'] - base['test_f1'], 6),
        'baseline_test_auc': base['test_auc'],
        'best_symbolic_test_auc': b['test_auc'],
    }
    print(f'  HEADLINE: baseline test_F1={base["test_f1"]:.3f} -> symbolic test_F1={b["test_f1"]:.3f} '
          f'(delta {res["headline"]["delta_f1"]:+.3f})', flush=True)

    res['elapsed_sec'] = round(time.time() - t0, 1)
    with open(os.path.join(RESULTS, 'symbolic_results.json'), 'w') as fh:
        json.dump(res, fh, indent=2)
    print(f'wrote results/symbolic_results.json in {res["elapsed_sec"]}s', flush=True)
    print('DONE', flush=True)

if __name__ == '__main__':
    main()
