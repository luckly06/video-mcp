# Jules review rules

## Project boundaries

- Treat `CRVideoMate/` as a read-only archive. Changes under this directory are blocking unless the pull request explicitly documents why the archive must change.
- The application may access input videos only under `station/assets/`, and generated videos must remain under the repository-level `output/` directory.
- Reject path traversal or path-resolution changes that can escape the allowed input and output directories.
- `delete_output` must remain blocked from direct agent execution and require explicit confirmation through the web interface.

## Pipeline behavior

- `probe_video` must complete successfully before `dedup_video` or `batch_fission` runs.
- Video processing must preserve the existing validation guarantees: changed MD5, unchanged resolution, and sufficiently close duration.
- Do not introduce a dependency on the archived `CRVideoMate.exe`; processing must continue through the bundled ffmpeg implementation.

## Tests

- Changes to pipeline orchestration, path validation, hook policy, job state, or MCP request handling require focused automated tests.
- Tests must not depend on external network access, desktop GUI state, or files outside the repository fixture directories.
