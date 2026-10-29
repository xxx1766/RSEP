"""Linux process snapshots; CPU is percent of one logical core, not host percent."""
import os
from pathlib import Path

def parse_stat(text, page_size):
    end = text.rindex(')')
    pid = int(text[:text.index(' ')])
    f = text[end + 2:].split()
    return dict(pid=pid, ppid=int(f[1]), ticks=int(f[11])+int(f[12]),
                start=int(f[19]), rss_kib=int(f[21])*page_size/1024,
                comm=text[text.index('(')+1:end])

def cpu_delta(a, b, elapsed, hz):
    if (a['pid'], a['start']) != (b['pid'], b['start']):
        raise ValueError('process identity changed')
    if b['ticks'] < a['ticks'] or elapsed <= 0 or hz <= 0:
        raise ValueError('invalid CPU interval')
    return (b['ticks']-a['ticks']) / hz / elapsed * 100

def processes():
    result = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try:
            p = parse_stat((path/'stat').read_text(), os.sysconf('SC_PAGE_SIZE'))
            p['cmd'] = (path/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
            p['cgroup'] = (path/'cgroup').read_text().strip()
            result[p['pid']] = p
        except (FileNotFoundError, ProcessLookupError, PermissionError): pass
    return result

def memory(p):
    try:
        fields = dict(line.split(':', 1) for line in Path(f"/proc/{p['pid']}/smaps_rollup").read_text().splitlines() if ':' in line)
        p['pss_kib'] = int(fields['Pss'].split()[0])
        p['rss_kib'] = int(fields['Rss'].split()[0])
    except (FileNotFoundError, ProcessLookupError):
        p['pss_kib'] = None
    return p

def descendants(all_procs, root):
    selected={root}
    while True:
        extra={pid for pid,p in all_procs.items() if p['ppid'] in selected}
        new=selected|extra
        if new==selected: return selected
        selected=new

def container_root(cgroup):
    path=cgroup.strip().split('::',1)[1]
    return path.removesuffix('/init.scope')

def summarize_samples(samples, start, end, hz):
    """Clip intervals to window; linearly apportion ticks and time-weight memory.

    Birth/death intervals are counted as missing, never assumed to consume zero.
    Output missing_cpu_intervals makes the sampling limitation auditable.
    """
    groups={p['group'] for s in samples for p in s['processes']}
    out={g:dict(cpu_s=0.,rss_area=0.,pss_area=0.,seconds=0.,missing_cpu_intervals=0,missing_pss=0) for g in groups}
    for a,b in zip(samples,samples[1:]):
        dt=b['monotonic_s']-a['monotonic_s']
        if dt<=0: raise ValueError('samples must have increasing times')
        overlap=max(0,min(end,b['monotonic_s'])-max(start,a['monotonic_s']))
        if not overlap: continue
        before={(p['pid'],p['start']):p for p in a['processes']}
        after={(p['pid'],p['start']):p for p in b['processes']}
        for g in groups:
            ids={key for key,p in before.items() if p['group']==g}
            new={key for key,p in after.items() if p['group']==g}
            r=out[g]; r['seconds']+=overlap
            r['missing_cpu_intervals']+=len(ids^new)
            for key in ids&new:
                r['cpu_s']+=cpu_delta(before[key],after[key],dt,hz)/100*overlap
            for p in before.values():
                if p['group']!=g: continue
                r['rss_area']+=p['rss_kib']/1024*overlap
                if p.get('pss_kib') is None: r['missing_pss']+=1
                else: r['pss_area']+=p['pss_kib']/1024*overlap
    for g,r in out.items():
        if r['seconds']<=0: raise ValueError('no sample coverage')
        r['cpu_pct']=100*r['cpu_s']/r['seconds']
        r['rss_mib']=r.pop('rss_area')/r['seconds']
        r['pss_mib']=r.pop('pss_area')/r['seconds']
    return out

def inherited_cgroup_value(leaf, name, root=Path('/sys/fs/cgroup')):
    """Find effective controller metadata when a leaf has no delegated controller.

    This reports the nearest available value, not a substitute for checking the
    actual process affinity. Keep the source path so inherited values are explicit.
    """
    leaf=Path(leaf); root=Path(root)
    if leaf!=root and root not in leaf.parents: raise ValueError('cgroup is outside root')
    while True:
        path=leaf/name
        if path.exists(): return path.read_text().strip(),str(leaf)
        if leaf==root: return None,None
        leaf=leaf.parent
