import cherrypy
import db
import logging
import threading
import json
import cgi
import time

import settings

from omniverse import ArticleProducer

def get_setting(option, section = "NNTP"):
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
            except (settings.NoOptionError, settings.NoSectionError), e:
                break
        
        data= {
            "host" : get_setting("server.0.host"),
            "port" : get_setting("server.0.port"),
            "is_ssl" : "checked" if get_setting("server.0.is_ssl") == "1" else "",
            "username" : get_setting("server.0.username"),
            "password" : get_setting("server.0.password"),
            "groups" : groups
        }
        return load_template('./html/settings.tmp.htm').render(**data)

    @cherrypy.expose
    def save(self, **kwargs):
 
        host = kwargs['host']
        port = kwargs['port']
        is_ssl = "1" if kwargs['is_ssl'] == "checked" else "0"
        username = kwargs['username']
        password = kwargs['password']
        groups = kwargs['groups'].split(',')

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

        if groups[0]:
            for idx, group in zip(range(len(groups)), groups):
                settings.set("NNTP:server.0.group.%d" % idx, "(%s:%s)" % (group, old_group_values.get(group, "0")))
        
        settings.set("NNTP:server.0.enabled", 1)
        return "Settings Updated."

class GroupWorker(threading.Thread):

    def __init__(self, conn, keyword):
        threading.Thread.__init__(self)
        self.groups = None
        self.conn = conn
        self.keyword = keyword

    def run(self):
        (resp, groups) = self.conn.list()
        self.conn.quit()
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

class NzbPages:

    @cherrypy.expose
    def save(self, ids):

        # nzb spec can be found here: http://docs.newzbin2.es/index.php/Newzbin:NZB_Specs
        # refactor refactor refactor
        
        if type(ids) is not type([]):
            ids = [ids]

        nzb_filename = "omniverse_%d" % int(time.time())

        cherrypy.response.headers['Content-type'] = 'application/xml+nzb'
        cherrypy.response.headers['Content-disposition'] = 'attachment; filename="%s.nzb"' % nzb_filename
        connection = db.connect()

        pieces = []
        pieces.append('<?xml version="1.0" encoding="iso-8859-1" ?>')
        pieces.append('<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">')
        pieces.append('<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">')
        pieces.append('<head>')
        pieces.append('\t<meta type="title">%s</meta>' % nzb_filename)
        pieces.append('\t<meta type="tag">Comics</meta>')
        pieces.append('</head>')

        if ids:
            for _id in ids:
                (rowid, subject, parts, total_parts, complete, 
                    filename, groups, poster, date_posted, size, yenc) = connection.select(
                        "SELECT rowid, subject, parts, total_parts, complete, filename, groups, poster, date_posted, size, yenc from articles WHERE rowid = ?", (_id,)).next()
               
                pieces.append('<file poster="%s" date="%d" subject="%s">' % (
                    cgi.escape(poster, True), 
                    date_posted, 
                    cgi.escape(subject, True)))
                
                pieces.append('\t<groups>')
                for group in groups.split(','):
                    pieces.append('\t\t<group>%s</group>' % group.strip())
                pieces.append('\t</groups>')

                parts = eval(parts)
                
                pieces.append('\t<segments>')
                for part in parts.keys():
                    idx = parts[part]
                    pieces.append('\t\t<segment bytes="%d" number="%s">%s</segment>' % (
                        int(size) / int(total_parts),
                        idx,
                        cgi.escape(part[1:-1], True)))
                pieces.append('\t</segments>')


                pieces.append('</file>')
        pieces.append('</nzb>')

        output = "\n".join(pieces)

        try:
            import xml.parsers.expat

            parser = xml.parsers.expat.ParserCreate()
            parser.Parse(output)
        except Exception, e:
            logging.getLogger().exception("Failed to parse nzb data.")

        return output

class RootPages:

    # child pages
    groups = GroupPages()
    settings = SettingsPages()
    nzb = NzbPages()

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
