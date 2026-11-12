from pathlib import Path
import hashlib
import json

FILES = ['historical_ablation_original_style_embedded.pdf', 'historical_allocation.pdf',
         'historical_wan.pdf', 'historical_cc_relative.pdf', 'historical_pi_relative.pdf',
         'historical_ei_relative.pdf', 'historical_latency_original.pdf',
         'historical_placement_original.pdf', 'historical_goodput_original.pdf']


def copy_fixed_assets(source, output):
    source, output = Path(source), Path(output)
    manifest = []
    for index, name in enumerate(FILES, 1):
        data = (source / name).read_bytes()
        (output / name).write_bytes(data)
        manifest.append({'figure': 'S' + str(index), 'file': name,
                         'method': 'original figure asset',
                         'sha256': hashlib.sha256(data).hexdigest()})
    (output / 'supplement_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
