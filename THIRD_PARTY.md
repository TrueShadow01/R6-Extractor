# Third-party software

The standalone application bundles Python, Qt for Python (PySide6 and
Shiboken), Qt libraries, Pillow and NumPy, together with their dependencies.

These components retain their respective copyrights and licenses.
The project's GPLv3 license does not replace their license terms.

## License texts

- Python: `licenses/Python-LICENSE.txt`
- GNU GPL version 3: `licenses/GPL-3.0.txt`
- GNU LGPL version 3: `licenses/LGPL-3.0.txt`
- Pillow and NumPy: license files under their `_internal/*.dist-info` directories
- Additional component notices: retained alongside the bundled dependencies

## Qt and Qt for Python

This build uses Qt for Python 6.11.2 and Qt libraries distributed with it.
The libraries use open-source licensing; applicable terms vary by component.

Qt and PySide are dynamically loaded from `_internal`. Users may modify
or replace the libraries under their applicable licenses. Replacement
libraries must be binary-compatible with this build.

Upstream source and licensing information:

- Qt 6.11.2 source:
  https://download.qt.io/archive/qt/6.11/6.11.2/single/
- PySide/Shiboken 6.11.2 source:
  https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/
- Qt component licensing:
  https://doc.qt.io/qt-6/licensing.html
- Qt for Python third-party notices:
  https://doc.qt.io/qtforpython-6/licenses.html

Publish the matching application source and build specification with
the binary release. Retain access to the corresponding dependency sources.

## Excluded components

Rainbow Six Siege assets are not distributed with this application.