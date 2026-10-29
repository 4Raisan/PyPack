# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/4Raisan/PyPack/security/advisories/new)
rather than opening a public issue. Include the PyPack version (`PyPack.exe --version`
or `python pypack_setup.py --version`), your operating system, and the steps to reproduce.

## What PyPack does to your machine

PyPack is a front end to `pip`. Installing or upgrading packages runs arbitrary code
from those packages' build backends, with the permissions of the Python environment
you target. That is inherent to installing Python packages, not a defect in PyPack,
but it means you should only install packages you trust.

- PyPack never uses a shell. Package names are passed as separate `argv` entries after
  a `--` separator, and names beginning with `-` are rejected.
- PyPack refuses to modify an OS-managed Python (PEP 668 `EXTERNALLY-MANAGED`) and
  checks whether `site-packages` is writable before installing.
- Search results show 30-day download counts, and names outside the published
  top-download list are highlighted, to make typosquatting easier to spot. This is a
  hint, not a guarantee: always check the name before installing.
- Stopping an operation or closing the window kills the running `pip` process tree.

## Verifying a download

The Windows executable is **unsigned**, so Windows SmartScreen may warn about it.
Every release asset is covered by `SHA256SUMS.txt` and by a GitHub build-provenance
attestation, which proves the file was produced by this repository's release workflow:

```sh
sha256sum --check SHA256SUMS.txt
gh attestation verify PyPack.exe --repo 4Raisan/PyPack
```

The Windows Python-install helper verifies the Authenticode signature of the official
python.org installer and checks that the signer is the Python Software Foundation
before running it.

## Supported versions

Only the latest release is supported. Older releases remain downloadable but do not
receive fixes.