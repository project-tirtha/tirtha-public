# Third-Party Notices

Tirtha is licensed under the GNU Affero General Public License v3.0 or later (see [LICENSE](./LICENSE)). This file lists third-party material that is **bundled in this repository** or that Tirtha **runs as an external tool** under a licence other than the AGPL. Dependencies installed by package managers (`uv.lock`, `frontend/package-lock.json`) carry their own licences and are not repeated here.

## Bundled in this repository

### Basis Universal transcoder

- Files: `frontend/public/basis/basis_transcoder.js`, `frontend/public/basis/basis_transcoder.wasm`
- Source: <https://github.com/BinomialLLC/basis_universal> (Binomial LLC)
- Licence: Apache License 2.0 (<https://www.apache.org/licenses/LICENSE-2.0>)
- `frontend/public/basis/ktx2-bootstrap.js` is Tirtha's own loader for it (AGPL-3.0-or-later).

### India state boundaries (`INDIA_STATES.geojson`)

- File: `frontend/public/India/INDIA_STATES.geojson`, simplified with `frontend/scripts/simplify_geojson.py`
- Its attributes (`STNAME_SH`, `STCODE11`) follow the Census of India 2011 state coding, which suggests a Census-2011-derived boundary dataset.
- **Provenance: source to be confirmed.** The upstream dataset and its licence have not yet been recorded. Until they are, do not assume this file can be reused under the AGPL or any other licence. Corrections are welcome via an issue.
- Boundaries are shown for orientation only and are not an authoritative depiction of borders.

### `copyExifNoOrient.js`

- File: `tirtha_bk/static/js/copyExifNoOrient.js`
- Adapted (2023) from code by Weihang Jian (© 2022), as its header states.
- **Upstream licence: to be confirmed.** The file is a legacy asset of the jQuery-era frontend.

## Git submodule

### VGGT (`tirtha_bk/nn_models/vggt`)

- Source: <https://github.com/facebookresearch/vggt> (Meta Platforms, Inc.)
- Licence: the **VGGT License** in that repository, which is not an open-source licence and carries its own acceptable-use terms. Model checkpoints have separate terms.
- The submodule is not checked out by default and is used only by the experimental VGGT path (`VGGT_ENABLE`, off by default). The AGPL does not apply to it. Read its licence before enabling it.

## External tools run by Tirtha (not distributed here)

### FastGS

- Source: <https://github.com/fastgs/FastGS>
- Licence: MIT for FastGS's own code. Its CUDA rasteriser (`diff-gaussian-rasterization`) and other parts derive from the Inria 3D Gaussian Splatting reference implementation and are under the **Inria/MPII Gaussian-Splatting License**, which permits **non-commercial research and evaluation use only** (<https://github.com/graphdeco-inria/gaussian-splatting/blob/main/LICENSE.md>).
- Tirtha does not include FastGS. It runs FastGS as a subprocess from a separate environment when `GS_BACKEND = "fastgs"`. Anyone deploying that backend must make sure their use complies with the non-commercial terms.

### Other external tools

COLMAP (BSD 3-Clause), splat-transform (MIT), AliceVision/Meshroom (MPL 2.0), nerfstudio (Apache 2.0), obj2gltf (Apache 2.0), gltfpack/meshoptimizer (MIT) and ffmpeg (LGPL/GPL, depending on the build) are installed separately and run as external programs. Each is under its own licence.
