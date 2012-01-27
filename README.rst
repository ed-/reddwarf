=====================================================
Reddwarf (Database As A Service)
=====================================================

-------
Preface
-------

Reddwarf is extending the OpenStack Nova to enable Database as a
Service.

Get more information and get involved with the OpenStack community.

* http://openstack.org/
* http://openstack.org/community/
* https://github.com/openstack/nova

-----------------------------
Getting Started
-----------------------------

The information below is set to be run on a VM. Inside 'integration/vagrant' folder
is a file called 'VagrantFile'. This is a script that runs with Vagrant
(http://www.vagrantup.com) and VirtualBox (http://www.virtualbox.org/).
The versions we are currently using are Vagrant version 0.8.7 and
Virtualbox 4.1.4 r74291. Please be aware that virtualization must be
enabled in your systems BIOS to get virtualbox running with ANY 64b image.

#. Create a new Folder::

    [nix:~]$ mkdir ~/dev
    [nix:~]$ cd ~/dev

#. (Required) Download the openvz glance image from::

    [nix:~/dev]$ mkdir ~/dev/glanceimg
    [nix:~/dev]$ cd ~/dev/glanceimg
    [nix:~/dev/glanceimg]$ wget http://c629296.r96.cf2.rackcdn.com/debian-squeeze-x86_64-openvz.tar.gz

#. (Required) Define the environment variable for glance images (path to directory 
   of debian-squeeze-x86_64-openvz.tar.gz)::

    [nix:~/dev]$ export GLANCEIMAGES=~/dev/glanceimg

#. Fresh check out from github.::

    [nix:~/dev]$ git clone git://github.com/rackspace/reddwarf.git

#. Enter the directory reddwarf/integration/vagrant::

    [nix:~/dev]$ cd reddwarf/integration/vagrant

#. Startup the Virtual Machine with Vagrant::

    [nix:~/dev/reddwarf/integration/vagrant]$ vagrant up

Command Downloads the necessary files and starts up a Virtual Box image to
start using.

-----------
Quick Setup
-----------

#. On your host machine (i.e. do NOT use vagrant ssh to enter it)::

    [nix:~/dev/reddwarf/integration/vagrant]$ ./reddwarf-ci vagrantci

This automates all the manual steps below in order.

1. Install dependencies (install)
2. Build all the required packages (build)
3. Initialize Nova environemnt (initialize)
4. Run the CI tests (test)

---------------------
Manual Setup Reddwarf
---------------------

#. This will ssh in to the virtual machine::

    [nix:~/dev/reddwarf/integration/vagrant]$ vagrant ssh

#. This is the home location for vagrant and will have all the files in the integration/vagrant folder::

    [vagrant:~]$ cd /vagrant

#. This will be sure the environment has all the required dependencies installed::

    [vagrant:/vagrant]$ reddwarf-ci install

#. This will build all the packages required to start and run nova. This
   includes the guest-agent package that will run on the instances that listens
   for events from the API::

    [vagrant:/vagrant]$ reddwarf-ci build

#. This will clean and prepare the environment to start running nova as a
   clean setup. You can run this multiple times to refresh the environment::

    [vagrant:/vagrant]$ reddwarf-ci initialize

----------------
Testing Reddwarf
----------------

This is the integration tests for reddwarf that will run a plethora of tests
and be sure that everything is setup and working correctly.::

    [vagrant:/vagrant]$ reddwarf-ci test

You can run specific groups of tests by specifying the group paramter. The below example shows running just the volume tests::

    [vagrant:/vagrant]$ reddwarf-ci test --group=nova.volumes.driver

----------------------------------
Starting Up Reddwarf/Nova Manually
----------------------------------

#. Start all the services in a screen session::

    [vagrant:/vagrant]$ reddwarf-ci start

#. Stop all the screen services, also kills all the screen sessions::

    [vagrant:/vagrant]$ reddwarf-ci stop

----------------------------------------
Example Calls/Utilties for Reddwarf/Nova
----------------------------------------

#. Open up a new terminal and goto integration/vagrant::

    [nix:~]$ cd ~/dev/reddwarf/integration/vagrant

#. SSH into the vagrant box::

    [nix:~]$ vagrant ssh

#. Go to the source bin directory::

    [vagrant:~]$ cd /src/bin

#. Run reddwarf-cli::

    [vagrant:/src/bin]$ ./reddwarf-cli

#. Authenticate::

    [vagrant:/src/bin]$ ./reddwarf-cli auth login admin admin dbaas

#. Create an instance::

    [vagrant:/src/bin]$ ./reddwarf-cli create instance test 1 flavors/2
    [vagrant:/src/bin]$ ./reddwarf-cli list instances
    [vagrant:/src/bin]$ sudo vzlist
    [vagrant:/src/bin]$ sudo vzctl enter 1

#. Create database::

    [vagrant:/src/bin]$ ./reddwarf-cli create database 1 testdb

#. Create a user::

    [vagrant:/src/bin]$ ./reddwarf-cli create user 1 testuser testpass testdb

#. Login to mysql::

    [vagrant:/src/bin]$ mysql -u testuser -ptestpass -h <ipaddress>

