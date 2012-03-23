import cherrypy
import db
import logging
import threading
import json

import settings

from omniverse import ArticleProducer

def init_setting(option, section = "NNTP"):
    try:
        return settings.get(section + ":" + option)
    except (settings.NoSectionError, settings.NoOptionError), error:
        return ""

class SettingsPages:

    @cherrypy.expose
    def index(self):

        groups = []
        while 1:
            try:
                (group, last) = settings.group_parse(settings.get("NNTP:server.0.group.%d" % len(groups)))
                groups.append(group)
            except settings.NoOptionError, e:
                break

        data= {
            "host" : init_setting("server.0.host"),
            "port" : init_setting("server.0.port"),
            "is_ssl" : "checked" if init_setting("server.0.is_ssl") else "",
            "username" : init_setting("server.0.username"),
            "password" : init_setting("server.0.password"),
            "groups" : groups
        }
        
        return load_template('./html/settings.tmp.htm').render(**data)

    @cherrypy.expose
    def save(self, **kwargs):
 
        host = kwargs['host']
        port = kwargs['port']
        is_ssl = 1 if kwargs['is_ssl'] == "checked" else 0
        username = kwargs['username']
        password = kwargs['password']
        groups = kwargs['groups'].split(',')

        print groups

        settings.set("NNTP:server.0.host", host)
        settings.set("NNTP:server.0.port", port)
        settings.set("NNTP:server.0.is_ssl", is_ssl)
        settings.set("NNTP:server.0.username", username)
        settings.set("NNTP:server.0.password", password)

        old_group_values = {}

        idx = 0
        while 1:
            try: 
                (group, last) = settings.group_parse(settings.get("NNTP:server.0.group.%d" % idx))
                settings.remove("NNTP:server.0.group.%d" % idx)
                
                old_group_values[group] = last
                idx += 1
            except settings.NoOptionError, e:
                break

        if len(groups) > 0:
            for idx, group in zip(range(len(groups)), groups):
                settings.set("NNTP:server.0.group.%d" % idx, "(%s:%s)" % (group, old_group_values.get(group, "0")))
        
        settings.set("NNTP:server.0.enabled", 1)
        return "success"

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

        cherrypy.response.headers['Content-type'] = 'application/json'

        try:
            worker = self._workers[uid]
            if worker.groups is not None:
                if len(worker.groups) > 0:
                    return json.dumps({'status' : 'done', 'result' : worker.groups})
                else:
                    return json.dumps({'status' : 'error', 'result' : 'No Newsgroups Found.'})

            return json.dumps({'status' : 'working', 'result' : '' })

        except KeyError, e:
            try:
                port = int(port)
            except ValueError, e:
                return json.dumps({'status' : 'error', 'result' : "Settings Error: Invalid Port."})

            try:
                conn = ArticleProducer().connect(host=host, port=port, is_ssl=is_ssl, username=username, password=password, retry=0)
            except IOError, e:
                return json.dumps({'status' : 'error', 'result' : 'Settings Error: %s' % str(e)})

            self._workers[uid] = GroupWorker(conn, keyword)
            self._workers[uid].start()
            return json.dumps({'status' : 'working', 'result' : '' })

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
