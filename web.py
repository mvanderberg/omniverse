import cherrypy
import db
import logging
import threading

from omniverse import ArticleProducer

class SettingsPages:

    @cherrypy.expose
    def index(self, username=""):
        return load_template('./html/settings.tmp.htm').render()

    @cherrypy.expose
    def save(self, **kwargs):
       return str(repr(kwargs))

class GroupWorker(threading.Thread):

    def __init__(self, conn, keyword):
        threading.Thread.__init__(self)
        self.groups = None
        self.conn = conn
        self.keyword = keyword

    def run(self):
        (resp, groups) = self.conn.list()
        groups = [(group, int(last) - int(first)) for (group, last, first, flag) in groups if self.keyword in group]
        groups.sort(lambda x, y: y[1] - x[1])
        groups = ["%s (%d)" % (group, size) for (group, size) in groups]
        self.groups = groups

class GroupPages:

    def __init__(self):
        self._workers = {}

    @cherrypy.expose
    def list(self, host, port, is_ssl, username, password, keyword="comics"):

        uid = (host, port, is_ssl, username, password, keyword)

        try:
            worker = self._workers[uid]
            if worker.groups is not None:
                return "\n".join(worker.groups) or "No newsgroups found."
            return "working"

        except KeyError, e:
            try:
                port = int(port)
            except ValueError, e:
                raise cherrypy.HTTPError("500 Internal Server Error", "Invalid port.")

            conn = ArticleProducer().connect(host=host, port=port, is_ssl=is_ssl, username=username, password=password, retry=0)
            if conn is None:
                raise cherrypy.HTTPError("500 Internal Server Error", "Failed to connect to %s." % host)
            self._workers[uid] = GroupWorker(conn, keyword)
            self._workers[uid].start()
            return "working"

class RootPages:

    # child pages
    groups = GroupPages()
    settings = SettingsPages()

    @cherrypy.expose
    def index(self, pg=1, sz=500, query=""):
        return self.browse(pg, sz, query)

    @cherrypy.expose
    def status(self):
        connection = db.connect()
        num_rows = connection.select("SELECT COUNT(*) as num_rows FROM articles WHERE filename LIKE '%cbr' OR filename LIKE '%cbz'").next()
        return "Number of records: %d" % num_rows

    @cherrypy.expose
    def browse(self, pg=1, sz=500, query=""):

        try:
            # size per page
            sz = max(500, int(sz)) 
            
            #current page number
            pg = max(1, int(pg))
        except ValueError:
            logging.getLogger().error("Bad GET value.")
            sz = 500
            pg = 1

        connection = db.connect()

     	result_set = connection.select(
            "SELECT rowid, * FROM articles WHERE filename LIKE ? ORDER BY filename LIMIT ? OFFSET ?", 
                ("%" + query + "%", sz, (pg - 1) * sz))

        return load_template('./html/browse.tmp.htm').render(result_set = result_set, pg = pg, sz = sz, query = query)

def load_template(filename):

    from mako.template import Template
    from mako.lookup import TemplateLookup

    return Template(
            filename=filename,output_encoding='utf-8',encoding_errors='replace', 
            lookup= TemplateLookup(
                directories=['./html'],
                output_encoding='utf-8',
                input_encoding='utf-8', 
                encoding_errors='replace'))
