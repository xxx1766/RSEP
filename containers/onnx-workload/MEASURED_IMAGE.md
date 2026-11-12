# Recorded workload image

Reference name: `docker.io/tide/fig5-piei-20261010:amd64`.

Reference manifest: `sha256:defacfb3b2342c9a72fbdb6504844eccadb61a234647d33c34fc02b0019daa0a`.

The recorded base contains ONNX Runtime and an application-script layer. The native PyTorch campaign mounts its dependency and workload tree at `/legacy`. `append_measured_layer.py` implements the OCI layer assembly step. The Dockerfile builds from the released application sources and reports its own image identity.
