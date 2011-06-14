#!/bin/bash
# Installs an apt repo locally.

self="${0#./}"
base="${self%/*}"
current=`pwd`
if [ "$base" = "$self" ] ; then
    home=$current
elif [[ $base =~ ^/ ]]; then
    home="$base"
else
    home="$current/$base"
fi
cd $home

source Utils.sh

# Installs a local apt-repo.

#get proper sphinx version from the drizzle ppa
pkg_install python-software-properties
add-apt-repository ppa:drizzle-developers/ppa
add-apt-repository ppa:bzr/ppa
apt-get update

#install a bunch of libs
pkg_install git-core python-all python-setuptools python-sphinx python-distutils-extra pep8 debhelper apache2 dupload bzr devscripts
pkg_install reprepro


#install the apt repo from /var/www
# add distributions file to conf
mkdir -p /var/www/ubuntu/{conf,incoming}

echo 'Origin: Rackspace
Label: Rackspace
Codename: lucid
Architectures: i386 amd64
Components: main
Description: Rackspace DBaaS APT Repository' > /var/www/ubuntu/conf/distributions

# Add dupload stuff so we can upload if necessary
echo '$cfg{"nova"} = {
    fqdn => "10.127.2.126",
    login => "apt-upload",
    method => "scpb",
    incoming => "/home/apt-upload/incoming_packages",
    dinstall_runs => 1,
};
' >> /etc/dupload.conf
