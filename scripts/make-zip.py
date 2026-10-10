"""Builds the installable plugin ZIP.

    python scripts/make-zip.py

Writes ``dist/graphexplorer_link-<version>.zip``. Inside is exactly one folder,
``graphexplorer_link/`` -- QGIS installs the plugin under that name, and
plugins.qgis.org expects a single top-level folder. The repository name does
not matter for that, which is why the folder name is fixed here.

Only what QGIS needs goes in: the Python modules, metadata, icon, licence and
README. Tests, scripts and anything Git ignores stay out.
"""

import configparser
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "graphexplorer_link"
EXTRA = ("metadata.txt", "icon.png", "icon.svg", "LICENSE", "README.md")


def version():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(os.path.join(ROOT, "metadata.txt"), encoding="utf-8")
    return parser.get("general", "version")


def files():
    names = sorted(n for n in os.listdir(ROOT) if n.endswith(".py"))
    return names + [n for n in EXTRA if os.path.exists(os.path.join(ROOT, n))]


def main():
    target_dir = os.path.join(ROOT, "dist")
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, "%s-%s.zip" % (PACKAGE, version()))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in files():
            zf.write(os.path.join(ROOT, name), "%s/%s" % (PACKAGE, name))
    print(target)


if __name__ == "__main__":
    main()
