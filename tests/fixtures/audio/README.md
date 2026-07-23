# Authorized Stage 1 Audio Fixtures

Stage 1 audio tests generate their PCM samples in memory. No third-party audio
or proprietary recording is stored in this directory.

`fixture-manifest.json` records the cases represented by the generators in
`tests/test_media_stage1.py` and `tests/test_studio_stage1.py`. The generated
signals are original mathematical sine waves or deliberately invalid byte
sequences, contain no lyrics or human performance, and are covered by the
repository's MIT license.

The corpus is authorized for local tests, redistribution, modification, and CI.
Adding a recorded or externally sourced fixture requires its license and source
authorization to be documented here before the bytes enter the repository.
