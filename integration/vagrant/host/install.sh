#!/bin/bash
# Builds packages.

source /vagrant-common/DbaasPkg.sh
source /vagrant-common/Utils.sh

exclaim "Deploying already built packages."
if [ -f ~/dependencies_are_installed ]
then
    dbaas_pkg_upload_release
    if [ $? -ne 0 ]; then exit 1; fi
else
    echo Dependencies are not installed.
    exit 1
fi
