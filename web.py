import cherrypy
import db

class HelloWorld:

    def index(self, pg=1, sz=500, query=""):
        return self.browse(pg, sz, query)

    def status(self):
        connection = db.connect()
        num_rows = connection.select("SELECT COUNT(*) as num_rows FROM articles WHERE filename LIKE '%cbr' OR filename LIKE '%cbz'").next()
        return "Number of records: %d" % num_rows

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

        from mako.template import Template
        from mako.lookup import TemplateLookup
    
        mytemplate = Template(
            filename='html/browse.tmp.htm',output_encoding='utf-8',encoding_errors='ignore', 
            lookup= TemplateLookup(
                directories=['html'],
                output_encoding='utf-8',
                input_encoding='utf-8', 
                encoding_errors='replace'))
        # mytemplate = Template(
        #     filename='html/browse.tmp.htm', lookup= TemplateLookup(directories=['html'],disable_unicode=True))

        connection = db.connect()

     	result_set = connection.select(
            "SELECT rowid, * FROM articles WHERE filename LIKE ? ORDER BY filename LIMIT ? OFFSET ?", 
                ("%" + query + "%", sz, (pg - 1) * sz))

        return mytemplate.render(result_set = result_set, pg = pg, sz = sz, query = query)

    index.exposed = True
    browse.exposed = True
    status.exposed = True


