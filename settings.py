# Settings Manager

import ConfigParser
import os
import logging

# todo a little thread safety would help

_shared_settings = ConfigParser.RawConfigParser()


class NoSectionError(ConfigParser.NoSectionError):
	def __init__(self, *args, **kwargs):
		ConfigParser.NoSectionError.__init__(self, *args, **kwargs)

class NoOptionError(ConfigParser.NoOptionError):
	def __init__(self, *args, **kwargs):
		ConfigParser.NoOptionError.__init__(self, *args, **kwargs)
	
class SettingsError(ConfigParser.Error):
	def __init__(self, *args, **kwargs):
		ConfigParser.Error.__init__(self, *args, **kwargs)
	

# creates a path to a file resource relative to the current working directory
# todo refactor into new module
def build_path(*args):
	return os.path.join(os.path.abspath("."), *args)

def load(filename):
	_shared_settings.read(build_path(filename));

def save(filename):
	logging.getLogger().info("Saving settings.")
	try:
		fobj = open(build_path(filename), "wb")
		_shared_settings.write(fobj)
	except IOErrror, e:
		logging.getLogger().exception("Unable to save settings.")
	
def get(option):
	(section, option) = option.split(":")
	if _shared_settings.has_section(section):
		try:
			return _shared_settings.get(section=section, option=option)
		except ConfigParser.NoOptionError, error:
			raise NoOptionError(section, option)
	else:
		raise NoSectionError("No section named %s." % section)

def remove(option):
	(section, option) = option.split(":")
	_shared_settings.remove_option(section, option)

# parses the group name/last message id pairing
# ie, "(rec.anime:3100)" -> ("rec.anime" : "3100")
def group_parse(group_option):
	return tuple(group_option[1:-1].split(':'))

def set(option, value):
	(section, option) = option.split(":")

	try:
		_shared_settings.add_section(section)
	except ConfigParser.DuplicateSectionError:
		pass

	return _shared_settings.set(section, option, value)
