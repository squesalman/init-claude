# Vendored JavaScript

Pinned, committed, served by Django static files (ADR-0002: no node_modules, no CDN at runtime).
Each file is the unmodified `dist` file from the package's npm tarball; the tarball's `sha512`
was checked against the npm registry `dist.integrity`, and the same bytes are served by the
jsDelivr npm path below. `journal/test_import_templates.py` recomputes each sha256.
`.gitattributes` keeps these files byte-exact (no CRLF conversion on Windows checkouts).

To upgrade: download the new file from the URL pattern below, update the name, version, URL and
sha256 here, update the `<script>` tags in `templates/base.html`, and re-run the tests.

| File | Package | Version | Source URL | sha256 |
|---|---|---|---|---|
| htmx-2.0.11.min.js | htmx.org | 2.0.11 | https://cdn.jsdelivr.net/npm/htmx.org@2.0.11/dist/htmx.min.js (tarball: https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.11.tgz) | d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717 |
| alpine-csp-3.17.4.min.js | @alpinejs/csp | 3.17.4 | https://cdn.jsdelivr.net/npm/@alpinejs/csp@3.17.4/dist/cdn.min.js (tarball: https://registry.npmjs.org/@alpinejs/csp/-/csp-3.17.4.tgz) | 0d18d7f8d7910e2e0212f0f056b12f50bebc3abb7d88d2f7c7cb4c336fe4519a |

htmx 2.0.11 is npm `latest`; htmx 4.0.0 is published under the `next` tag and was not chosen.
