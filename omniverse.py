## Python Standard Library
import nntplib
import datetime
import time, calendar
import cgi
import pickle
import logging
import logging.handlers
import sys
import getopt
import itertools
import ssl
import re
import ssl
import os
import os.path
import threading

## installed packages
import cherrypy
import mako

## local modules
import nntplib_ssl
import db
import parse
import web
import threads
import settings


## File manipulation functions
## Refactor to additional module

# create a directory relative to the current working directory.
# if a directory exists returns False
# if a directory doesn't exist, creates it and returns True
def create_child_dir(dirname):
	try:
		os.mkdir(build_path(dirname))
		return True
	except OSError:
		return False

# creates a path to a file resource relative to the current working directory
def build_path(*args):
	return os.path.join(os.path.abspath("."), *args)

def datetime_to_seconds(date):

	# lots of inconsistencies with the format of the date with each article.
	found = re.search(r"(?P<date>\d{1,2} \w\w\w \d\d\d\d \d{1,2}:\d\d:\d\d)", date).group(0)
	try:		
		date_obj = datetime.datetime.strptime(found, "%d %b %Y %H:%M:%S")
	except ValueError, e:
		logging.getLogger().exception("Not able to parse date %s. Attempting alternate formats." % date)
		try:
			date_obj = datetime.datetime.strptime(date, "%a, %d %b %Y %H:%M:%S %Z")
		except ValueError, e:
			date_obj = datetime.datetime.strptime(date, "%d %b %Y %H:%M:%S %Z")	
			
	time_tuple = date_obj.timetuple()
	return calendar.timegm(time_tuple)

def utf_decode(string_to_decode):

	try:
		return string_to_decode.decode('utf-8', 'replace')
	except UnicodeDecodeError, e:
		logging.getLogger().exception("Unicode decode error while decoding %s. Error message: %s" % (repr(string_to_decode), str(e)))
		raise e	

