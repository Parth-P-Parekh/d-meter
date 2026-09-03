# Staged Wood Build Bundle

Place only immutable copies of the approved production-parity inputs in this directory. Do not edit assets in place after recording their hashes.

Required files are declared in `bundle-manifest.json`; copy the provided example and replace every placeholder with the approved file name and SHA-256 checksum.

Expected asset roles:

- `post_surgery_onnx`: compiler-ready ONNX model.
- `calibration_directory`: representative production calibration images.
- `classes_file`: ordered labels; the required order is `ChipOff`, `Major`, `Minor`.
- `known_good_mpk`: package used to capture production baselines.
- `production_configuration`: generated configuration/resources used by that package.

Never place credentials, SSH keys, board addresses, or production logs containing sensitive data in this bundle.
