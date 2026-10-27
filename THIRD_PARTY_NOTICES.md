# External software

PDF Reducer uses Python and its standard library, browser APIs, and separately installed Ghostscript (`gs`) and MuPDF (`mutool`) executables. No copies of these runtimes, tools, libraries, fonts, or their source code are included in this repository.

| Component | Use | License information |
| --- | --- | --- |
| Python | Runtime and standard library | PSF License Version 2 and applicable component licenses: https://docs.python.org/3/license.html |
| Ghostscript | PDF rendering/compression via command-line calls | Installed Ubuntu package: AGPL-3.0-or-later for core code; commercial licensing also available: https://www.ghostscript.com/licensing/index.html |
| MuPDF / mutool | PDF inspection/merging via command-line calls | Installed Ubuntu package: AGPL-3.0-or-later for core code; commercial licensing also available: https://mupdf.readthedocs.io/en/latest/license.html |

Python and its standard library are maintained by the Python Software Foundation and other contributors. Copyright and license notices, including those of earlier Python copyright holders and bundled components, are documented at https://docs.python.org/3/license.html.

Ghostscript and MuPDF are copyright Artifex Software, Inc. and other contributors. Their licenses and notices remain applicable to their respective components. See https://artifex.com/licensing for upstream guidance and the copyright/license files supplied with installed packages for version-specific details and bundled third-party components.

This repository does not grant a commercial license to these tools. Bundling executables or dependencies in an installer or container requires a separate review of their source and notice obligations.

PDF Reducer adopts AGPL-3.0-or-later as a conservative licensing choice. Calling a separately installed command-line tool does not by itself establish that the calling application must use the same license; the actual integration and applicable terms matter. These notices are informational and do not provide a legal guarantee.
