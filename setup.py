#!/usr/bin/env python
"""Package metadata for openedx_webhook_relay."""

import os
import re
import sys

from setuptools import find_packages, setup


def get_version(*file_paths):
    filename = os.path.join(os.path.dirname(__file__), *file_paths)
    version_file = open(filename, encoding="utf8").read()
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")


def load_requirements(path):
    with open(path, encoding="utf8") as handle:
        return [
            line.strip()
            for line in handle
            if line.strip() and not line.startswith("#") and not line.startswith("-")
        ]


VERSION = get_version("openedx_webhook_relay", "__init__.py")

if sys.argv[-1] == "tag":
    os.system(f"git tag -a v{VERSION} -m 'version {VERSION}'")
    os.system("git push --tags")
    sys.exit()

with open("README.rst", encoding="utf8") as readme:
    README = readme.read()

setup(
    name="openedx-webhook-relay",
    version=VERSION,
    description="Signed, audited, asynchronous webhook relay for Open edX events.",
    long_description=README,
    long_description_content_type="text/x-rst",
    author="Pearson / Credly",
    url="https://github.com/YourAcclaim/openedx-webhook-relay",
    packages=find_packages(exclude=["*tests*"]),
    include_package_data=True,
    install_requires=load_requirements("requirements/base.in"),
    python_requires=">=3.11",
    license="AGPL-3.0-or-later",
    zip_safe=False,
    keywords="Open edX webhooks Credly badges events celery security",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Framework :: Django",
        "Framework :: Django :: 4.2",
        "Framework :: Django :: 5.2",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    entry_points={
        "lms.djangoapp": [
            "openedx_webhook_relay = openedx_webhook_relay.apps:OpenedxWebhookRelayConfig",
        ],
    },
)