class ArticleWorker(threads.MyThread):

	def __init__(self, parent):
		threading.Thread.__init__(self)
		self.name = "ArticleWorker-%d" % hash(self)
		self.ev = threading.Event()
		self.parent = parent
		self.connection = None
		self.cursor = None
		self.count = 0
		self.CANCEL = False

	def cancel(self):
		self.CANCEL = True

	def run(self):
		
		self.connection = db.connect()

		while not self.CANCEL:
			try:
				# each variable is a tuple containing the article number and the value. The list
				# compression filters out the article numbers.
				(subject, _from, message_id, date, lines, group) = self.parent.produce()
				subject = subject[1].strip()
				_from = _from[1]
				message_id = message_id[1]
				date = date[1]
				lines = lines[1]

				try:
					if self.add_segment(subject, _from, message_id, date, lines, group):
						self.count += 1
				except Exception, e:
					logging.getLogger().exception("General failure while processing message. Subject: %s, From: %s, Message ID: %s, Date: %s, Lines: %s, Group: %s" % (subject, _from, message_id, date, lines, group))
					
				if self.count >= 2000:
					logging.getLogger().info("Worker: Committing 2000 records.")

					# TODO: remove. with the new queue for sql operations, this probably isn't necessary.
				
					try:
						self.connection.commit()
						self.count = 0
					except Exception, e: 
						logging.getLogger().exception("Can't commit: %s. Will retry." % str(e))
						self.count -= 500

			except StopIteration, e:
				if self.parent.isAlive():
					logging.getLogger().info("Article processor has nothing to do. Sleeping.")
					self.ev.wait(4)
				else:
					logging.getLogger().info("Finished processing articles. Quitting.")
					return

	def add_segment(self, subject, _from, message_id, date, lines, group):

		if parse.bad_filter(subject):
			logging.getLogger().info("Looks Spammy: %s" % subject)
			return False

		try:
			subject = utf_decode(subject)
			_from = utf_decode(_from)
			message_id = utf_decode(message_id)
			group = utf_decode(group)
		except UnicodeDecodeError, e:
			logging.getLogger().exception("UnicodeDecodeError while trying to decode message_id %s" % message_id)
			return False

		subject_similar = parse.subject_to_similar(subject)
		part_num, total_parts = parse.subject_to_totals(subject)
		filename = parse.subject_to_filename(subject)

		if not filename or not subject_similar or not total_parts:
			logging.getLogger().info("\"" + subject + "\" is either a plain usenet post or a subject line that cannot be parsed correctly.")
			return False

		yenc = 1 if parse.subject_to_yenc(subject) else 0
		# UUENCODE: 45 bytes per line
		# yEnc: 128 bytes per line
		try:
			if yenc == 1:
				size = int(lines) * 128
			else:
				size = int(lines) * 45
		except ValueError:
			#occasionally the line info returned from the server will be no good
			size = 0

		result = self.connection.select("SELECT rowid, parts, total_parts, groups FROM articles WHERE subject=? LIMIT 1", (subject_similar,) )
		
		try:
			(rowid, parts, total_parts, groups) = result.next()

			groups = groups.split(',')
			
			if group not in groups:
				groups.append(group)
				self.connection.execute("UPDATE articles SET groups=? WHERE rowid=?", (','.join(groups), rowid))

			if message_id in parts:
				return False
				
			parts = eval(parts)

			parts[ message_id ] = part_num

			complete = 1 if len(parts.keys()) == int(total_parts) else 0

			self.connection.execute("UPDATE articles SET parts=?, complete=?, size=size+? WHERE rowid=?", (str(parts), complete, size, rowid))
		
		except StopIteration, e:

			poster = _from
			parts = { message_id : part_num }

			try:
				date_posted = datetime_to_seconds(date)
			except ValueError, e:
				logging.getLogger().exception("Unable to parse %s because bad timestamp. Skipping." % subject)
				return False

			#size = parse.subject_to_size(subject) or (int(lines) * .85)
			
			complete = 1 if len(parts.keys()) == int(total_parts) else 0

			self.connection.execute("INSERT INTO articles " +
				"(subject, parts, total_parts, complete, filename, groups, poster, date_posted, size, yenc) " + 
				"VALUES (?,?,?,?,?,?,?,?,?,?)", 
				(subject_similar, str(parts), total_parts, complete, filename, group, poster, date_posted, size, yenc))			
		return True



