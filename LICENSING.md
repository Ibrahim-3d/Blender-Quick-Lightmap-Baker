# Licensing — 2026-09-30

Current distributions of Quick Lightmap Baker use **GPL-3.0-or-later**.
The program grant is in `quick_lightmap_baker/LICENSE.txt`; the complete,
unmodified GPL version 3 text is in root `LICENSE` and in the installable
add-on's `quick_lightmap_baker/COPYING`.

The earlier root license at commit
`1a9b381d00e75ebdad34ad8cf017d23d67c3ab21` was an abbreviated permissive text
labelled MIT, rather than the standard complete MIT License. Its exact text
is retained in `quick_lightmap_baker/LICENSES/legacy-MIT-notice.txt`; do not
rewrite it as though different conditions had originally been granted.
Previously granted permissions are not revoked by this change.

The owner-authorized current GPL grant does not claim ownership over any
third-party code. Preserve upstream notices if material is incorporated.
It does not change licenses of the separate LightBaker repositories.

## Packaging

Distribute the full `quick_lightmap_baker` directory, including Python source,
`LICENSE.txt`, `COPYING`, and `LICENSES/`. Do not ship only the Python entry
file without its license materials. Source recipients retain the rights
provided by GPL even when an official download or support service is paid.

No existing tag, release archive, or application behavior was changed by
this license-hygiene commit.
