# Four-Case Articulation Benchmark

This directory contains the original Bell, Switch, Stapler, and Microwave reference images and manifest. Images have not been cropped, recompressed, or edited. The original prompts, SHA-256 hashes, dimensions, execution order, seed metadata, job limits, and joint acceptance criteria are preserved from the historical benchmark.

## Original Test Inputs

The executable prompts in `manifest.json` intentionally remain in Chinese. They are fixed test data, not untranslated documentation. Translating them would change the model inputs and invalidate an effort-only or backend comparison with the recorded runs. The English descriptions below are for readers; they are not substituted into the verified benchmark:

| Case | English description of the original request |
| --- | --- |
| Bell | Generate a bell based on the reference image. |
| Switch | Generate a switch based on the reference image. |
| Stapler | Generate a pressable stapler based on the reference image. |
| Microwave | Generate a drawer-style, pull-out microwave based on the reference image, with two push buttons and one rotary knob on the right. |

After following the repository-root installation and API startup instructions, run:

```bash
".venv/bin/python" "local.py" benchmark --backend api
".venv/bin/python" "local.py" benchmark --backend api --run --continue-on-failure
```

The first command only performs preflight checks. The second invokes a real model and submits each case once. Bounded network-request recovery is not HTTP job resubmission. Output is stored in `outputs/benchmarks/` and excluded from Git.

Acceptance checks download hashes, binary USDC, OpenUSD stage validity, joint counts/limits/body targets, authored mass and inertia, finite transforms, and self-contained resources. It does not establish visual semantics, dynamics, full-range collision safety, or manipulation-task success. The inference seed is not controlled, and one `4/4` run is not a stable success-rate guarantee.

## Clean-Clone Validation

On 2026-10-09, revision `dadbe5ac4f272c0503a62129d0bb37d2ff1581ac` was cloned from GitHub and installed into a fresh Conda environment and `.venv`. Installation, the offline USDC smoke check, and 70 tests passed. The four API cases then ran on a separate port. Images, SDK modules, and output all came from that clean clone, without borrowing the original workspace's `.venv` or baseline inputs. Real API credentials were supplied through an external user configuration and were not published.

Using OrcaRouter `openai/gpt-5.6-sol / low / Responses`, each case was submitted once. All four completed and passed independent static USDC acceptance, with no HTTP retries:

| Case | Duration | Final compile warnings |
| --- | --- | --- |
| Bell | 1 min 46 sec | 0 |
| Switch | 3 min 8 sec | 0 |
| Stapler | 4 min 34 sec | 1 |
| Microwave | 2 min 34 sec | 2 |

Total duration was approximately 12 minutes 3 seconds. Acceptance rules were not loosened to clear warnings. Dynamics and human semantic review were not performed. Billing was not calculated; `cost=0` does not mean free requests. See the [machine-readable validation summary](results/clean-clone-2026-10-09.json). It contains no key, host paths, or generated assets, only the tested revision, input/output hashes, and acceptance metrics. This records one experiment, not a success-rate promise for other accounts, machines, or future model versions.
