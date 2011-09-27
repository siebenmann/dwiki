#
# Core WSGI handling for DWiki.
#
import sys, time

import httpcore

def errfp(env):
	return env['wsgi.errors']

# This has certain burned-in assumptions about logging.
class DWikiLogger:
	def __init__(self, environ, do_timestamp = False):
		self.env = environ
		self.do_ts = do_timestamp

	def msg(self, cls, msg):
		cfg = httpcore.get_cfg(self.env)
		efp = errfp(self.env)
		if self.do_ts:
			ts = time.strftime("%a %b %d %H:%M:%S %Y")
			pref = '[%s] [%s] [client %s] ' % \
			       (ts, cls, self.env.get("REMOTE_ADDR", "na?"))
		else:
			pref = ''
		efp.write("%s%s: dwiki %s: instance %s: %s\n" %
			  (pref, sys.argv[0], cls, cfg['wikiname'], msg))
		
	def warn(self, msg):
		self.msg('warning', msg)
	def error(self, msg):
		self.msg('error', msg)

def genWSGITop(next, cfg, ms, webserv, staticstore, cachestore,
	       logfactory = DWikiLogger):
	def wsgiFunc(environ, start_response):
		httpcore.environSetup(environ, cfg, ms, webserv, staticstore,
				      cachestore)
		environ['dwiki.logger'] = logfactory(environ,
						     'stamp-messages' in cfg)
		return next(environ, start_response)
	return wsgiFunc
