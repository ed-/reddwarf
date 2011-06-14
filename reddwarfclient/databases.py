from novaclient import base
from reddwarfclient.dbcontainers import DbContainer


class Database(base.Resource):
    """
    According to Wikipedia, "A database is a system intended to organize, store, and retrieve
    large amounts of data easily."
    """
    def __repr__(self):
        return "<Database: %s>" % self.name


class Databases(base.ManagerWithFind):
    """
    Manage :class:`Databases` resources.
    """
    resource_class = Database

    def create(self, dbcontainer_id, databases):
        """
        Create new databases within the specified container
        """
        body = {"databases": databases}
        url = "/dbcontainers/%s/databases" % dbcontainer_id
        resp, body = self.api.client.post(url, body=body)

    def delete(self, dbcontainer_id, dbname):
        """Delete an existing database in the specified instance"""
        url = "/dbcontainers/%s/databases/%s" % (dbcontainer_id, dbname)
        self._delete(url)

    def _list(self, url, response_key):
        resp, body = self.api.client.get(url)
        if not body:
            raise Exception("Call to " + url +
                            " did not return a body.")
        return [self.resource_class(self, res) for res in body[response_key]]

    def list(self, dbcontainer):
        """
        Get a list of all Databases from the dbcontainer.

        :rtype: list of :class:`Database`.
        """
        return self._list("/dbcontainers/%s/databases" % base.getid(dbcontainer),
                          "databases")

#    def get(self, dbcontainer, database):
#        """
#        Get a specific containers.
#
#        :param flavor: The ID of the :class:`Database` to get.
#        :rtype: :class:`Database`
#        """
#        assert isinstance(dbcontainer, DbContainer)
#        assert isinstance(database, (Database, int))
#        dbcontainer_id = base.getid(dbcontainer)
#        db_id = base.getid(database)
#        url = "/dbcontainers/%s/databases/%s" % (dbcontainer_id, db_id)
#        return self._get(url, "database")
