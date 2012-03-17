
## Python Standard Library
import nntplib
import datetime
import time, calendar
import cgi
import pickle
import logging
import logging.handlers
import sys
import itertools
import ssl
import ConfigParser
import re
import ssl
import os
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

## Configuration manager
## TODO: refactor this into a seperate module or class.
_config = None
def config():

	global _config

	if _config is None:
		_config = ConfigParser.SafeConfigParser()
		_config.read('config.ini')

		if not _config.has_section("NNTP"):
			_config.add_section("NNTP")
			_config.set("NNTP", "server.0.enabled", "1")
			_config.set("NNTP", "server.0.host", "none.nowhere.com")
			_config.set("NNTP", "server.0.port", "443")
			_config.set("NNTP", "server.0.username", "your_username")
			_config.set("NNTP", "server.0.password", "your_password")
			_config.set("NNTP", "server.0.ssl", "1")
			_config.set("NNTP", "server.0.group.0", "(alt.binaries.comics:0)")
			_config.set("NNTP", "server.0.group.1", "(alt.binaries.comics.reposts:0)")

			with open('config.ini', 'wb') as configfile:
			    _config.write(configfile)

			print "*** Update config.ini file with your information and re-execute omniverse."
			sys.exit(1)
	return _config
			
config()

def datetime_to_seconds(date):

	# lots of inconsistencies with the format of the date with each article.
	found = re.search(r"(?P<date>\d{1,2} \w\w\w \d\d\d\d \d{1,2}:\d\d:\d\d)", date).group(0)
	try:		
		date_obj = datetime.datetime.strptime(found, "%d %b %Y %H:%M:%S")
	except ValueError, e:
		logging.getLogger().error("Not able to parse date %s. Attempting alternate formats." % date)
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
		logging.getLogger().error("Unicode decode error while decoding %s. Error message: %s" % (repr(string_to_decode), str(e)))
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
				(subject, _from, message_id, date, lines) = [x[1] for x in self.parent.produce()]
				subject = subject.strip()
				if self.add_segment(subject, _from, message_id, date, lines):
					self.count += 1

				if self.count >= 2000:
					logging.getLogger().info("Worker: Committing 2000 records.")

					# TODO: remove. with the new queue for sql operations, this probably isn't necessary.
				
					try:
						self.connection.commit()
						self.count = 0
					except Exception, e: 
						logging.getLogger().info("Can't commit: %s" % str(e))
						self.count -= 500

			except StopIteration, e:
				if self.parent.isAlive():
					logging.getLogger().info("Article processor has nothing to do. Sleeping.")
					self.ev.wait(4)
				else:
					logging.getLogger().info("Finished processing articles. Quitting.")
					return

	def add_segment(self, subject, _from, message_id, date, lines):

		if parse.bad_filter(subject):
			logging.getLogger().info("Looks Spammy: %s" % subject)
			return False

		try:
			subject = utf_decode(subject)
			_from = utf_decode(_from)
			message_id = utf_decode(message_id)
		except UnicodeDecodeError, e:
			return False

		subject_similar = parse.subject_to_similar(subject)
		part_num, total_parts = parse.subject_to_totals(subject)
		filename = parse.subject_to_filename(subject)

		if not filename or not subject_similar or not total_parts:
			# segment is not part of a file -- do not add
			logging.getLogger().info("\"" + subject + "\" is either a plain usenet post or a subject line that cannot be parsed correctly.")

		else:
			result = self.connection.select("SELECT rowid, parts, total_parts FROM articles WHERE subject=? LIMIT 1", (subject_similar,) )
			
			try:
				(rowid, parts, total_parts) = result.next()

				if message_id in parts:
					return False
					
				parts = eval(parts)

				parts[ message_id ] = part_num

				complete = 1 if len(parts.keys()) == int(total_parts) else 0

				self.connection.execute("UPDATE articles SET parts=?, complete=? WHERE rowid=?", (str(parts), complete, rowid))
			
			except StopIteration, e:

				poster = _from
				parts = { message_id : part_num }

				try:
					date_posted = datetime_to_seconds(date)
				except ValueError, e:
					logging.getLogger().error("Unable to parse %s because bad timestamp. Skipping." % subject)
					return False

				size = parse.subject_to_size(subject) or (int(lines) * .85)
				yenc = 1 if parse.subject_to_yenc(subject) else 0
				complete = 1 if len(parts.keys()) == int(total_parts) else 0

				self.connection.execute("INSERT INTO articles " +
					"(subject, parts, total_parts, complete, filename, poster, date_posted, size, yenc) " + 
					"VALUES (?,?,?,?,?,?,?,?,?)", 
					(subject_similar, str(parts), total_parts, complete, filename, poster, date_posted, size, yenc))			
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

		while not self.cancel:
			if self.headers_size > int(self.CHUNK * 1.2):
				logging.getLogger().info(
					"Producer Thread has has too many articles (%d). Waiting for Worker to catch up." % self.headers_size)
				self.ev.wait(3)
			else:
				logging.getLogger().info("Producer Thread has %d articles waiting for processing." % self.headers_size)
				break


	def produce(self):
		obj = self.headers.next()
		self.headers_size -= 1
		return obj

	def connect(self, host, port, username, password, is_ssl = None, retry = 5):

		if retry == 0: return None

		try:
			logging.getLogger().info("Creating a new connection.")
			if is_ssl:
				self.connection = nntplib_ssl.NNTP_SSL(host, port, username, password)
				logging.getLogger().info("(%s:%s,SSL): %s." % (host, port, self.connection.getwelcome()[:20]) )
			else:
				self.connection = nntplib.NNTP(host, port, username, password)
				logging.getLogger().info("(%s:%s): %s..." % (host, port, self.connection.getwelcome()[:20]) )
			return self.connection

		except nntplib.NNTPPermanentError, msg:
			if msg.startswith("502"):
				logging.getLogger().error("Your per-user connection limit reached. Perhaps too many connections are being used by a program such as SABNZBD?")
			else:
				logging.getLogger().error("NNTPPermanentError: %s" % msg)
			self.ev.wait(60)
			logging.getLogger().info("Retrying connection.")
			return self.connect(host, port, username, password, is_ssl, retry - 1)
		
		except nntplib.NNTPTemporaryError, msg:
			logging.getLogger().error("NNTPTemporaryError: %s" % msg)
			self.ev.wait(60)
			logging.getLogger().info("Retrying connection.")
			return self.connect(host, port, username, password, is_ssl, retry - 1)
		
		except nntplib.NNTPError, msg:
			logging.getLogger().error("NNTP ERROR: %s" % msg)
			self.ev.wait(60)
			logging.getLogger().info("Retrying connection.")
			return self.connect(host, port, username, password, is_ssl, retry - 1)
		except nntplib.NNTPProtocolError, msg:
			logging.getLogger().error("NNTP Protocol Error: %s" % msg)
			self.ev.wait(60)
			logging.getLogger().info("Retrying connection.")
			return self.connect(host, port, username, password, is_ssl, retry - 1)
		except IOError, msg:
			logging.getLogger().error("Socket Error: %s" % msg)
			self.ev.wait(60)
			logging.getLogger().info("Retrying connection.")
			return self.connect(host, port, username, password, is_ssl, retry - 1)
		
	def run(self):

		# for the time being, there is only one server
		server_index = 0

		while not self.CANCEL:

			try:
				enabled = config().get("NNTP", "server.%d.enabled" % server_index) == "1"
				host = config().get("NNTP", "server.%d.host" % server_index)
				port = config().getint("NNTP", "server.%d.port" % server_index)
				username = config().get("NNTP", "server.%d.username" % server_index)
				password = config().get("NNTP", "server.%d.password" % server_index)
				is_ssl = config().get("NNTP", "server.%d.ssl" % server_index) == "1"
			except ConfigParser.NoOptionError, e:
				logging.getLogger().info("No more servers to process.")
				break
			except ConfigParser.NoSectionError, e:
				logging.getLogger().info("No server information setup. Use web interface to configure.")
				break

			group_index = 0

			while not self.CANCEL:

				self.check_workload()

				try:
					(group, low) = config().get("NNTP", "server.%d.group.%d" % (server_index, group_index))[1:-1].split(":")
				except ConfigParser.NoOptionError, e:
					logging.getLogger().info("No more groups to process for this server.")
					break

				self.connect(host, port, username, password, is_ssl)

				try:
					(response, count, first, last, name) = self.connection.group(group)
				except nntplib.NNTPTemporaryError, e:
					logging.getLogger().error("Failed to select group %s. Server response: %s" %
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
					logging.getLogger().error(
						"Failed to retrieve articles %d through %d from %s. Server response: %s" % 
						(low, high, group, str(e)))
					continue
				except nntplib.NNTPPermanentError, e:
					logging.getLogger().error(
						"Failed to retrieve articles %d through %d from %s. Server response: %s" % 
						(low, high, group, str(e)))
					continue
				except ssl.SSLError, e:
					logging.getLogger().error(
						"Failure in communication from %s. Error message: %s" % (host, str(e)))
					continue
				except IOError, e:
					logging.getLogger().error(
						"Failure in reading or writing from %s. Error message: %s" % (host, str(e)))
					continue
				
				# TODO this should be locked I'm sure...
				self.headers = itertools.chain(self.headers, 
													threads.LockedIterator(itertools.izip(subjects, froms, message_ids, dates, lines)))
				self.headers_size += len(subjects)

				config().set("NNTP", "server.%d.group.%d" % (server_index, group_index), "(%s:%d)" % (group, high + 1) )

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


class FileHandler:


	def to_nzb(self, files):

		if type(files) == type(""):
			files = [files]
	
		pieces = []
		pieces.append('<?xml version="1.0" encoding="iso-8859-1" ?>')
		pieces.append('<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">')
		pieces.append('<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">')
		pieces.append('<head>')
		pieces.append('\t<meta type="title">%s</meta>\n' % f[0].info["filename"])
		pieces.append('\t<meta type="tag">Comics</meta>')
		pieces.append('</head>')

		for f in files:
			pieces.append(f.to_nzb())

		pieces.append('</nzb>')

		return "\n".join(pieces)

	
		

	

class File:

	def __init__(self, _id, **kwargs):

		self.info = kwargs
		self.segments = {}


	def is_complete(self):
		return len(self.segments.keys()) == int(self.total_parts)

	def to_nzb(self):

		pieces = []

		pieces.append('<file poster="%s" date="%d" subject="%s">' % (
			self.info["from"], 
			self.info["date"], 
			cgi.escape(self.info["subject"], True)))

		pieces.append('\t<groups>')
		for group in self.info["newsgroups"].split(','):
			pieces.append('\t\t<group>%s</group>' % group.strip())
		pieces.append('\t</groups>')

		pieces.append('\t<segments>')

		parts = self.segments.keys()
		parts.sort()
		for part in parts:
			pieces.append('\t\t<segment="%d" number="%s">%s</segment>' % (
				int(self.info["size"]) / int(self.info["total_parts"]),
				part,
				self.segments[part]))
		pieces.append('\t</segments>')

		pieces.append('</file>')

		return "\n".join(pieces)

def startup():

	try:
		os.mkdir("db")
	except OSError, e:
		pass # directory exists

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

	with open("config.ini", "wb") as configfile:
		config().write(configfile)

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

	global SIGNAL

	startup()
	#handler = FileHandler()

	t = threading.Timer(5.0, start_article_download)
	t.setName("[Initiate Article Download Timer Thread]")
	t.start()
	
	cherrypy.engine.autoreload.unsubscribe()
	cherrypy.server.socket_port = 8085

	threading.Thread(group=None, 
		target=cherrypy.quickstart, 
		args = [web.HelloWorld()]).start()

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

try:

	try:
		os.mkdir('db')
	except OSError:
		pass #database directory already exists
	
	try:
		os.mkdir('log')
	except OSError:
		pass #log directory already exists

	logger = logging.getLogger()
	logger.setLevel(logging.DEBUG)
	
	# create file handler which logs even debug messages
	fh = logging.handlers.RotatingFileHandler('log\\omniverse.log', backupCount=5)
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