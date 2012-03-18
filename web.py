import cherrypy
import db

class GroupPages:

    @cherrypy.expose
    def list(self, host, port, is_ssl, username, password, keyword="comics"):

        conn = ArticleProducer().connect(host, port, is_ssl, username, password)
        if conn is None:
            return "Error: Failed to connect."
        
        groups = ["\t<group>" + group + "</group>\n" for group in conn.list() if keyword in group]

        return "<groups>\n" + "".join(groups) + "</groups>"

class RootPages:

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

    @cherrypy.expose
    def settings(self):
        pass

    groups = GroupPages()
        
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