class ArticleProducer(threads.MyThread):

	def __init__(self):
		threading.Thread.__init__(self)
		self.name = "ArticleProducer-%d" % hash(self)
		self.ev = threading.Event()
		self.headers = threads.LockedIterator([].__iter__())
		self.headers_size = 0
		self.connection = None
		self.CANCEL = False

		# number of articles to download from newsserver in one pass.
		self.CHUNK = 20000

	def cancel(self):
		self.CANCEL = True
	
	def wake(self):
		if self.isAlive():
			self.ev.set()

	# checks to see if there are too many articles still to process by the worker thread. If there is, 
	# waits for a few seconds.
	def check_workload(self):

		logging.getLogger().info("Checking workload. %d" % self.headers_size)
		while not self.CANCEL:
			if self.headers_size > int(self.CHUNK * 1.2):
				#logging.getLogger().info(
				#	"Producer Thread has has too many articles (%d). Waiting for Worker to catch up." % self.headers_size)
				self.ev.wait(3)
			else:
				#logging.getLogger().info("Producer Thread has %d articles waiting for processing." % self.headers_size)
				break


	def produce(self):
		obj = self.headers.next()
		self.headers_size -= 1
		return obj

	def connect(self, host, port, username, password, is_ssl = None, retry = 5):

		if retry < 0: return None

		try:
			logging.getLogger().info("Creating a new connection.")
			if is_ssl:
				self.connection = nntplib_ssl.NNTP_SSL(host, port, username, password)
				logging.getLogger().info("(%s:%s,SSL): %s." % (host, port, self.connection.getwelcome()[:20]) )
			else:
				self.connection = nntplib.NNTP(host, port, username, password)
				logging.getLogger().info("(%s:%s): %s..." % (host, port, self.connection.getwelcome()[:20]) )
			return self.connection
		
		except (nntplib.NNTPPermanentError, 
				nntplib.NNTPTemporaryError,
				nntplib.NNTPError,
				nntplib.NNTPProtocolError), error:

			
			logging.getLogger().exception("NNTPError. Server response: %s" % error.message)

			if retry == 0:
				raise IOError(error)
			else:
				self.ev.wait(60)
				logging.getLogger().info("Retrying connection.")
				return self.connect(host, port, username, password, is_ssl, retry - 1)
			
		except IOError, error:
			if retry == 0:
				raise error
			else:
				logging.getLogger().exception("Socket Error: %s" % str(error))
				self.ev.wait(60)
				logging.getLogger().info("Retrying connection.")
				return self.connect(host, port, username, password, is_ssl, retry - 1)
		
	def run(self):

		# for the time being, there is only one server
		server_index = 0

		while not self.CANCEL:

			try:
				enabled =  settings.get("NNTP:server.%d.enabled" % server_index) == "1"
				host =     settings.get("NNTP:server.%d.host" % server_index)
				port = int(settings.get("NNTP:server.%d.port" % server_index))
				username = settings.get("NNTP:server.%d.username" % server_index)
				password = settings.get("NNTP:server.%d.password" % server_index)
				is_ssl =   settings.get("NNTP:server.%d.is_ssl" % server_index) == "1"
			except (settings.NoOptionError, settings.NoSectionError), e:
				logging.getLogger().info("No more servers to process.")
				if server_index == 0:
					logging.getLogger().error("No server information configured. Use web interface to configure.")
				break
			except ValueError, e:
				logging.getLogger().exception("Invalid port value.")

			group_index = 0

			while not self.CANCEL:

				self.check_workload()

				try:
					(group, low) = settings.get("NNTP:server.%d.group.%d" % (server_index, group_index))[1:-1].split(":")
				except settings.NoOptionError, e:
					logging.getLogger().info("No more groups to process for this server.")
					break

				self.connect(host, port, username, password, is_ssl)

				try:
					(response, count, first, last, name) = self.connection.group(group)
				except nntplib.NNTPTemporaryError, e:
					logging.getLogger().exception("Failed to select group %s. Server response: %s" %
						(group, str(e)))
					logging.getLogger().info("Skipping %s." % group)
					group_index += 1
					continue

				logging.getLogger().info('Selecting group %s' % group)
				logging.getLogger().info(name + ' has ' + count + ' articles, ' + first + ' - ' + last)

				low = int(low)
				first = int(first)
				last = int(last)

				low = max(low, first)
				high = min(low+self.CHUNK, last)

				try:
					(response, subjects) = self.connection.xhdr('subject', '%d-%d' % (low, high) )
					(response, froms) = self.connection.xhdr('from', '%d-%d' % (low, high) )
					(response, message_ids) = self.connection.xhdr('message-id', '%d-%d' % (low, high) )
					(response, dates) = self.connection.xhdr('date', '%d-%d' % (low, high) )
					(response, lines) = self.connection.xhdr('lines', '%d-%d' % (low, high) )

					logging.debug("[%s: %09d - %09d (of: %09d), %s]" % (group, low, high, last, response))

				except nntplib.NNTPTemporaryError, e:
					logging.getLogger().exception(
						"Failed to retrieve articles %d through %d from %s. Server response: %s" % 
						(low, high, group, str(e)))
					continue
				except nntplib.NNTPPermanentError, e:
					logging.getLogger().exception(
						"Failed to retrieve articles %d through %d from %s. Server response: %s" % 
						(low, high, group, str(e)))
					continue
				except ssl.SSLError, e:
					logging.getLogger().exception(
						"Failure in communication from %s. Error message: %s" % (host, str(e)))
					continue
				except IOError, e:
					logging.getLogger().exception(
						"Failure in reading or writing from %s. Error message: %s" % (host, str(e)))
					continue
				
				# TODO this should be locked I'm sure...
				self.headers = itertools.chain(self.headers, 
													threads.LockedIterator(itertools.izip(subjects, froms, message_ids, dates, lines, [group] * len(subjects))))
				self.headers_size += len(subjects)
				settings.set("NNTP:server.%d.group.%d" % (server_index, group_index), "(%s:%d)" % (group, high + 1) )

				if high + 1 >= last:
					logging.getLogger().info("%s is up to date." % group)
					group_index += 1
					
				self.connection.quit()

			server_index += 1

		if not self.CANCEL:
			logging.getLogger().info("Finished with downloading of headers. Will Recheck in 5 hours.")
			t = threading.Timer(60 * 60 * 5, start_article_download)
			t.start()
		else:
			logging.getLogger().info("ArticleProducer quitting.")

