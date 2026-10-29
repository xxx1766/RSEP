"""Recover plotted vertices, not original sampling events, from the native SVG."""
import csv
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
NUMBER = r'[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?'
ROLES = {
    'rgb(0%, 43.920898%, 75.292969%)': 'KubeEdge-2C4C',
    'rgb(0%, 50.195312%, 0%)': 'KubeEdge-3C8C',
    'rgb(75.292969%, 0%, 0%)': 'Tide',
}


def coordinates(element):
    # These published paths contain only move/line commands; fail on curves.
    assert not re.sub(NUMBER + r'|[MLml\s,]', '', element['d'])
    values = list(map(float, re.findall(NUMBER, element['d'])))
    assert len(values) % 2 == 0
    transform = list(map(float, re.findall(NUMBER, element['transform'])))
    assert len(transform) == 6 and transform[1] == transform[2] == 0
    a, _, _, d, e, f = transform
    return [(a*x+e, d*y+f) for x, y in zip(values[::2], values[1::2])]


def extract(path):
    paths = [e.attrib for e in ET.parse(path).getroot().iter()
             if e.tag.endswith('path') and e.get('fill') == 'none' and e.get('stroke-width')]
    grids = [coordinates(e) for e in paths if e.get('stroke-width') == '0.5']
    vertical = sorted(p[0][0] for p in grids if len(p) == 2 and p[0][0] == p[1][0])
    horizontal = sorted((p[0][1] for p in grids if len(p) == 2 and p[0][1] == p[1][1]), reverse=True)
    assert len(vertical) == 11 and len(horizontal) == 8
    # Tick labels independently read from the original PDF: 0..100 seconds,
    # and 0..35 CPUs in steps of five. Small PDF-coordinate rounding remains.
    x0, x100 = vertical[0], vertical[-1]
    y0, y35 = horizontal[0], horizontal[-1]
    assert max(abs((x-x0)/(x100-x0)*100-i*10) for i, x in enumerate(vertical)) < .001
    assert max(abs((y0-y)/(y0-y35)*35-i*5) for i, y in enumerate(horizontal)) < .002
    rows = []
    for p in paths:
        if p.get('stroke-width') != '2' or p.get('stroke') not in ROLES:
            continue
        vertices = coordinates(p)
        if len(vertices) <= 2:  # Original legend segment, not a measured trace.
            continue
        for index, (x, y) in enumerate(vertices):
            rows.append(dict(platform=ROLES[p['stroke']], vertex_index=index,
                             time_s_from_pdf_vector=(x-x0)/(x100-x0)*100,
                             allocated_cpus_from_pdf_vector=(y0-y)/(y0-y35)*35))
    assert len(rows) == 77
    assert {role: sum(r['platform'] == role for r in rows) for role in ROLES.values()} == {
        'KubeEdge-2C4C': 15, 'KubeEdge-3C8C': 13, 'Tide': 49}
    return rows, dict(x0=x0, x100=x100, y0=y0, y35=y35)


def main():
    source = ROOT/'logic_core_new-native.svg'
    rows, axes = extract(source)
    with (ROOT/'allocation_trace_vector_vertices.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    report = dict(method='All native polyline vertices retained in order; independent tick calibration. No rounding to CPU integers, interpolation, invented samples, smoothing or new measurement.',
                  vertices=len(rows), source_svg_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  axes=axes, original_sampling_events_recovered=False)
    (ROOT/'allocation_trace_extraction.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
