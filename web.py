import cherrypy
import db

class HelloWorld:

    def index(self, pg=1, sz=500, query=""):
        return self.browse(pg, sz)

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

    	html = ["<html><head></head><body>"]

    	connection = db.connect()

        html.append("<form action=\"browse\">Search: <input type=\"text\" name=\"query\" /> <input type=\"submit\" value=\"Go\" />")
        html.append("</form>")


    	html.append("<table><tr><td>Subject</td><td>Filename</td><td>Complete?</td></tr>")
    	
    	result_set = connection.select(
            "SELECT rowid, * FROM articles WHERE filename LIKE ? ORDER BY filename LIMIT ? OFFSET ?", 
                ("%" + query + "%", sz, (pg - 1) * sz))

        for result in result_set:
    		(rowid, subject, parts, total_parts, complete, filename, poster, date_posted, size, yenc) = result
    		html.append("<tr><td>%s</td><td>%s</td><td>%d</td>" % (subject, filename, complete))

    	html.append("</table>")


        for page in range(pg, pg + 11):
            html.append("<a href=\"?pg=%d&sz=%d&query=%s\">%d</a>&nbsp;" % (page, sz, query, page))

        html.append("</body></html>")



        return ''.join(html)

    index.exposed = True
    browse.exposed = True
    status.exposed = True