def startup():

	settings.load('settings.ini')

	db.setup()

	logging.getLogger().info("Checking database integrity.")
	#db.connection.execute("VACUUM")
	logging.getLogger().info("Integrity check complete. Continuing.")

def shutdown():

	# cleanup and shutdown code
	logging.getLogger().info("Shutting Down. Signalling other worker threads.")
	for thread in threading.enumerate():
		if thread == threading.current_thread():
			continue
		else:
			try:
				logging.getLogger().info("Canceling thread %s." % thread.getName())
				thread.cancel()
			except AttributeError, e:
				#logging.getLogger().info("%s respone: %s" % (thread.getName(), str(e)))
				pass

	cherrypy.engine.exit()

	settings.save('settings.ini')

def start_article_download():

	for thread in threading.enumerate():
		if thread is ArticleWorker and thread.isAlive() or thread is ArticleProducer and thread.isAlive():
			logging.getLogger().info("ArticleProducer/Worker still running. Won't start another.")
			return

	producer = ArticleProducer()
	producer.start()

	worker = ArticleWorker(producer)
	worker.start()

def main():

	optlist, args = getopt.getopt(sys.argv[1:], '', ["host=", "port="])

	global SIGNAL

	startup()
	#handler = FileHandler()

	t = threading.Timer(5.0, start_article_download)
	t.setName("[Initiate Article Download Timer Thread]")
	t.start()
	
	config = {'/media':
				{'tools.staticdir.on': True,
				 'tools.staticdir.dir': build_path("html", "media"),
				}
			 }

	cherrypy.engine.autoreload.unsubscribe()

	# default
	cherrypy.server.socket_port = 8085
	for option, value in optlist:
		if option == "--host":
			cherrypy.server.socket_host = value
		elif option == "--port":
			try:
				cherrypy.server.socket_port = int(value)
			except ValueError, e:
				logging.getLogger().exception("Invalid port number %s. Defaulting to 8085" % port)
				cherrypy.server.socket_port = 8085
		else:
			print "Unknown options. Available options: --host=[ip address to bind interface to] --port=[port for interface to listen to]"

	cherrypy.tree.mount(web.RootPages(), '/', config=config)
	cherrypy.engine.start()

	try:
		while True:
			if not SIGNAL:
				time.sleep(1)
			else: 
				logging.getLogger().info('Received signal: ' + SIGNAL)
				# if SIGNAL == 'shutdown'
				# shutdown()
				# if SIGNAL == 'restart'
				# restart()
	except KeyboardInterrupt, e:
		SIGNAL = 'shutdown'
		shutdown()

	logging.getLogger().info("Main thread finished.")

SIGNAL = None

if __name__ == "__main__":
	try:

		create_child_dir('db')
		create_child_dir('log')

		logger = logging.getLogger()
		logger.setLevel(logging.DEBUG)
		
		# create file handler which logs even debug messages
		fh = logging.handlers.RotatingFileHandler(build_path('log', 'omniverse.log'), backupCount=5)
		fh.setLevel(logging.DEBUG)
		
		ch = logging.StreamHandler()
		
		formatter = logging.Formatter('(%(levelname)s): %(message)s')
		ch.setFormatter(formatter)
		
		formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')	
		fh.setFormatter(formatter)

		# add the handlers to logger
		logger.addHandler(ch)
		logger.addHandler(fh)
		logger.debug("Starting...")
		main()
	except SystemExit, e:	
		logging.getLogger().info("Shutting Down")
	
 