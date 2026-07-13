"""
05 — Emit a compact JSON for the artifact's method-comparison / band-combination section.
Reads results/symbolic_results.json -> writes results/symbolic_artifact.json.
"""
import os, json

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, 'results')
d = json.load(open(os.path.join(R, 'symbolic_results.json'), encoding='utf-8'))

sm = d['standard_methods']
methods = []
label = {'youden_J': "Youden's J (max TSS)", 'f1': 'Max F1 (intact)',
         'kappa': 'Max Cohen’s κ', 'accuracy': 'Max overall accuracy',
         'sens_eq_spec': 'Sensitivity = specificity'}
for k in ['youden_J', 'f1', 'kappa', 'accuracy', 'sens_eq_spec']:
    v = sm.get(k)
    if not v:
        continue
    methods.append({'method': label[k], 'threshold': round(v['threshold'], 3),
                    'f1': v['f1'], 'youden_J': v['youden_J'], 'kappa': v['kappa'],
                    'accuracy': v['accuracy'], 'recall': v['recall'], 'specificity': v['specificity']})

# band-combination leaderboard: crafted rules + best PySR, by held-out test F1
rules = []
for name, v in d['crafted_rules'].items():
    rules.append({'rule': name.replace('  [PySR motif]', ''), 'test_f1': v['test_f1'],
                  'test_auc': v['test_auc'], 'threshold': round(v['threshold'], 4),
                  'kind': 'baseline' if 'baseline' in name else 'crafted'})
# add PySR best (complexity>1) if distinct
front = d['symbolic'].get('front', [])
if front:
    # the simplest (complexity 1) is p_i == baseline; include the best non-trivial by test_f1
    nontrivial = [e for e in front if e['complexity'] > 1]
    if nontrivial:
        bestp = max(nontrivial, key=lambda e: e['test_f1'])
        rules.append({'rule': f"PySR best: {bestp['equation']}", 'test_f1': bestp['test_f1'],
                      'test_auc': bestp.get('full_auc', bestp.get('test_auc')),
                      'threshold': round(bestp['threshold'], 4), 'kind': 'pysr'})
rules.sort(key=lambda r: r['test_f1'], reverse=True)

# pareto front: complexity vs test F1 vs mse
pareto = [{'complexity': e['complexity'], 'test_f1': e['test_f1'],
           'train_f1': e.get('train_f1'), 'mse': e.get('loss_mse', e.get('loss')),
           'equation': e['equation']} for e in front]
pareto.sort(key=lambda e: e['complexity'])

baseline_f1 = d['crafted_rules']['p_intact (baseline)']['test_f1']

out = {
    'n': d['n'], 'n_intact': d['n_intact'],
    'sampling': d['sampling'], 'split': d['split'],
    'roc_auc': sm['roc_auc'],
    'methods': methods,
    'consensus_threshold': methods[0]['threshold'],  # they converge
    'rules': rules,
    'baseline_test_f1': baseline_f1,
    'pareto': pareto,
    'headline': d['headline'],
    'refdata': {'intact': 751, 'moderate': 633, 'severe': 820, 'transformed': 778, 'bontveld': 96,
                'modelled': ['intact', 'moderate', 'severe'], 'excluded': ['transformed', 'bontveld']},
}
with open(os.path.join(R, 'symbolic_artifact.json'), 'w', encoding='utf-8') as fh:
    json.dump(out, fh)
print('wrote symbolic_artifact.json; methods=%d rules=%d pareto=%d consensus_tau=%.3f best_rule=%s' % (
    len(methods), len(rules), len(pareto), out['consensus_threshold'], rules[0]['rule']))
