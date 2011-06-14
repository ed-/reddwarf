# Runs the host tests.
cd /src
# sudo find -name "*.pyc" -delete

# Won't be necessary soon as Proboscis moves to its own package...
# nosetests /src/integration/proboscis/proboscis_test.py --verbose
# nosetests /src/integration/tests/util/util_test.py --verbose

cd /tests
sudo -E NOVASRC=/src /tests/run_tests_nv.sh --conf=/tests/vagrant/host/host.nemesis.conf --group=host.ovz
