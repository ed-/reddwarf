#!/bin/bash
# Build the debian package for the Nova packages.

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

pkg_install python-m2crypto python-twisted-web python-mox python-carrot python-boto python-amqplib python-ipy python-routes python-webob python-tempita python-migrate python-glance


#prepare the build dir
rm -rf /tmp/build
mkdir /tmp/build
cd /tmp/build
cp -R /src /tmp/build/dbaas
rm -rf /tmp/build/dbaas/.bzr
http_proxy=$http_proxy https_proxy=$https_proxy bzr checkout --lightweight lp:~openstack-ubuntu-packagers/ubuntu/natty/nova/ubuntu dbaas
cd dbaas

#get the git rev # to put in for the revision
export gitversion=`/usr/bin/git show|head -n 1|cut -d' ' -f2`''
echo "$gitversion" > /tmp/build/dbaas/_version.txt

# add the packages to the control file to make sure we also build our packages
# Build package section, adding in the platform api and nova guest!
echo 'Package: reddwarf-api
Architecture: all
Depends: nova-common (= ${binary:Version}), ${python:Depends}, ${misc:Depends}
Description: Red Dwarf - Nova - API frontend' >> debian/control
echo '' >> debian/control
echo 'Package: nova-guest
Architecture: all
Depends: nova-common (= ${binary:Version}), ${python:Depends}, ${misc:Depends}
Description: Red Dwarf - Nova - Guest agent' >> debian/control
echo '' >> debian/control
echo 'Package: nova-dns
Architecture: all
Depends: nova-common (= ${binary:Version}), ${python:Depends}, ${misc:Depends}
Description: Red Dwarf - Nova - DNS' >> debian/control

echo "nova ($gitversion) lucid; urgency=low

  [aut-gen]
  * generated version from the integration build.

 -- Apt Repo <dbaas-dev@rackspace.com>  `date +'%a, %d %b %Y %I:%M:%S %z'`

" | cat - debian/changelog >> debian/changelog.tmp
mv debian/changelog.tmp debian/changelog

#change the packages to lucid packages in the existing bzr checked out code
sed -i.bak -e 's/ natty;/ lucid;/g' debian/changelog

#now hot-mod the guest file and platform api based on the existing stuff
for file in `ls debian/ |grep nova-api`
do
   cp debian/$file "debian/nova-guest."`echo $file|cut -d'.' -f2`
   cp debian/$file "debian/reddwarf-api."`echo $file|cut -d'.' -f2`
   cp debian/$file "debian/nova-dns."`echo $file|cut -d'.' -f2`
done
sed -i.bak -e 's/nova-api/nova-guest/g' debian/nova-guest.*
sed -i.bak -e 's/nova-api/reddwarf-api/g' debian/reddwarf-api.*
sed -i.bak -e 's/nova-api/nova-dns/g' debian/nova-dns.*

#Fix the api paste config
sed -i.bak -e 's/api-paste\.ini/reddwarf-api-paste\.ini/g' debian/reddwarf-api.install
echo 'usr/bin/reddwarf-cli' >> debian/reddwarf-api.install
echo 'usr/bin/reddwarf-manage' >> debian/reddwarf-api.install

#hack up the rules file thats broken
echo '--sql_connection=mysql://nova:novapass@10.0.2.15/nova' >> debian/nova.conf
sed -i.bak 's/mkdir -p doc\/build\/html/mkdir -p doc\/doc\/build\/html/g' debian/rules

#now build the sucker
DEB_BUILD_OPTIONS=nocheck,nodocs dpkg-buildpackage -rfakeroot -b -uc -us
