#!/usr/bin/env python3
# scripts/generate_agent_context.py
import re
import sys
import pathlib
import datetime

ROOT = pathlib.Path('.').resolve()

def read_text(path):
    try:
        return path.read_text(encoding='utf8')
    except Exception:
        return ''

def first_paragraph(text, max_len=400):
    for block in text.split('\n\n'):
        s = ' '.join(line.strip() for line in block.splitlines() if line.strip())
        if s:
            return s[:max_len]
    return ''

def yaml_dump(obj, indent=0):
    sp = '  ' * indent
    if isinstance(obj, dict):
        lines = []
        for k, v in obj.items():
            if v is None:
                lines.append(f"{sp}{k}:")
            elif isinstance(v, (dict, list)):
                lines.append(f"{sp}{k}:")
                lines.append(yaml_dump(v, indent+1))
            else:
                lines.append(f"{sp}{k}: {yaml_scalar(v)}")
        return '\n'.join(lines)
    if isinstance(obj, list):
        lines = []
        for item in obj:
            if isinstance(item, (dict, list)):
                lines.append(f"{sp}-")
                lines.append(yaml_dump(item, indent+1))
            else:
                lines.append(f"{sp}- {yaml_scalar(item)}")
        return '\n'.join(lines)
    return f"{sp}{yaml_scalar(obj)}"

def yaml_scalar(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if v is None:
        return 'null'
    s = str(v)
    if any(c in s for c in ':\n#{}[],&*?|-<>=!%@\\') or s.strip() == '' or s.startswith(' '):
        return f"\"{s.replace('\"','\\\"')}\""
    return s

def scan_css(css_path):
    txt = read_text(css_path)
    vars_ = sorted(set(re.findall(r'--[A-Za-z0-9_-]+', txt)))
    # capture .class and #id selectors
    sels = sorted(set(re.findall(r'([.#][A-Za-z0-9_-]+)', txt)))
    # also add common tag selectors by finding tokens before '{'
    tag_candidates = set()
    for part in re.split(r'\{', txt):
        head = part.split('}')[-1] if '}' in part else part
    # Return limited selectors
    return {'path': str(css_path), 'variables': vars_, 'key_selectors': sels[:60]}

def scan_python_package(app_dir):
    res = []
    for p in sorted(app_dir.glob('*.py')):
        res.append(p.name)
    return res

def parse_env(env_path):
    txt = read_text(env_path)
    names = []
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
        if m:
            names.append(m.group(1))
    return sorted(names)

def main():
    out = {
        'version': 1,
        'repo': {
            'name': ROOT.name,
            'generated_at': datetime.datetime.utcnow().isoformat() + 'Z'
        },
        'summary': {},
        'structure': {
            'top_level_files': [],
            'key_folders': []
        },
        'entry_points': {},
        'dependencies': {},
        'runtime': {},
        'important_files': [],
        'frontend': {'css': []},
        'notes_for_agents': [
            "Read README.md first, then this file.",
            "Do not store secrets here; list env var names only."
        ]
    }

    for fname in ('README.md', 'requirements.txt', 'Dockerfile', '.env'):
        p = ROOT / fname
        if p.exists():
            out['structure']['top_level_files'].append(fname)
            if fname == 'README.md':
                out['summary']['brief'] = first_paragraph(read_text(p))
    for d in ('app', 'data', 'static', 'scripts', 'tests'):
        p = ROOT / d
        if p.exists():
            out['structure']['key_folders'].append(f"{d}/")

    app_dir = ROOT / 'app'
    if app_dir.exists():
        out['entry_points']['python_package'] = 'app'
        if (app_dir / 'main.py').exists():
            out['entry_points']['main_module'] = 'app/main.py'
        out['important_files'].extend([str(x) for x in sorted(app_dir.glob('*.py'))])
        out['entry_points']['app_files'] = scan_python_package(app_dir)

    req = ROOT / 'requirements.txt'
    if req.exists():
        reqs = [ln.strip() for ln in read_text(req).splitlines() if ln.strip() and not ln.strip().startswith('#')]
        out['dependencies']['manifest'] = 'requirements.txt'
        out['dependencies']['entries'] = reqs

    env = ROOT / '.env'
    if env.exists():
        out['runtime']['env_vars'] = parse_env(env)

    # CSS files
    for css in sorted(ROOT.rglob('*.css')):
        out['frontend']['css'].append(scan_css(css))
        out['important_files'].append(str(css))

    # tests presence
    if (ROOT / 'tests').exists():
        out['important_files'].append('tests/')

    # make some defaults
    out['entry_points']['test_command'] = 'pytest -q'
    out['runtime']['env_files'] = ['.env']

    # output YAML
    print(yaml_dump(out))

if __name__ == '__main__':
    main()