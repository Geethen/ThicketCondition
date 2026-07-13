"""Inject a results JSON into the HTML artifact template, replacing __DATA_PLACEHOLDER__
(first run) or the existing <script id="DATA"> contents (subsequent runs).

Usage:
  python inject_artifact_data.py [DATA_JSON] [HTML_FILE]
Defaults:
  DATA_JSON = results/artifact_data.json
  HTML_FILE = ../threshold_sensitivity.html
"""
import json, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'results', 'artifact_data.json')
HTML = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'threshold_sensitivity.html')

with open(DATA, encoding='utf-8') as f:
    data = json.load(f)
# compact but safe JSON; ensure no </script> can break out (none expected in numeric data)
blob = json.dumps(data, separators=(',', ':'))
assert '</script' not in blob.lower(), 'unexpected script terminator in data'

with open(HTML, encoding='utf-8') as f:
    html = f.read()

if '__DATA_PLACEHOLDER__' in html:
    html = html.replace('__DATA_PLACEHOLDER__', blob)
    mode = 'placeholder->data'
else:
    # already injected once; replace the existing DATA script contents
    import re
    pat = re.compile(r'(<script id="DATA" type="application/json">)(.*?)(</script>)', re.S)
    html, n = pat.subn(lambda m: m.group(1) + '\n' + blob + '\n' + m.group(3), html)
    mode = f'reinjected ({n})'

with open(HTML, 'w', encoding='utf-8') as f:
    f.write(html)

print(f'injected: {mode}; data bytes={len(blob)}; ideal_tau={data["summary"]["ideal_threshold_youden"]["threshold"]}')
