# Native Pytorch experiment

The source under `source/campaign/` implements the recorded procedure. Configure `hosts.example.json` and the environment options in `source/campaign/runtime_config.py` for the reproduction hosts. Workload,resource and timing settings are listed in `../../../docs/CURRENT_ENVIRONMENT.md` and the local plan files.

## Prepare a new campaign

The shipped frozen files describe the measured original. Create a new Linux campaign root with the released runtime and workload source, build both binaries with Go1.26.2 and CGO, and freeze the new source, build and dependency hashes before any benchmark.

Set `RSEP_IMAGE` and `RSEP_IMAGE_DIGEST` to the locally built image imported into containerd and its manifest digest from `ctr images ls`.

The legacy tree must contain `deps/`, `source/workload/user_program.py`, `source/YOLOX-0.3.0/`, `downloads/yolox_nano.pth` and `workdir/assets/dog.jpg`.

```sh
export RSEP_NATIVE_EVIDENCE_ROOT=/srv/rsep/native-campaign
export RSEP_RUNTIME_SOURCE=/srv/rsep/native-campaign/runtime-source/Tide
export RSEP_NATIVE_LEGACY_ROOT=/srv/rsep/legacy
export RSEP_NATIVE_DEPENDENCY_ROOT=/srv/rsep/legacy/deps
python3 /opt/RSEP/tools/prepare_native_reproduction.py \
  --workdir /srv/rsep/native-campaign \
  --legacy-root /srv/rsep/legacy \
  --image "$RSEP_IMAGE" \
  --image-digest "$RSEP_IMAGE_DIGEST"
```

The command refuses an existing workdir and does not start an experiment. It writes the formal plan (180 runs,37800 offers), `frozen/identities.json`, `frozen/build-identity.json`, `frozen/dependency-files.json`, `frozen/image-identity.json` and `runtime-env.sh` under the campaign root. Image overrides use `RSEP_NATIVE_IMAGE` and `RSEP_NATIVE_IMAGE_DIGEST`.

Run `python tools/reproduce_current.py figures` from the repository root to redraw the included measurements. Runtime source is under `code/tide/`;the native PyTorch source variant is under `code/reproduction/runtime-issue42/Tide/`. Container and baseline instructions are in `../../../docs/REPRODUCTION.md`.

After preparation,source the generated `runtime-env.sh` before invoking a campaign command.
