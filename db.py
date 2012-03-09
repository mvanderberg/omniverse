import sqlite3
import sys
import threading
import Queue
import logging

## local modules
import threads

DB_FILE = "db\\headers.db"
connection = None

def setup():
 
	global connection

	try:
		connection = sqlite3.connect(DB_FILE)
		connection.execute(
			'CREATE TABLE IF NOT EXISTS articles ' 
				'(subject TEXT PRIMARY KEY, '
				'parts TEXT, '
				'total_parts INT, '
				'complete TINYINT, '
				'filename TEXT, '
				'poster TEXT, '
				'date_posted INT, '
				'size INT, '
				'yenc TINYINT)')

		connection.execute(
			'CREATE INDEX IF NOT EXISTS filename_asc ON articles (filename ASC)')
		
	except sqlite3.Error, e:
		logging.getLoggger().severe("Database cannot be found or created. Error Message: %s. Cannot continue, quiting." % e)

		#TODO a more graceful exit.
		sys.exit(1)


class MultiThreadOK(threads.MyThread):
    def __init__(self, db):
        super(MultiThreadOK, self).__init__(name="Database Thread")
        self.db=db
        self.CANCEL = False
        self.reqs=Queue.Queue()
        self.start()
    def run(self):
        cnx = sqlite3.connect(self.db) 
        cnx.text_factory = sqlite3.OptimizedUnicode
        cursor = cnx.cursor()
        while not self.CANCEL:
            try:
                req, arg, res = self.reqs.get(timeout=10)
            except Queue.Empty:
                continue
                
            if req=='--close--': break
            if req=='--commit--':
            	cnx.commit()
            	continue
            cursor.execute(req, arg)
            if res:
                for rec in cursor:
                    res.put(rec)
                res.put('--no more--')
        cnx.close()
    def cancel(self):
        logging.getLogger().info("%s will finish canceling after processing approximately %d items." % (self.name, self.reqs.qsize()))
        self.CANCEL = True
    def execute(self, req, arg=None, res=None):
        self.reqs.put((req, arg or tuple(), res))
    def select(self, req, arg=None):
        res=Queue.Queue()
        self.execute(req, arg, res)
        while True:
            rec=res.get()
            if rec=='--no more--': break
            yield rec
    def close(self):
        self.execute('--close--')
    def commit(self):
    	self.execute('--commit--')

c = MultiThreadOK(DB_FILE)
def connect():
	return c