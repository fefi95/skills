# Vendored files

## mermaid.min.js

- Package: `mermaid` 11.4.1 (MIT), from the npm registry
- How it was fetched: `npm pack mermaid@11.4.1`, then `package/dist/mermaid.min.js`
- SHA-256: a43bc1afd446f9c4cc66ac5dd45d02e8d65e26fc5344ec0ef787f88d6ddb6f9e
- `mermaid.LICENSE` is the license file from that package, unchanged.
- The bundle also contains other open-source libraries (for example d3, dagre, DOMPurify, khroma).
  Their notices are inside the bundle's header comments, and each library keeps its own license.
  See https://github.com/mermaid-js/mermaid for the full list.

To upgrade: fetch the new version the same way, replace the file, update the version and hash here,
and update `MERMAID_SHA256` in `tests/plan-board/test_packaging.py`. Re-run `make test`.
